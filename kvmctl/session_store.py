"""Tamper-evident persistence for verified sessions and capabilities."""
from __future__ import annotations
import hashlib, hmac, json, os, pathlib, stat, time
from contextlib import contextmanager
import fcntl
from .machines import SelectionRecord, SelectionState, SessionState


def _paths(path):
    p = pathlib.Path(path).expanduser()
    return p, p.with_name(p.name + ".key")


def _secure_file(path: pathlib.Path, *, allow_missing=False) -> None:
    try:
        info = path.stat()
    except FileNotFoundError:
        if allow_missing: return
        raise
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077):
        raise PermissionError(f"unsafe persistent file: {path}")


def _atomic_write(path: pathlib.Path, data: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.chmod(tmp, 0o600)
    _secure_file(tmp)
    os.replace(tmp, path)


def save_session(session: SessionState, path: str, *, endpoint: str) -> None:
    rec = session.current
    if rec is None or not rec.verified:
        return
    payload = {"endpoint": endpoint, "machine": rec.machine, "port": rec.port,
               "state": rec.state.value, "detail": rec.detail[:300], "at": rec.at}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    p, keypath = _paths(path); p.parent.mkdir(parents=True, exist_ok=True)
    if not keypath.exists():
        keypath.write_bytes(os.urandom(32)); os.chmod(keypath, 0o600)
    _secure_file(keypath); _secure_file(p, allow_missing=True)
    key = keypath.read_bytes()
    envelope = {"payload": payload, "mac": hmac.new(key, raw, hashlib.sha256).hexdigest()}
    _atomic_write(p, json.dumps(envelope, sort_keys=True))


class FileAuthorizationStore:
    """Tamper-evident, cross-process atomic multi-capability store."""
    def __init__(self, path: str):
        self.path, self.keypath = _paths(path)
        self.lockpath = self.path.with_name(self.path.name + ".lock")

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _secure_file(self.path, allow_missing=True); _secure_file(self.keypath, allow_missing=True)
        with self.lockpath.open("a+") as lock:
            os.chmod(self.lockpath, 0o600); _secure_file(self.lockpath)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try: yield
            finally: fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _records(self):
        if not self.path.exists(): return []
        _secure_file(self.path); _secure_file(self.keypath)
        env = json.loads(self.path.read_text(encoding="utf-8"))
        if "payloads" in env:
            records = env["payloads"]
            raw = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
        else:  # read old single-capability files during migration
            records = [env["payload"]]
            raw = json.dumps(env["payload"], sort_keys=True, separators=(",", ":")).encode()
        if not hmac.compare_digest(env["mac"], hmac.new(self.keypath.read_bytes(), raw, hashlib.sha256).hexdigest()):
            return []
        return records

    def _write_records(self, records):
        raw = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
        env = {"payloads": records, "mac": hmac.new(self.keypath.read_bytes(), raw, hashlib.sha256).hexdigest()}
        _atomic_write(self.path, json.dumps(env, sort_keys=True))

    def put(self, auth):
        with self._locked():
            if not self.keypath.exists():
                self.keypath.write_bytes(os.urandom(32)); os.chmod(self.keypath, 0o600)
            _secure_file(self.keypath)
            payload = {"token": auth.token, "target": auth.target, "plan": auth.plan.to_mapping(),
                       "plan_hash": auth.plan_hash, "expires_at": auth.expires_at,
                       "workflow_revision": auth.workflow_revision, "binding": auth.binding, "used": False}
            records = [p for p in self._records() if p.get("token") != auth.token]
            records.append(payload)
            self._write_records(records)

    def _read(self, token, *, consume, binding=None):
        with self._locked():
            try:
                records = self._records()
                p = next((item for item in records if item["token"] == token), None)
                if p is None or p["used"]: return None
                if binding is not None and not hmac.compare_digest(str(p.get("binding", "")), str(binding)): return None
                if consume:
                    p["used"] = True; self._write_records(records)
                from .sequences import validate_plan
                from .sequence_executor import SequenceAuthorization
                return SequenceAuthorization(validate_plan(p["plan"]), p["target"], p["plan_hash"],
                    float(p["expires_at"]), p.get("workflow_revision"), token=token, binding=p.get("binding", ""))
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, PermissionError):
                return None

    def peek(self, token, *, binding=None): return self._read(token, consume=False, binding=binding)
    def take(self, token, *, binding=None): return self._read(token, consume=True, binding=binding)


def load_session(path: str, *, endpoint: str, max_age_s: float = 3600.0):
    session = SessionState(); p, keypath = _paths(path)
    try:
        _secure_file(p); _secure_file(keypath)
        env = json.loads(p.read_text(encoding="utf-8")); payload = env["payload"]
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        if not hmac.compare_digest(env["mac"], hmac.new(keypath.read_bytes(), raw, hashlib.sha256).hexdigest()): return session
        if payload.get("endpoint") != endpoint or payload.get("state") != "verified": return session
        if time.time() - float(payload["at"]) > max_age_s: return session
        machine = payload["machine"]
        from .machines import RACK
        if machine not in RACK or int(payload["port"]) != RACK[machine].port: return session
        session.current = SelectionRecord(machine, int(payload["port"]), SelectionState.VERIFIED,
                                          str(payload.get("detail", "")), float(payload["at"]))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, PermissionError): pass
    return session
