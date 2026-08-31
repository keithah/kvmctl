"""Canonical, bounded plans for target-bound KVM input sequences."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
from typing import Any, Mapping

from .input import parse_combo, resolve_key


class UnexpectedScreenPolicy(str, Enum):
    ABORT = "abort"


@dataclass(frozen=True)
class SequenceLimits:
    max_actions: int = 10
    max_duration_ms: int = 30_000
    max_hold_duration_ms: int = 5_000


@dataclass(frozen=True)
class Action:
    kind: str
    value: str | None = None
    key: str | None = None
    duration_ms: int | None = None
    x: int | None = None
    y: int | None = None
    x_pct: float | None = None
    y_pct: float | None = None
    button: str | None = None
    count: int | None = None
    dx: int | None = None
    dy: int | None = None
    contains: str | None = None

    def to_mapping(self) -> dict[str, Any]:
        fields = {"type": self.kind}
        for name in ("value", "key", "duration_ms", "x", "y", "x_pct", "y_pct", "button", "count", "dx", "dy", "contains"):
            value = getattr(self, name)
            if value is not None:
                fields[name if name != "key" else "key"] = value
        if self.kind == "hold_key":
            fields.pop("value", None)
        return dict(sorted(fields.items()))


@dataclass(frozen=True)
class SequencePlan:
    target: str
    actions: tuple[Action, ...]
    max_duration_ms: int = 30_000
    unexpected_screen_policy: UnexpectedScreenPolicy = UnexpectedScreenPolicy.ABORT

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "SequencePlan":
        if not isinstance(raw, Mapping):
            raise TypeError("plan must be a mapping")
        allowed = {"target", "actions", "max_duration_ms", "unexpected_screen_policy"}
        _reject_fields(raw, allowed, "plan")
        target = raw.get("target")
        if not isinstance(target, str) or not target.strip():
            raise ValueError("target must be a non-empty string")
        raw_actions = raw.get("actions")
        if not isinstance(raw_actions, (list, tuple)) or not raw_actions:
            raise ValueError("actions must be non-empty")
        if len(raw_actions) > SequenceLimits().max_actions:
            raise ValueError("too many actions")
        duration = _integer(raw.get("max_duration_ms", 30_000), "max_duration_ms")
        if not 1 <= duration <= SequenceLimits().max_duration_ms:
            raise ValueError("max_duration_ms must be between 1 and 30000")
        policy = raw.get("unexpected_screen_policy", "abort")
        try:
            policy = UnexpectedScreenPolicy(policy)
        except (ValueError, TypeError) as exc:
            raise ValueError("unsupported unexpected_screen_policy") from exc
        return cls(target=target.strip(), actions=tuple(_action(a) for a in raw_actions),
                   max_duration_ms=duration, unexpected_screen_policy=policy)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "actions": [a.to_mapping() for a in self.actions],
            "max_duration_ms": self.max_duration_ms,
            "target": self.target,
            "unexpected_screen_policy": self.unexpected_screen_policy.value,
        }


def _reject_fields(mapping: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(mapping) - allowed
    if unknown:
        raise ValueError(f"unsupported {label} field(s): {', '.join(sorted(unknown))}")


def _number(value: Any, label: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be finite numeric")
    return int(value) if float(value).is_integer() else float(value)


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be an integer")
    if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
        raise ValueError(f"{label} must be an integer")
    return int(value)


def _action(raw: Any) -> Action:
    if not isinstance(raw, Mapping):
        raise TypeError("each action must be a mapping")
    kind = raw.get("type")
    schemas = {
        "text": {"type", "value"}, "key": {"type", "value"},
        "hold_key": {"type", "key", "duration_ms"}, "release_all": {"type"},
        "mouse_move": {"type", "x", "y"}, "mouse_move_pct": {"type", "x_pct", "y_pct"},
        "mouse_click": {"type", "button", "count"}, "mouse_scroll": {"type", "dx", "dy"},
        "wait": {"type", "duration_ms"},
        "assert_screen": {"type", "contains"},
    }
    if kind not in schemas:
        raise ValueError(f"unsupported action type: {kind!r}")
    _reject_fields(raw, schemas[kind], "action")
    if kind == "text":
        if not isinstance(raw.get("value"), str): raise ValueError("text value must be a string")
        return Action(kind, value=raw["value"])
    if kind == "key":
        modifiers, main = parse_combo(raw.get("value", ""))
        return Action(kind, value="+".join([*modifiers, main]))
    if kind == "hold_key":
        key = resolve_key(raw.get("key", ""))
        duration = _integer(raw.get("duration_ms"), "duration_ms")
        if not 1 <= duration <= 5000: raise ValueError("hold duration must be between 1 and 5000 ms")
        return Action(kind, key=key, duration_ms=duration)
    if kind == "release_all": return Action(kind)
    if kind == "mouse_move":
        x, y = _integer(raw.get("x"), "x"), _integer(raw.get("y"), "y")
        if not (-32768 <= x <= 32767 and -32768 <= y <= 32767): raise ValueError("mouse coordinates out of range")
        return Action(kind, x=x, y=y)
    if kind == "mouse_move_pct":
        x, y = _number(raw.get("x_pct"), "x_pct"), _number(raw.get("y_pct"), "y_pct")
        if not (0 <= x <= 100 and 0 <= y <= 100): raise ValueError("mouse percentages out of range")
        return Action(kind, x_pct=x, y_pct=y)
    if kind == "mouse_click":
        button = raw.get("button", "left")
        count = _integer(raw.get("count", 1), "count")
        if button not in {"left", "middle", "right", "up", "down"} or not 1 <= count <= 5: raise ValueError("invalid mouse click")
        return Action(kind, button=button, count=count)
    if kind == "mouse_scroll":
        dx, dy = _integer(raw.get("dx", 0), "dx"), _integer(raw.get("dy", 0), "dy")
        if not (-127 <= dx <= 127 and -127 <= dy <= 127): raise ValueError("mouse scroll out of range")
        return Action(kind, dx=dx, dy=dy)
    if kind == "assert_screen":
        contains = raw.get("contains")
        if not isinstance(contains, str) or not contains or len(contains) > 200:
            raise ValueError("screen assertion must contain 1-200 characters")
        return Action(kind, contains=contains)
    duration = _integer(raw.get("duration_ms"), "duration_ms")
    if not 1 <= duration <= 30000: raise ValueError("wait duration out of range")
    return Action(kind, duration_ms=duration)


def _validate_typed_plan(plan: SequencePlan) -> None:
    if not isinstance(plan.target, str) or not plan.target.strip():
        raise ValueError("target must be a non-empty string")
    if not isinstance(plan.actions, tuple) or not plan.actions:
        raise ValueError("actions must be a non-empty tuple")
    if len(plan.actions) > SequenceLimits().max_actions:
        raise ValueError("too many actions")
    duration = _integer(plan.max_duration_ms, "max_duration_ms")
    if not 1 <= duration <= SequenceLimits().max_duration_ms:
        raise ValueError("max_duration_ms must be between 1 and 30000")
    if not isinstance(plan.unexpected_screen_policy, UnexpectedScreenPolicy):
        raise ValueError("unsupported unexpected_screen_policy")
    for action in plan.actions:
        if not isinstance(action, Action):
            raise TypeError("each action must be an Action")
        _action(action.to_mapping())


def validate_plan(plan: SequencePlan | Mapping[str, Any]) -> SequencePlan:
    if isinstance(plan, SequencePlan):
        _validate_typed_plan(plan)
        return plan
    return SequencePlan.from_mapping(plan)


def canonicalize_plan(plan: SequencePlan | Mapping[str, Any]) -> dict[str, Any]:
    canonical = validate_plan(plan).to_mapping()
    canonical["target"] = canonical["target"].strip()
    return canonical


def plan_hash(plan: SequencePlan | Mapping[str, Any]) -> str:
    payload = json.dumps(canonicalize_plan(plan), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
