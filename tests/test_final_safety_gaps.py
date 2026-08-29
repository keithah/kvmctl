import json
import threading
import pytest
from kvmctl.journal import Journal
from kvmctl.session_store import FileAuthorizationStore, load_session, save_session
from kvmctl.client import effective_endpoint_identity
from kvmctl.machines import device_lock
from kvmctl.sequence_executor import SequenceExecutor
from kvmctl.results import normalize_error, operation_result
from test_sequence_executor import FakeClient, ready_session


def test_persisted_authorization_is_bound_to_verified_context_and_single_use(tmp_path):
    store = FileAuthorizationStore(str(tmp_path / 'auth.json'))
    session = ready_session()
    c1 = FakeClient(); c1.base_url = 'https://one.test'
    c2 = FakeClient(); c2.base_url = 'https://two.test'
    e1 = SequenceExecutor(c1, session, Journal(tmp_path/'j1'), authorization_store=store)
    plan = e1.plan({'target':'pve2','actions':[{'type':'release_all'}]})
    auth = e1.authorize(plan, approved=True)
    e2 = SequenceExecutor(c2, ready_session(), Journal(tmp_path/'j2'), authorization_store=store)
    assert e2.execute(auth.token).error == 'authorization invalid'
    assert e1.execute(auth.token).ok
    assert e1.execute(auth.token).error == 'authorization used'


def test_same_url_different_http_host_rejects_persisted_authorization(tmp_path):
    store = FileAuthorizationStore(str(tmp_path / 'auth.json'))
    c1 = FakeClient(); c1.base_url = c2_url = 'https://shared.test:8443/api'; c1.host = 'kvm-a.example'
    c2 = FakeClient(); c2.base_url = c2_url; c2.host = 'kvm-b.example'
    e1 = SequenceExecutor(c1, ready_session(), Journal(tmp_path/'j1'), authorization_store=store)
    auth = e1.authorize(e1.plan({'target':'pve2','actions':[{'type':'release_all'}]}), approved=True)
    e2 = SequenceExecutor(c2, ready_session(), Journal(tmp_path/'j2'), authorization_store=store)
    assert e2.execute(auth.token).error == 'authorization invalid'
    assert e1.execute(auth.token).ok


def test_same_url_matching_http_host_accepts_persisted_authorization(tmp_path):
    store = FileAuthorizationStore(str(tmp_path / 'auth.json'))
    c1 = FakeClient(); c1.base_url = 'https://shared.test:8443/api'; c1.host = 'kvm.example'
    c2 = FakeClient(); c2.base_url = c1.base_url; c2.host = 'kvm.example'
    e1 = SequenceExecutor(c1, ready_session(), Journal(tmp_path/'j1'), authorization_store=store)
    auth = e1.authorize(e1.plan({'target':'pve2','actions':[{'type':'release_all'}]}), approved=True)
    e2 = SequenceExecutor(c2, ready_session(), Journal(tmp_path/'j2'), authorization_store=store)
    assert e2.execute(auth.token).ok


def test_session_persistence_binds_effective_http_host(tmp_path):
    path = str(tmp_path / 'session.json')
    session = ready_session()
    save_session(session, path, endpoint=effective_endpoint_identity('https://shared.test:8443/api', 'kvm-a.example'))
    assert load_session(path, endpoint=effective_endpoint_identity('https://shared.test:8443/api', 'kvm-a.example')).current is not None
    assert load_session(path, endpoint=effective_endpoint_identity('https://shared.test:8443/api', 'kvm-b.example')).current is None


@pytest.mark.parametrize("host", ["kvm.example", "192.0.2.10:8443", "[2001:db8::10]:8443", "[::1]"])
def test_effective_endpoint_identity_accepts_valid_http_authorities(host):
    identity = effective_endpoint_identity("https://shared.test:443/api", host)
    assert identity.endswith("|host=" + host.lower())


@pytest.mark.parametrize("host", ["example.com@evil", "foo bar", "[::1]:99999", "[::1", "::1", "example.com:", ":8080", "[]", "example.com/path", "example..com"])
def test_effective_endpoint_identity_rejects_invalid_http_authorities(host):
    with pytest.raises(ValueError):
        effective_endpoint_identity("https://shared.test:443/api", host)


def test_persistence_rejects_symlinked_parent_and_lock(tmp_path):
    real = tmp_path / "real"; real.mkdir()
    parent_link = tmp_path / "parent-link"; parent_link.symlink_to(real, target_is_directory=True)
    with pytest.raises(PermissionError):
        save_session(ready_session(), str(parent_link / "session.json"), endpoint="https://x:443|host=x")
    store = FileAuthorizationStore(str(tmp_path / "auth.json"))
    (tmp_path / "auth.json.lock").symlink_to(real / "outside.lock")
    with pytest.raises(PermissionError):
        with store._locked():
            pass


def test_device_lock_rejects_symlinked_lock_file(tmp_path, monkeypatch):
    lock_dir = tmp_path / "locks"; lock_dir.mkdir()
    monkeypatch.setenv("KVMCTL_LOCK_DIR", str(lock_dir))
    import hashlib
    name = hashlib.sha256(b"symlinked").hexdigest() + ".lock"
    (lock_dir / name).symlink_to(tmp_path / "outside.lock")
    assert not device_lock("symlinked").acquire(blocking=False)


def test_persisted_authorization_take_is_process_safe(tmp_path):
    store = FileAuthorizationStore(str(tmp_path / 'auth.json'))
    e = SequenceExecutor(FakeClient(), ready_session(), Journal(tmp_path/'j'), authorization_store=store)
    auth = e.authorize(e.plan({'target':'pve2','actions':[{'type':'release_all'}]}), approved=True)
    results=[]
    threads=[threading.Thread(target=lambda: results.append(store.take(auth.token))) for _ in range(2)]
    for t in threads:t.start()
    for t in threads:t.join()
    assert sum(x is not None for x in results) == 1


def test_screen_assertion_timeout_and_oversize_stop_hid(tmp_path):
    class Slow(FakeClient):
        base_url='https://screen.test'
        def snapshot_jpeg(self): raise TimeoutError('slow')
    e=SequenceExecutor(Slow(), ready_session(), Journal(tmp_path/'j'), clock=lambda:0.0, sleep=lambda _:None)
    p=e.plan({'target':'pve2','actions':[{'type':'assert_screen','contains':'ok'},{'type':'key','value':'Enter'}], 'max_duration_ms':100})
    r=e.execute(e.authorize(p, approved=True).token)
    assert not r.ok and 'screen' in r.error


def test_rejection_paths_journal_exact_binding(tmp_path):
    e=SequenceExecutor(FakeClient(), ready_session(), Journal(tmp_path/'j'), clock=lambda:0.0, sleep=lambda _:None)
    p=e.plan({'target':'pve2','actions':[{'type':'release_all'}]})
    a=e.authorize(p, approved=True)
    assert e.execute(a.token, expected_plan={'target':'pve2','actions':[{'type':'wait','duration_ms':1}]}).error == 'plan mismatch'
    rows=[json.loads(x) for x in (tmp_path/'j').read_text().splitlines()]
    assert rows[-1]['transition']=='aborted'
    assert rows[-1]['target']=='pve2' and rows[-1]['plan_hash']==a.plan_hash
    assert 'timestamp' in rows[-1] and 'duration_ms' in rows[-1] and 'target_verification' in rows[-1]


def test_error_normalization_redacts_exception_and_nested_sensitive_values(tmp_path):
    secret = "backend-password=do-not-leak"
    assert secret not in normalize_error(ValueError(secret))
    result = operation_result(
        operation="sequence", transport="kvm", read_only=False, ok=False,
        error={"code": secret, "message": secret,
               "nested": {"password": secret, "safe": "visible"}},
    )
    serialized = json.dumps(result)
    assert secret not in serialized
    assert result["error"] == {"code": "operation failed", "retryable": False,
                                "requires_human": False}

    journal = Journal(tmp_path / "redacted.jsonl")
    e = SequenceExecutor(FakeClient(), ready_session(), journal)
    e.reject(secret, target="pve2")
    assert secret not in (tmp_path / "redacted.jsonl").read_text()
