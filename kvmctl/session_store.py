"""Tamper-evident persistence for verified sessions and capabilities."""
from __future__ import annotations
import hashlib, hmac, json, math, os, pathlib, stat, tempfile, time, errno
from contextlib import contextmanager
import fcntl
from .machines import SelectionRecord, SelectionState, SessionState


def _paths(path):
    p = pathlib.Path(path).expanduser()
    return p, p.with_name(p.name + ".key")


class AuthorizationStoreIntegrityError(PermissionError):
    """Persistent authorization state cannot be trusted."""


def _secure_dir(path: pathlib.Path, *, create=False) -> None:
    path = pathlib.Path(path).expanduser()
    if create:
        parts = path.parts
        current = pathlib.Path(parts[0])
        for part in parts[1:]:
            current /= part
            try:
                info = current.lstat()
            except FileNotFoundError:
                current.mkdir(mode=0o700)
                info = current.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise PermissionError(f"unsafe persistent directory: {current}")
        info = path.lstat()
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise PermissionError(f"unsafe persistent directory: {path}")
        return
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise PermissionError(f"unsafe persistent directory: {path}")


def _secure_file(path: pathlib.Path, *, allow_missing=False) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if allow_missing: return
        raise
    if (stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077):
        raise PermissionError(f"unsafe persistent file: {path}")


def _create_secret(path: pathlib.Path) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, os.urandom(32))
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(path: pathlib.Path, data: str) -> None:
    path = pathlib.Path(path)
    _secure_dir(path.parent)
    _secure_file(path, allow_missing=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = pathlib.Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise PermissionError(f"unsafe temporary file: {tmp}")
        os.replace(tmp, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def save_session(session: SessionState, path: str, *, endpoint: str) -> None:
    rec = session.current
    if rec is None or not rec.verified:
        return
    payload = {"endpoint": endpoint, "machine": rec.machine, "port": rec.port,
               "state": rec.state.value, "detail": rec.detail[:300], "at": rec.at}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    p, keypath = _paths(path); _secure_dir(p.parent, create=True)
    if not keypath.exists():
        try:
            _create_secret(keypath)
        except FileExistsError:
            pass
    _secure_file(keypath); _secure_file(p, allow_missing=True)
    key = keypath.read_bytes()
    envelope = {"payload": payload, "mac": hmac.new(key, raw, hashlib.sha256).hexdigest()}
    _atomic_write(p, json.dumps(envelope, sort_keys=True))


class FileAuthorizationStore:
    """Tamper-evident, cross-process atomic multi-capability store."""
    def __init__(self, path: str, *, clock=time.monotonic, max_ttl_s=30.0):
        self.path, self.keypath = _paths(path)
        self.lockpath = self.path.with_name(self.path.name + ".lock")
        self.clock = clock
        self.max_ttl_s = max_ttl_s

    @contextmanager
    def _locked(self):
        _secure_dir(self.path.parent, create=True)
        _secure_file(self.path, allow_missing=True); _secure_file(self.keypath, allow_missing=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(self.lockpath, flags, 0o600)
        except OSError as exc:
            if getattr(exc, "errno", None) == errno.ELOOP:
                raise PermissionError(f"unsafe lock file: {self.lockpath}") from exc
            raise
        lock = os.fdopen(fd, "a+")
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise PermissionError(f"unsafe lock file: {self.lockpath}")
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try: yield
            finally: fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        finally:
            lock.close()

    def _records(self):
        if not self.path.exists(): return []
        _secure_file(self.path); _secure_file(self.keypath)
        try:
            env = json.loads(self.path.read_text(encoding="utf-8"))
            if "payloads" in env:
                records = env["payloads"]
                raw = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
            else:  # read old single-capability files during migration
                records = [env["payload"]]
                raw = json.dumps(env["payload"], sort_keys=True, separators=(",", ":")).encode()
            valid = hmac.compare_digest(env["mac"], hmac.new(self.keypath.read_bytes(), raw, hashlib.sha256).hexdigest())
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise AuthorizationStoreIntegrityError("authorization store integrity failure") from exc
        if not valid:
            raise AuthorizationStoreIntegrityError("authorization store integrity failure")
        return records

    def _write_records(self, records):
        raw = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
        env = {"payloads": records, "mac": hmac.new(self.keypath.read_bytes(), raw, hashlib.sha256).hexdigest()}
        _atomic_write(self.path, json.dumps(env, sort_keys=True))

    def put(self, auth):
        try:
            payload = {"token": auth.token, "target": auth.target, "plan": auth.plan.to_mapping(),
                       "plan_hash": auth.plan_hash, "expires_at": auth.expires_at,
                       "workflow_revision": auth.workflow_revision, "binding": auth.binding,
                       "session_id": auth.session_id, "used": False}
            # Validate the complete candidate before touching either the key or
            # the existing capability bytes.
            self._validate_record(payload)
        except (AttributeError, TypeError, ValueError, KeyError) as exc:
            raise ValueError("invalid authorization") from exc
        with self._locked():
            if not self.keypath.exists():
                try:
                    _create_secret(self.keypath)
                except FileExistsError:
                    pass
            _secure_file(self.keypath)
            records = [p for p in self._records() if p.get("token") != auth.token]
            records.append(payload)
            self._write_records(records)

    def _read(self, token, *, consume, binding=None):
        with self._locked():
            try:
                records = self._records()
                # Validate every record before selecting or rewriting one.  A
                # MAC authenticates bytes, not their meaning.
                capabilities = [self._validate_record(item) for item in records]
                index = next((i for i, item in enumerate(capabilities) if item.token == token), None)
                if index is None:
                    return None
                auth = capabilities[index]
                if records[index]["used"] is True:
                    return None
                if binding is not None and not hmac.compare_digest(auth.binding, binding): return None
                if consume:
                    records[index]["used"] = True
                    self._write_records(records)
                return auth
            except AuthorizationStoreIntegrityError:
                raise
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                raise AuthorizationStoreIntegrityError("authorization store integrity failure") from exc
            except OSError:
                return None

    def _validate_record(self, p):
        from .sequences import plan_hash, validate_plan
        from .sequence_executor import SequenceAuthorization
        required = {"token", "target", "plan", "plan_hash", "expires_at",
                    "workflow_revision", "binding", "session_id", "used"}
        if not isinstance(p, dict) or set(p) != required:
            raise ValueError("invalid authorization schema")
        if not isinstance(p["token"], str) or not p["token"]:
            raise ValueError("invalid authorization token")
        if not isinstance(p["target"], str) or not p["target"]:
            raise ValueError("invalid authorization target")
        if not isinstance(p["plan"], dict):
            raise ValueError("invalid authorization plan")
        plan = validate_plan(p["plan"])
        if p["plan"] != plan.to_mapping() or p["target"] != plan.target:
            raise ValueError("invalid authorization plan binding")
        if not isinstance(p["plan_hash"], str) or not hmac.compare_digest(plan_hash(plan), p["plan_hash"]):
            raise ValueError("invalid authorization plan hash")
        if not isinstance(p["workflow_revision"], (str, type(None))) or (isinstance(p["workflow_revision"], str) and not p["workflow_revision"]):
            raise ValueError("invalid workflow revision")
        if not isinstance(p["binding"], str) or not p["binding"]:
            raise ValueError("invalid authorization binding")
        if isinstance(p["session_id"], bool) or not isinstance(p["session_id"], int) or p["session_id"] < 0:
            raise ValueError("invalid authorization session")
        if not isinstance(p["used"], bool):
            raise ValueError("invalid authorization use state")
        expires_at = p["expires_at"]
        if isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)):
            raise ValueError("invalid authorization expiry")
        expires_at = float(expires_at)
        now = self.clock()
        if not math.isfinite(expires_at) or not math.isfinite(now) or not math.isfinite(self.max_ttl_s):
            raise ValueError("invalid authorization expiry")
        if expires_at <= now or expires_at > now + self.max_ttl_s:
            raise ValueError("authorization expiry outside allowed window")
        return SequenceAuthorization(plan, p["target"], p["plan_hash"], expires_at,
            p["workflow_revision"], token=p["token"], session_id=p["session_id"], binding=p["binding"])

    def peek(self, token, *, binding=None): return self._read(token, consume=False, binding=binding)
    def take(self, token, *, binding=None): return self._read(token, consume=True, binding=binding)


def load_session(path: str, *, endpoint: str, max_age_s: float = 3600.0):
    session = SessionState(); p, keypath = _paths(path)
    try:
        _secure_dir(p.parent)
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
