import hashlib, hmac, json, os, stat, time
import pytest
from kvmctl.session_store import FileAuthorizationStore, AuthorizationStoreIntegrityError
from kvmctl.sequence_executor import SequenceAuthorization
from kvmctl.sequences import SequencePlan, plan_hash


def auth(token):
    p = SequencePlan.from_mapping({'target':'pve2','actions':[{'type':'release_all'}]})
    return SequenceAuthorization(p, 'pve2', plan_hash(p), time.monotonic() + 10, token=token, binding='b')


def test_store_preserves_multiple_capabilities_and_consumes_once(tmp_path):
    s = FileAuthorizationStore(str(tmp_path/'auth'))
    s.put(auth('one')); s.put(auth('two'))
    assert s.peek('one', binding='b').token == 'one'
    assert s.peek('two', binding='b').token == 'two'
    assert s.take('one', binding='b').token == 'one'
    assert s.take('one', binding='b') is None
    assert s.take('two', binding='b').token == 'two'


def test_store_rejects_unsafe_existing_files(tmp_path):
    path = tmp_path/'auth'; path.write_text('{}'); os.chmod(path, 0o644)
    s = FileAuthorizationStore(str(path))
    try: s.put(auth('one'))
    except PermissionError: pass
    else: raise AssertionError('unsafe file accepted')


def test_atomic_staging_uses_unique_exclusive_regular_file(tmp_path):
    from kvmctl.session_store import _atomic_write
    path = tmp_path / "state"
    _atomic_write(path, "first")
    _atomic_write(path, "second")
    assert path.read_text() == "second"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(tmp_path.glob("state.tmp"))


def test_atomic_write_rejects_symlink_target(tmp_path):
    from kvmctl.session_store import _atomic_write
    target = tmp_path / "real"
    target.write_text("keep")
    link = tmp_path / "state"
    link.symlink_to(target)
    try:
        _atomic_write(link, "must-not-follow")
    except PermissionError:
        pass
    else:
        raise AssertionError("symlink target accepted")
    assert target.read_text() == "keep"


def _rewrite_payload(path, keypath, update):
    envelope = json.loads(path.read_text())
    payloads = envelope["payloads"]
    update(payloads[0])
    raw = json.dumps(payloads, sort_keys=True, separators=(",", ":")).encode()
    envelope["mac"] = hmac.new(keypath.read_bytes(), raw, hashlib.sha256).hexdigest()
    path.write_text(json.dumps(envelope, sort_keys=True))


def test_mac_valid_malformed_capability_is_not_consumed_or_rewritten(tmp_path):
    path = tmp_path / "auth"
    store = FileAuthorizationStore(str(path))
    store.put(auth("keep"))
    original = path.read_bytes()
    _rewrite_payload(path, path.with_name("auth.key"), lambda p: p.update(plan_hash=123))
    malformed = path.read_bytes()
    with pytest.raises(AuthorizationStoreIntegrityError):
        store.take("keep", binding="b")
    assert path.read_bytes() == malformed
    assert path.read_bytes() != original


@pytest.mark.parametrize("expiry", [float("nan"), float("inf"), float("-inf")])
def test_mac_valid_nonfinite_expiry_is_not_consumed_or_rewritten(tmp_path, expiry):
    path = tmp_path / "auth"
    store = FileAuthorizationStore(str(path))
    store.put(auth("keep"))
    _rewrite_payload(path, path.with_name("auth.key"), lambda p: p.update(expires_at=expiry))
    original = path.read_bytes()
    with pytest.raises(AuthorizationStoreIntegrityError):
        store.take("keep", binding="b")
    assert path.read_bytes() == original


@pytest.mark.parametrize("field,value", [
    ("used", 0), ("used", None), ("used", "false"),
    ("workflow_revision", 7), ("binding", 7), ("session_id", "7"),
])
def test_mac_valid_schema_corruption_is_not_consumed_or_rewritten(tmp_path, field, value):
    path = tmp_path / "auth"
    store = FileAuthorizationStore(str(path))
    store.put(auth("keep"))
    _rewrite_payload(path, path.with_name("auth.key"), lambda p: p.update({field: value}))
    original = path.read_bytes()
    with pytest.raises(AuthorizationStoreIntegrityError):
        store.take("keep", binding="b")
    assert path.read_bytes() == original


def test_mac_valid_distant_expiry_is_not_consumed_or_rewritten(tmp_path):
    path = tmp_path / "auth"
    store = FileAuthorizationStore(str(path), clock=lambda: 100.0, max_ttl_s=30.0)
    initial = auth("keep")
    object.__setattr__(initial, "expires_at", 110.0)
    store.put(initial)
    _rewrite_payload(path, path.with_name("auth.key"), lambda p: p.update(expires_at=131.0))
    original = path.read_bytes()
    with pytest.raises(AuthorizationStoreIntegrityError):
        store.take("keep", binding="b")
    assert path.read_bytes() == original


def test_tampered_store_fails_closed_and_does_not_overwrite(tmp_path):
    from kvmctl.session_store import AuthorizationStoreIntegrityError
    path = tmp_path / "auth"
    s = FileAuthorizationStore(str(path))
    s.put(auth("keep"))
    original = path.read_bytes()
    path.write_bytes(original.replace(b"keep", b"evil"))
    with __import__("pytest").raises(AuthorizationStoreIntegrityError):
        s.put(auth("new"))
    assert b"new" not in path.read_bytes()


def test_tampered_store_read_propagates_integrity_error_and_missing_is_none(tmp_path):
    path = tmp_path / "auth"
    store = FileAuthorizationStore(str(path))
    store.put(auth("keep"))
    path.write_bytes(path.read_bytes().replace(b"keep", b"evil"))
    with pytest.raises(AuthorizationStoreIntegrityError):
        store.peek("missing", binding="b")

    missing_store = FileAuthorizationStore(str(tmp_path / "missing"))
    assert missing_store.peek("missing", binding="b") is None


def test_non_integrity_oserror_still_fails_closed(tmp_path, monkeypatch):
    store = FileAuthorizationStore(str(tmp_path / "auth"))
    monkeypatch.setattr(store, "_records", lambda: (_ for _ in ()).throw(OSError("unreadable")))
    assert store.peek("missing", binding="b") is None


def test_lock_acquisition_oserror_fails_closed(tmp_path, monkeypatch):
    store = FileAuthorizationStore(str(tmp_path / "auth"))
    monkeypatch.setattr(store, "_locked", lambda: (_ for _ in ()).throw(OSError("lock unavailable")))
    assert store.peek("missing", binding="b") is None


def test_lock_validation_oserror_fails_closed(tmp_path, monkeypatch):
    store = FileAuthorizationStore(str(tmp_path / "auth"))
    monkeypatch.setattr("kvmctl.session_store._secure_file", lambda *a, **k: (_ for _ in ()).throw(OSError("validation unavailable")))
    assert store.peek("missing", binding="b") is None


def test_integrity_error_from_lock_path_is_not_swallowed(tmp_path, monkeypatch):
    store = FileAuthorizationStore(str(tmp_path / "auth"))
    monkeypatch.setattr(store, "_locked", lambda: (_ for _ in ()).throw(AuthorizationStoreIntegrityError("corrupt lock")))
    with pytest.raises(AuthorizationStoreIntegrityError):
        store.peek("missing", binding="b")


def test_put_rejects_malformed_capability_without_modifying_store(tmp_path):
    path = tmp_path / "auth"
    store = FileAuthorizationStore(str(path))
    store.put(auth("keep"))
    original = path.read_bytes()
    bad = auth("bad")
    object.__setattr__(bad, "plan_hash", "sha256:wrong")
    with pytest.raises(ValueError):
        store.put(bad)
    assert path.read_bytes() == original


@pytest.mark.parametrize("expiry", [float("nan"), float("inf"), float("-inf"), 1000.0])
def test_put_rejects_invalid_expiry_without_modifying_store(tmp_path, expiry):
    path = tmp_path / "auth"
    store = FileAuthorizationStore(str(path), max_ttl_s=30.0)
    store.put(auth("keep"))
    original = path.read_bytes()
    bad = auth("bad")
    object.__setattr__(bad, "expires_at", expiry)
    with pytest.raises(ValueError):
        store.put(bad)
    assert path.read_bytes() == original
