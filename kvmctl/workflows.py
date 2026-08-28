"""Immutable, declarative named KVM workflow definitions."""
from __future__ import annotations

from dataclasses import dataclass, replace
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
    revision: str
    target_independent: bool = False
    resolved_target: str | None = None

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
            "plan": canonical_plan,
        }
        payload = json.dumps(revision_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        revision = "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
        supplied = raw.get("revision")
        if supplied is not None and (not isinstance(supplied, str) or supplied != revision):
            raise _fail("workflow revision mismatch")
        return cls(name=name, plan=plan, revision=revision, target_independent=independent)

    @property
    def target(self) -> str | None:
        return None if self.target_independent else self.plan.target

    def to_mapping(self) -> dict[str, Any]:
        canonical = canonicalize_plan(self.plan)
        if self.target_independent:
            canonical["target"] = None
        actions = [dict(action) for action in canonical.pop("actions")]
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


class WorkflowRepository:
    def __init__(self, definitions: Iterable[WorkflowDefinition]):
        ordered = tuple(sorted(definitions, key=lambda definition: definition.name))
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

    def list(self) -> tuple[WorkflowDefinition, ...]:
        return self._definitions

    def resolve(self, name: str, revision: str, target: str | None = None) -> WorkflowDefinition:
        definition = self._by_name.get(name)
        if definition is None:
            raise _fail("unknown workflow")
        if not isinstance(revision, str) or revision != definition.revision:
            raise _fail("workflow revision mismatch")
        if definition.target_independent:
            if target is not None and (not isinstance(target, str) or not target.strip()):
                raise _fail("invalid invocation target")
        elif target != definition.target:
            raise _fail("workflow target mismatch")
        return replace(definition, resolved_target=target)

    def inspect(self, name: str, revision: str | None = None, target: str | None = None) -> dict[str, Any]:
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
