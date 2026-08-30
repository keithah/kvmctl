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
    fd = _open_secure_dir(path, create=create)
    os.close(fd)


def _open_secure_dir(path: pathlib.Path, *, create=False, check_final=True) -> int:
    """Open every parent component without following symlinks."""
    path = pathlib.Path(path).expanduser()
    # macOS exposes /var and /tmp as stable system aliases.  Canonicalize only
    # those aliases; all application-controlled components are still opened
    # descriptor-relative with O_NOFOLLOW below.
    if path.is_absolute() and path.parts[1:2] in (("var",), ("tmp",)):
        alias = path.parts[1]
        target = pathlib.Path("/private") / alias
        if os.path.islink("/" + alias) and os.readlink("/" + alias) == "private/" + alias:
            path = target.joinpath(*path.parts[2:])
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    absolute = path.is_absolute()
    fd = os.open(os.sep if absolute else ".", flags)
    try:
        parts = path.parts[1:] if absolute else path.parts
        for part in parts:
            try:
                next_fd = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                next_fd = os.open(part, flags, dir_fd=fd)
            except OSError as exc:
                raise PermissionError(f"unsafe persistent directory: {path}") from exc
            os.close(fd)
            fd = next_fd
            info = os.fstat(fd)
            if not stat.S_ISDIR(info.st_mode):
                raise PermissionError(f"unsafe persistent directory: {path}")
        info = os.fstat(fd)
        if check_final and (info.st_uid != os.getuid() or info.st_mode & 0o022):
            raise PermissionError(f"unsafe persistent directory: {path}")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _secure_file(path: pathlib.Path, *, allow_missing=False) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if allow_missing: return
        raise
    if (stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600):
        raise PermissionError(f"unsafe persistent file: {path}")


def _read_regular(path: pathlib.Path, *, allow_missing=False) -> bytes | None:
    """Read one validated file descriptor, never a path after validation."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        if allow_missing:
            return None
        raise
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise PermissionError(f"unsafe persistent file: {path}")
        chunks = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(fd)


def _read_regular_fd(parent_fd: int, name: str, *, allow_missing=False) -> bytes | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        if allow_missing:
            return None
        raise
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise PermissionError(f"unsafe persistent file: {name}")
        chunks = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(fd)


def _create_secret(path: pathlib.Path) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, os.urandom(32))
        os.fsync(fd)
    finally:
        os.close(fd)


def _create_secret_fd(parent_fd: int, name: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(name, flags, 0o600, dir_fd=parent_fd)
    try:
        os.fchmod(fd, 0o600)
        data = os.urandom(32)
        if os.write(fd, data) != len(data):
            raise OSError("short secret write")
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(path: pathlib.Path, data: str, *, parent_fd: int | None = None) -> None:
    path = pathlib.Path(path)
    owned_parent = parent_fd is None
    if owned_parent:
        parent_fd = _open_secure_dir(path.parent)
    assert parent_fd is not None
    tmp_name = None
    try:
        try:
            info = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise PermissionError(f"unsafe persistent file: {path}")
        candidates = tempfile._get_candidate_names()
        for candidate in candidates:
            tmp_name = f".{path.name}.{candidate}.tmp"
            try:
                fd = os.open(tmp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                             getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=parent_fd)
                break
            except FileExistsError:
                continue
        else:
            raise FileExistsError("unable to create temporary persistence file")
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise PermissionError(f"unsafe temporary file: {path.parent / tmp_name}")
        os.replace(tmp_name, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        if owned_parent:
            os.close(parent_fd)


def save_session(session: SessionState, path: str, *, endpoint: str) -> None:
    rec = session.current
    if rec is None or not rec.verified:
        return
    payload = {"endpoint": endpoint, "machine": rec.machine, "port": rec.port,
               "state": rec.state.value, "detail": rec.detail[:300], "at": rec.at}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    p, keypath = _paths(path)
    parent_fd = _open_secure_dir(p.parent, create=True)
    try:
        if _read_regular_fd(parent_fd, keypath.name, allow_missing=True) is None:
            try:
                _create_secret_fd(parent_fd, keypath.name)
            except FileExistsError:
                pass
        key = _read_regular_fd(parent_fd, keypath.name)
        if key is None:
            raise FileNotFoundError(keypath)
        envelope = {"payload": payload, "mac": hmac.new(key, raw, hashlib.sha256).hexdigest()}
        _atomic_write(p, json.dumps(envelope, sort_keys=True), parent_fd=parent_fd)
    finally:
        os.close(parent_fd)


class FileAuthorizationStore:
    """Tamper-evident, cross-process atomic multi-capability store."""
    def __init__(self, path: str, *, clock=time.monotonic, max_ttl_s=30.0):
        self.path, self.keypath = _paths(path)
        self.lockpath = self.path.with_name(self.path.name + ".lock")
        self.clock = clock
        self.max_ttl_s = max_ttl_s

    @contextmanager
    def _locked(self):
        parent_fd = _open_secure_dir(self.path.parent, create=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(self.lockpath.name, flags, 0o600, dir_fd=parent_fd)
        except OSError as exc:
            os.close(parent_fd)
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
            try: yield parent_fd
            finally: fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        finally:
            lock.close()
            os.close(parent_fd)

    def _records(self, parent_fd):
        raw_file = _read_regular_fd(parent_fd, self.path.name, allow_missing=True)
        if raw_file is None: return []
        key = _read_regular_fd(parent_fd, self.keypath.name)
        try:
            env = json.loads(raw_file.decode("utf-8"))
            if "payloads" in env:
                records = env["payloads"]
                raw = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
            else:  # read old single-capability files during migration
                records = [env["payload"]]
                raw = json.dumps(env["payload"], sort_keys=True, separators=(",", ":")).encode()
            valid = hmac.compare_digest(env["mac"], hmac.new(key, raw, hashlib.sha256).hexdigest())
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise AuthorizationStoreIntegrityError("authorization store integrity failure") from exc
        if not valid:
            raise AuthorizationStoreIntegrityError("authorization store integrity failure")
        return records

    def _write_records(self, records, parent_fd):
        raw = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
        key = _read_regular_fd(parent_fd, self.keypath.name)
        env = {"payloads": records, "mac": hmac.new(key, raw, hashlib.sha256).hexdigest()}
        _atomic_write(self.path, json.dumps(env, sort_keys=True), parent_fd=parent_fd)

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
        with self._locked() as parent_fd:
            if _read_regular_fd(parent_fd, self.keypath.name, allow_missing=True) is None:
                try:
                    _create_secret_fd(parent_fd, self.keypath.name)
                except FileExistsError:
                    pass
            records = [p for p in self._records(parent_fd) if p.get("token") != auth.token]
            records.append(payload)
            self._write_records(records, parent_fd)

    def _read(self, token, *, consume, binding=None):
        try:
            with self._locked() as parent_fd:
                records = self._records(parent_fd)
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
                    self._write_records(records, parent_fd)
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
    parent_fd = None
    try:
        parent_fd = _open_secure_dir(p.parent)
        raw_file = _read_regular_fd(parent_fd, p.name); key = _read_regular_fd(parent_fd, keypath.name)
        if raw_file is None or key is None:
            raise FileNotFoundError(p)
        env = json.loads(raw_file.decode("utf-8")); payload = env["payload"]
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        if not hmac.compare_digest(env["mac"], hmac.new(key, raw, hashlib.sha256).hexdigest()): return session
        if payload.get("endpoint") != endpoint or payload.get("state") != "verified": return session
        if time.time() - float(payload["at"]) > max_age_s: return session
        machine = payload["machine"]
        from .machines import RACK
        if machine not in RACK or int(payload["port"]) != RACK[machine].port: return session
        session.current = SelectionRecord(machine, int(payload["port"]), SelectionState.VERIFIED,
                                          str(payload.get("detail", "")), float(payload["at"]))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, PermissionError): pass
    finally:
        if parent_fd is not None:
            os.close(parent_fd)
    return session
