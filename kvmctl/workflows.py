"""Immutable, declarative named KVM workflow definitions."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .sequences import SequencePlan, canonicalize_plan, plan_hash, validate_plan


class WorkflowError(ValueError):
    """Raised when a workflow definition cannot be validated or resolved."""


_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_INDEPENDENT_TARGET = "__target_independent__"
_ALLOWED = {"name", "target", "target_independent", "max_duration_ms", "unexpected_screen_policy", "steps", "revision"}


def _fail(message: str) -> WorkflowError:
    return WorkflowError(message)


@dataclass(frozen=True)
class WorkflowDefinition:
    name: str
    plan: SequencePlan
    target_independent: bool = False
    revision: str = field(init=False)
    resolved_target: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME.fullmatch(self.name):
            raise _fail("invalid workflow name")
        try:
            validate_plan(self.plan)
        except (TypeError, ValueError, KeyError) as exc:
            raise _fail("invalid workflow plan") from exc
        if not isinstance(self.target_independent, bool):
            raise _fail("target_independent must be boolean")
        if self.target_independent and self.resolved_target is None and self.plan.target != _INDEPENDENT_TARGET:
            raise _fail("target-independent workflow must use an unbound plan")
        object.__setattr__(self, "revision", self._derived_revision())

    def _derived_revision(self) -> str:
        canonical_plan = canonicalize_plan(self.plan)
        if self.target_independent:
            canonical_plan["target"] = None
        revision_payload = {
            "name": self.name,
            "target": None if self.target_independent else self.plan.target,
            "target_independent": self.target_independent,
            "plan": canonical_plan,
        }
        payload = json.dumps(revision_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "WorkflowDefinition":
        if not isinstance(raw, Mapping):
            raise _fail("workflow must be a mapping")
        unknown = set(raw) - _ALLOWED
        if unknown:
            raise _fail("unsupported workflow field")
        name = raw.get("name")
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise _fail("invalid workflow name")
        independent = raw.get("target_independent", False)
        if not isinstance(independent, bool):
            raise _fail("target_independent must be boolean")
        target = raw.get("target")
        if independent:
            if target is not None:
                raise _fail("target-independent workflow cannot declare target")
            plan_target = _INDEPENDENT_TARGET
        else:
            if not isinstance(target, str) or not target.strip():
                raise _fail("target must be a non-empty string")
            plan_target = target
        steps = raw.get("steps")
        if not isinstance(steps, (list, tuple)) or not steps:
            raise _fail("steps must be non-empty")
        plan_input = {
            "target": plan_target,
            "actions": list(steps),
            "max_duration_ms": raw.get("max_duration_ms", 30000),
            "unexpected_screen_policy": raw.get("unexpected_screen_policy", "abort"),
        }
        try:
            plan = validate_plan(plan_input)
            canonical_plan = canonicalize_plan(plan)
        except (TypeError, ValueError, KeyError) as exc:
            raise _fail("invalid workflow plan") from exc
        revision_payload = {
            "name": name,
            "target": None if independent else plan.target,
            "target_independent": independent,
            "plan": {**canonical_plan, "target": None} if independent else canonical_plan,
        }
        payload = json.dumps(revision_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        revision = "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
        supplied = raw.get("revision")
        if supplied is not None and (not isinstance(supplied, str) or supplied != revision):
            raise _fail("workflow revision mismatch")
        definition = cls(name=name, plan=plan, target_independent=independent)
        if definition.revision != revision:
            raise _fail("workflow revision mismatch")
        return definition

    @property
    def target(self) -> str | None:
        return None if self.target_independent else self.plan.target

    def to_mapping(self) -> dict[str, Any]:
        canonical = canonicalize_plan(self.plan)
        if self.target_independent:
            canonical["target"] = None
        actions = [dict(action) for action in canonical.pop("actions")]
        for action in actions:
            if action.get("type") == "text":
                action["value"] = "[REDACTED]"
        return {
            "name": self.name,
            "revision": self.revision,
            "target": canonical.pop("target"),
            "target_independent": self.target_independent,
            "max_duration_ms": canonical["max_duration_ms"],
            "unexpected_screen_policy": canonical["unexpected_screen_policy"],
            "actions": actions,
            "steps": [dict(action) for action in actions],
        }


def _bind(definition: WorkflowDefinition, target: str | None) -> WorkflowDefinition:
    """Return an immutable invocation-bound copy without changing identity."""
    bound = object.__new__(WorkflowDefinition)
    object.__setattr__(bound, "name", definition.name)
    object.__setattr__(bound, "plan", replace(definition.plan, target=target or definition.plan.target))
    object.__setattr__(bound, "target_independent", definition.target_independent)
    object.__setattr__(bound, "revision", definition.revision)
    object.__setattr__(bound, "resolved_target", target)
    return bound


class WorkflowRepository:
    def __init__(self, definitions: Iterable[WorkflowDefinition]):
        try:
            supplied = tuple(definitions)
            if any(not isinstance(definition, WorkflowDefinition) for definition in supplied):
                raise _fail("invalid workflow definition")
            if any(definition._derived_revision() != definition.revision for definition in supplied):
                raise _fail("workflow revision mismatch")
            ordered = tuple(sorted(supplied, key=lambda definition: definition.name))
        except WorkflowError:
            raise
        except (TypeError, ValueError, AttributeError) as exc:
            raise _fail("invalid workflow repository") from exc
        if len({definition.name for definition in ordered}) != len(ordered):
            raise _fail("duplicate workflow name")
        self._definitions = ordered
        self._by_name = MappingProxyType({definition.name: definition for definition in ordered})

    @classmethod
    def from_mappings(cls, mappings: Iterable[Mapping[str, Any]]) -> "WorkflowRepository":
        try:
            definitions = [WorkflowDefinition.from_mapping(raw) for raw in mappings]
        except (TypeError, ValueError) as exc:
            if isinstance(exc, WorkflowError):
                raise
            raise _fail("invalid workflow repository") from exc
        return cls(definitions)

    @classmethod
    def from_file(cls, path: str) -> "WorkflowRepository":
        """Load declarative JSON workflows; never execute file contents."""
        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise WorkflowError("unable to load workflow definitions") from exc
        mappings = payload.get("workflows") if isinstance(payload, Mapping) else payload
        if not isinstance(mappings, list):
            raise WorkflowError("workflow file must contain a list or workflows list")
        return cls.from_mappings(mappings)

    def list(self) -> tuple[WorkflowDefinition, ...]:
        return self._definitions

    def resolve(self, name: str, revision: str, target: str | None = None) -> WorkflowDefinition:
        if not isinstance(name, str):
            raise _fail("invalid workflow name")
        definition = self._by_name.get(name)
        if definition is None:
            raise _fail("unknown workflow")
        if not isinstance(revision, str) or revision != definition.revision:
            raise _fail("workflow revision mismatch")
        if definition.target_independent:
            if not isinstance(target, str) or not target.strip():
                raise _fail("invalid invocation target")
        elif target != definition.target:
            raise _fail("workflow target mismatch")
        if definition.target_independent:
            bound_target = target.strip() if isinstance(target, str) else ""
            return _bind(definition, bound_target)
        return _bind(definition, target)

    def inspect(self, name: str, revision: str | None = None, target: str | None = None) -> dict[str, Any]:
        if not isinstance(name, str):
            raise _fail("invalid workflow name")
        definition = self._by_name.get(name)
        if definition is None:
            raise _fail("unknown workflow")
        if revision is not None:
            self.resolve(name, revision, target)
        elif target is not None:
            if definition.target_independent:
                self.resolve(name, definition.revision, target)
            elif target != definition.target:
                raise _fail("workflow target mismatch")
        return definition.to_mapping()


def list_workflows(repository: WorkflowRepository) -> list[dict[str, Any]]:
    return [definition.to_mapping() for definition in repository.list()]


def inspect_workflow(repository: WorkflowRepository, name: str, revision: str | None = None, target: str | None = None) -> dict[str, Any]:
    return repository.inspect(name, revision, target)


def resolve_workflow(repository: WorkflowRepository, name: str, revision: str, target: str | None = None) -> WorkflowDefinition:
    return repository.resolve(name, revision, target)
