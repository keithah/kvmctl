import os, stat
from kvmctl.session_store import FileAuthorizationStore
from kvmctl.sequence_executor import SequenceAuthorization
from kvmctl.sequences import SequencePlan


def auth(token):
    p = SequencePlan.from_mapping({'target':'pve2','actions':[{'type':'release_all'}]})
    return SequenceAuthorization(p, 'pve2', 'sha256:x', 999, token=token, binding='b')


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
