"""Small append-only journal for bounded, secret-free operation checkpoints."""
from __future__ import annotations

import dataclasses
import datetime as _datetime
import errno
import fcntl
import json
import math
import os
import stat
import sys
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from kvmctl.session_store import _open_secure_dir

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


def _cleanup(action, label: str) -> None:
    """Attempt cleanup without masking an exception already in flight."""
    primary = sys.exc_info()[1]
    try:
        action()
    except BaseException as cleanup_error:
        if primary is None:
            raise
        primary.add_note(f"journal cleanup {label} failed: {cleanup_error!r}")


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
        with _lock_for(self.path):
            parent_fd = _open_secure_dir(self.path.parent, create=True)
            lock_fd = None
            try:
                lock_flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
                try:
                    lock_fd = os.open(self.path.name + ".lock", lock_flags, 0o600,
                                      dir_fd=parent_fd)
                except OSError as exc:
                    if getattr(exc, "errno", None) == errno.ELOOP:
                        raise PermissionError(f"unsafe journal lock file: {self.path}.lock") from exc
                    raise
                lock_acquired = False
                try:
                    info = os.fstat(lock_fd)
                    if (not stat.S_ISREG(info.st_mode)
                            or info.st_uid != os.getuid()
                            or stat.S_IMODE(info.st_mode) != 0o600):
                        raise PermissionError(f"unsafe journal lock file: {self.path}.lock")
                    fcntl.flock(lock_fd, fcntl.LOCK_EX)
                    lock_acquired = True
                    fd = None
                    try:
                        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
                        fd = os.open(self.path.name, flags, 0o600, dir_fd=parent_fd)
                        info = os.fstat(fd)
                        if (not stat.S_ISREG(info.st_mode)
                                or info.st_uid != os.getuid()
                                or stat.S_IMODE(info.st_mode) != 0o600):
                            raise PermissionError(f"unsafe journal file: {self.path}")
                        offset = os.lseek(fd, 0, os.SEEK_END)
                        try:
                            written = 0
                            while written < len(payload):
                                count = os.write(fd, payload[written:])
                                if count <= 0:
                                    raise OSError("short atomic journal append")
                                written += count
                            os.fsync(fd)
                        except BaseException as original:
                            try:
                                os.ftruncate(fd, offset)
                                try:
                                    os.fsync(fd)
                                except BaseException as rollback_error:
                                    original.add_note(f"journal rollback fsync failed: {rollback_error!r}")
                            except BaseException as rollback_error:
                                original.add_note(f"journal rollback failed: {rollback_error!r}")
                            raise
                    finally:
                        if fd is not None:
                            _cleanup(lambda: os.close(fd), "journal fd close")
                finally:
                    if lock_acquired:
                        _cleanup(lambda: fcntl.flock(lock_fd, fcntl.LOCK_UN), "lock unlock")
            finally:
                if lock_fd is not None:
                    _cleanup(lambda: os.close(lock_fd), "lock fd close")
                    _cleanup(lambda: os.close(parent_fd), "parent fd close")
                else:
                    _cleanup(lambda: os.close(parent_fd), "parent fd close")

    def checkpoint(self, *, operation: str, target: str | None,
                   transition: str, **details: Any) -> None:
        # Checkpoint identity is controlled by the caller's explicit fields;
        # details cannot spoof operation/target/transition in the journal.
        for reserved in ("operation", "target", "transition"):
            details.pop(reserved, None)
        self.append({"operation": operation, "target": target,
                     "transition": transition, **details})


__all__ = ["Journal"]
