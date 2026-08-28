"""Stable, JSON-compatible results for remote operations.

The builder deliberately returns plain dictionaries so existing CLI and MCP
callers can consume results without importing a model class.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, TypedDict


class OperationError(TypedDict, total=False):
    """Stable error fields used by operation results."""

    code: str
    retryable: bool
    requires_human: bool
    message: str


class OperationResult(TypedDict):
    operation: str
    target: str | None
    transport: str
    read_only: bool
    ok: bool
    changed: bool
    state: str
    evidence: dict[str, Any]
    warnings: list[str]
    error: OperationError | None
    next_actions: list[str]


def _normalise_error(error: Mapping[str, Any] | str | None) -> OperationError | None:
    if error is None:
        return None
    if isinstance(error, str):
        return {"code": error, "retryable": False, "requires_human": False}
    result: OperationError = dict(error)  # type: ignore[assignment]
    result.setdefault("code", "operation_failed")
    result.setdefault("retryable", False)
    result.setdefault("requires_human", False)
    return result


def operation_result(
    *,
    operation: str,
    transport: str,
    read_only: bool,
    target: str | None = None,
    ok: bool = True,
    changed: bool = False,
    state: str = "unknown",
    evidence: Mapping[str, Any] | None = None,
    warnings: Sequence[str] = (),
    error: Mapping[str, Any] | str | None = None,
    next_actions: Sequence[str] = (),
) -> OperationResult:
    """Build a result with the complete stable operation-result shape."""
    return {
        "operation": operation,
        "target": target,
        "transport": transport,
        "read_only": read_only,
        "ok": ok,
        "changed": changed,
        "state": state,
        "evidence": dict(evidence or {}),
        "warnings": list(warnings),
        "error": _normalise_error(error),
        "next_actions": list(next_actions),
    }


def _from_legacy(
    legacy: Mapping[str, Any],
    *,
    target: str | None = None,
    changed: bool = False,
    state: str = "unknown",
    warnings: Sequence[str] = (),
    next_actions: Sequence[str] = (),
) -> OperationResult:
    """Adapt the existing ``semantics._evidence`` dictionary shape."""
    return operation_result(
        operation=legacy["operation"],
        target=target,
        transport=legacy["transport"],
        read_only=legacy["read_only"],
        ok=legacy.get("ok", True),
        changed=changed,
        state=state,
        evidence=legacy.get("evidence", {}),
        warnings=warnings,
        error=legacy.get("error"),
        next_actions=next_actions,
    )


# Keep the convenient function API while exposing compatibility as a helper.
operation_result.from_legacy = _from_legacy  # type: ignore[attr-defined]

# Explicit alias for callers that prefer a verb-style builder.
build_result = operation_result

__all__ = ["OperationError", "OperationResult", "build_result", "operation_result"]
