"""Tamper-evident persistence for the CLI's verified target session."""
from __future__ import annotations
import hashlib, hmac, json, os, pathlib, tempfile
from contextlib import contextmanager
import fcntl
from .machines import SelectionRecord, SelectionState, SessionState


def _paths(path):
    p = pathlib.Path(path).expanduser()
    return p, p.with_name(p.name + ".key")


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
    key = keypath.read_bytes(); envelope = {"payload": payload, "mac": hmac.new(key, raw, hashlib.sha256).hexdigest()}
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(envelope, sort_keys=True), encoding="utf-8"); os.chmod(tmp, 0o600); os.replace(tmp, p)


class FileAuthorizationStore:
    """Small tamper-evident cross-process single-use token store."""
    def __init__(self, path: str):
        self.path, self.keypath = _paths(path)
        self.lockpath = self.path.with_name(self.path.name + ".lock")

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lockpath.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    def put(self, auth):
        with self._locked():
            if not self.keypath.exists(): self.keypath.write_bytes(os.urandom(32)); os.chmod(self.keypath, 0o600)
            payload = {"token": auth.token, "target": auth.target, "plan": auth.plan.to_mapping(), "plan_hash": auth.plan_hash,
                       "expires_at": auth.expires_at, "workflow_revision": auth.workflow_revision,
                       "binding": auth.binding, "used": False}
            raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(); env = {"payload": payload, "mac": hmac.new(self.keypath.read_bytes(), raw, hashlib.sha256).hexdigest()}
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(env, sort_keys=True), encoding="utf-8"); os.chmod(tmp, 0o600); os.replace(tmp, self.path)
    def _read(self, token, *, consume, binding=None):
      with self._locked():
        try:
            env=json.loads(self.path.read_text(encoding="utf-8")); p=env["payload"]; raw=json.dumps(p,sort_keys=True,separators=(",", ":")).encode()
            if not hmac.compare_digest(env["mac"], hmac.new(self.keypath.read_bytes(),raw,hashlib.sha256).hexdigest()) or p["token"] != token or p["used"]: return None
            if binding is not None and not hmac.compare_digest(str(p.get("binding", "")), str(binding)): return None
            if consume:
                p["used"] = True; self.path.write_text(json.dumps({"payload":p,"mac":hmac.new(self.keypath.read_bytes(),json.dumps(p,sort_keys=True,separators=(",", ":")).encode(),hashlib.sha256).hexdigest()},sort_keys=True), encoding="utf-8")
            from .sequences import validate_plan
            from .sequence_executor import SequenceAuthorization
            return SequenceAuthorization(validate_plan(p["plan"]), p["target"], p["plan_hash"], float(p["expires_at"]), p.get("workflow_revision"), token=token, binding=p.get("binding", ""))
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError): return None
    def peek(self, token, *, binding=None):
        return self._read(token, consume=False, binding=binding)
    def take(self, token, *, binding=None):
        return self._read(token, consume=True, binding=binding)


def load_session(path: str, *, endpoint: str, max_age_s: float = 3600.0):
    session = SessionState(); p, keypath = _paths(path)
    try:
        env = json.loads(p.read_text(encoding="utf-8")); payload = env["payload"]
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        if not hmac.compare_digest(env["mac"], hmac.new(keypath.read_bytes(), raw, hashlib.sha256).hexdigest()):
            return session
        if payload.get("endpoint") != endpoint or payload.get("state") != "verified": return session
        if __import__("time").time() - float(payload["at"]) > max_age_s: return session
        machine = payload["machine"]
        from .machines import RACK
        if machine not in RACK or int(payload["port"]) != RACK[machine].port: return session
        session.current = SelectionRecord(machine, int(payload["port"]), SelectionState.VERIFIED, str(payload.get("detail", "")), float(payload["at"]))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        pass
    return session
