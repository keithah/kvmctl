"""Regression tests for review fixes (t_03a568fe)."""
import json

import httpx
import pytest

from conftest import FakeKvmd
from kvmctl.client import KvmClient
from kvmctl.switch import SwitchProfile, SwitchProtocolError, execute_switch
from kvmctl.mcp_surface import dispatch_tool


def make_client(fake=None):
    fake = fake or FakeKvmd()
    fake.add("GET", "/api/info", body={
        "ok": True,
        "result": {"hid": {"enabled": True}, "streamer": {}, "extras": {}},
    })
    c = KvmClient("https://kvm.test", verify=False)
    c._transport = httpx.MockTransport(fake.handle)
    c.set_token("t0k")
    return c, fake


def test_held_key_rejects_unpaired_press():
    p = SwitchProfile(
        name="bad", min_port=1, max_port=4,
        sequence=(("ControlRight", "press"), ("Enter", "tap")),
        hold_ms=0.12,
    )
    with pytest.raises(SwitchProtocolError):
        execute_switch(make_client()[0], p, 2, sleep=lambda s: None)


def test_held_key_rejects_consecutive_press():
    p = SwitchProfile(
        name="bad2", min_port=1, max_port=4,
        sequence=(("ControlRight", "press"), ("Digit2", "tap")),
        hold_ms=0.12,
    )
    with pytest.raises(SwitchProtocolError):
        execute_switch(make_client()[0], p, 2, sleep=lambda s: None)


def test_held_key_rejects_dangling_release():
    p = SwitchProfile(
        name="bad3", min_port=1, max_port=4,
        sequence=(("Enter", "tap"), ("Enter", "release")),
        hold_ms=0.12,
    )
    with pytest.raises(SwitchProtocolError):
        execute_switch(make_client()[0], p, 1, sleep=lambda s: None)


def test_mcp_select_without_sleep_is_refused():
    c, _ = make_client()
    out = json.loads(dispatch_tool("select", {"machine": "pve2", "transport": "kvm"}, context={"client": c}))
    assert out["ok"] is False
    assert "sleep" in out["error"]


def test_mcp_select_with_test_mode_allows_no_sleep():
    c, fake = make_client()
    fake.add("POST", "/api/hid/events/send_key")
    ctx = {"client": c, "test_mode": True, "write_enabled": True}
    # rearm disabled so no otg route needed; verify none via policy
    out = json.loads(dispatch_tool(
        "select", {"machine": "pve2", "transport": "kvm", "rearm": False,
                   "verify_policy": "none", "settle_s": 0},
        context=ctx))
    events = [(r["params"].get("key"), r["params"].get("state"))
              for r in fake.requests if r["path"] == "/api/hid/events/send_key"]
    assert events == [("ControlRight", "true"), ("ControlRight", "false"),
                           ("ControlRight", "true"), ("ControlRight", "false"),
                      ("Digit2", "true"), ("Digit2", "false"),
                      ("Enter", "true"), ("Enter", "false")]
    assert out["ok"] is True


def test_mcp_rearm_otg_without_sleep_is_refused():
    c, _ = make_client()
    out = json.loads(dispatch_tool("rearm_otg", {}, context={"client": c}))
    assert out["ok"] is False


def test_device_lock_release_closes_file_when_unlock_fails(monkeypatch, tmp_path):
    import fcntl
    from kvmctl.machines import DeviceLock
    monkeypatch.setenv("KVMCTL_LOCK_DIR", str(tmp_path))
    lock = DeviceLock("release-test")
    assert lock.acquire()
    original = fcntl.flock
    def fail_unlock(fd, flags):
        if flags == fcntl.LOCK_UN:
            raise OSError("unlock failed")
        return original(fd, flags)
    monkeypatch.setattr(fcntl, "flock", fail_unlock)
    with pytest.raises(OSError):
        lock.release()
    assert lock._file.closed


def test_workflow_inspection_redacts_every_text_action():
    from kvmctl.workflows import WorkflowRepository
    repo = WorkflowRepository.from_mappings([{"name": "all-text", "target": "pve2", "steps": [
        {"type": "text", "value": "ordinary text"},
        {"type": "text", "value": "tokenless but still private"},
    ]}])
    inspected = repo.inspect("all-text")
    assert [a["value"] for a in inspected["actions"]] == ["[REDACTED]", "[REDACTED]"]


def test_typed_sequence_plan_is_canonicalized_before_hashing():
    from kvmctl.sequences import Action, SequencePlan, canonicalize_plan
    plan = SequencePlan(" pve2 ", (Action("release_all"),))
    assert canonicalize_plan(plan)["target"] == "pve2"


def test_expired_authorization_records_are_pruned(tmp_path):
    from kvmctl.session_store import FileAuthorizationStore
    from test_sequence_executor import FakeClient, ready_session
    from kvmctl.journal import Journal
    from kvmctl.sequence_executor import SequenceExecutor
    now = [0.0]
    store = FileAuthorizationStore(str(tmp_path / "auth.json"), clock=lambda: now[0])
    ex = SequenceExecutor(FakeClient(), ready_session(), Journal(tmp_path / "j"), clock=lambda: now[0])
    auth = ex.authorize(ex.plan({"target": "pve2", "actions": [{"type": "release_all"}]}), approved=True)
    store.put(auth)
    now[0] = 31.0
    assert store.peek(auth.token) is None
    with store._locked() as parent_fd:
        assert store._records(parent_fd) == []


def test_no_password_literal_in_repo_sources():
    """The machine password must appear nowhere in tracked text files."""
    import pathlib
    import subprocess
    root = pathlib.Path(__file__).resolve().parent.parent
    files = subprocess.run(
        ["git", "-C", str(root), "ls-files"],
        capture_output=True, text=True).stdout.split()
    bad = []
    for f in files:
        if not f.endswith((".py", ".md", ".json", ".toml", ".txt", ".cfg")):
            continue
        secret = __import__("os").environ.get("KVMCTL_MACHINE_PASSWORD")
        if not secret:
            pytest.skip("KVMCTL_MACHINE_PASSWORD is not configured for this check")
        if secret in (root / f).read_text(errors="replace"):
            bad.append(f)
    assert bad == []
