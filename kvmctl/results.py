"""Stable, JSON-compatible results for remote operations.

The builder deliberately returns plain dictionaries so existing CLI and MCP
callers can consume results without importing a model class.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any, TypedDict

from kvmctl.policy import PolicyError


_SENSITIVE_KEY = re.compile(
    r"(?i)(?:pass(?:word|wd)?|token|secret|private[_ -]?key|credential|api[_ -]?key|authorization|cookie)"
)


def _safe(value: Any) -> Any:
    """Copy result data while dropping fields whose names may contain secrets."""
    if isinstance(value, Mapping):
        return {str(key): _safe(item) for key, item in value.items()
                if not _SENSITIVE_KEY.search(str(key))}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    return value


_SAFE_ERROR_MESSAGES = frozenset({
    "authorization expired", "authorization invalid", "authorization missing",
    "authorization used", "authorization target mismatch", "device lock conflict",
    "deadline exceeded", "invalid input", "operation failed", "operation rejected",
    "permission denied", "policy refused", "backend unavailable",
    "plan hash changed", "plan mismatch", "plan must be approved",
    "screen assertion failed", "screen assertion unavailable", "target mismatch",
    "target mismatch or session not verified", "target session is not verified",
    "workflow revision mismatch", "workflow target mismatch",
    "write_disabled", "host_reboot_timeout", "host_identity_mismatch",
})


def normalize_error(error: BaseException | str | None, *, default: str = "operation failed") -> str | None:
    """Convert untrusted exception text into a small safe public error code."""
    if error is None:
        return None
    text = error if isinstance(error, str) else str(error)
    if text in _SAFE_ERROR_MESSAGES:
        return text
    if text.startswith("unsupported argument"):
        return "unsupported argument"
    if text.startswith("invalid argument"):
        return "invalid argument"
    if text.startswith("duration_ms must be finite numeric"):
        return "duration_ms must be finite numeric"
    if isinstance(error, PolicyError):
        return "policy refused"
    if isinstance(error, (TypeError, ValueError, KeyError)):
        return "invalid input"
    if isinstance(error, PermissionError):
        return "permission denied"
    if isinstance(error, OSError):
        return "backend unavailable"
    return default if default in _SAFE_ERROR_MESSAGES else "operation failed"


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
        return {"code": normalize_error(error), "retryable": False, "requires_human": False}
    result: OperationError = {
        "code": normalize_error(error.get("code"), default="operation failed") or "operation failed",
        "retryable": bool(error.get("retryable", False)),
        "requires_human": bool(error.get("requires_human", False)),
    }
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
        "evidence": _safe(dict(evidence or {})),
        "warnings": list(warnings),
        "error": _safe(_normalise_error(error)),
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

__all__ = ["OperationError", "OperationResult", "build_result", "normalize_error", "operation_result"]
