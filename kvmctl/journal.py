"""Small append-only journal for bounded, secret-free operation checkpoints."""
from __future__ import annotations

import dataclasses
import datetime as _datetime
import json
import math
import os
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_SECRET_KEY = __import__("re").compile(
    r"(?i)(?:pass(?:word|wd)?|token|secret|private[_ -]?key|credential|api[_ -]?key|authorization|cookie)"
)
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = str(path.absolute())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def _safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return "<maximum nesting depth exceeded>"
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _safe(dataclasses.asdict(value), depth=depth + 1)
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            name = str(key)
            if _SECRET_KEY.search(name):
                continue
            result[name] = _safe(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_safe(item, depth=depth + 1) for item in value[:256]]
    if isinstance(value, (set, frozenset)):
        return [_safe(item, depth=depth + 1) for item in sorted(value, key=repr)[:256]]
    if isinstance(value, (_datetime.date, _datetime.datetime, _datetime.time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return f"<bytes omitted: {len(value)} bytes>"
    return f"<{type(value).__name__}>"


class Journal:
    """Write one complete JSON object per line, never rewriting old entries."""

    def __init__(self, path: str | os.PathLike[str], *, max_record_bytes: int = 65536):
        if not isinstance(max_record_bytes, int) or isinstance(max_record_bytes, bool) or max_record_bytes < 32:
            raise ValueError("max_record_bytes must be an integer of at least 32")
        self.path = Path(path)
        self.max_record_bytes = max_record_bytes

    def append(self, record: Mapping[str, Any]) -> None:
        if not isinstance(record, Mapping):
            raise TypeError("journal record must be a mapping")
        payload = json.dumps(_safe(record), ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
        if len(payload) > self.max_record_bytes:
            raise ValueError("journal record exceeds bound")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _lock_for(self.path):
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                written = os.write(fd, payload)
                if written != len(payload):
                    raise OSError("short atomic journal append")
                os.fsync(fd)
            finally:
                os.close(fd)

    def checkpoint(self, *, operation: str, target: str | None,
                   transition: str, **details: Any) -> None:
        self.append({"operation": operation, "target": target,
                     "transition": transition, **details})


__all__ = ["Journal"]
