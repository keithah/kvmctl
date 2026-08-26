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
    out = json.loads(dispatch_tool("select", {"machine": "pve2"}, context={"client": c}))
    assert out["ok"] is False
    assert "sleep" in out["error"]


def test_mcp_select_with_test_mode_allows_no_sleep():
    c, fake = make_client()
    fake.add("POST", "/api/hid/events/send_key")
    ctx = {"client": c, "test_mode": True, "write_enabled": True}
    # rearm disabled so no otg route needed; verify none via policy
    out = json.loads(dispatch_tool(
        "select", {"machine": "pve2", "rearm": False,
                   "verify_policy": "none", "settle_s": 0},
        context=ctx))
    events = [(r["params"].get("key"), r["params"].get("state"))
              for r in fake.requests if r["path"] == "/api/hid/events/send_key"]
    assert events == [("ControlRight", "down"), ("ControlRight", "up"),
                      ("ControlRight", "down"), ("ControlRight", "up"),
                      ("Digit2", "down"), ("Digit2", "up"),
                      ("Enter", "down"), ("Enter", "up")]
    assert out["ok"] is True


def test_mcp_rearm_otg_without_sleep_is_refused():
    c, _ = make_client()
    out = json.loads(dispatch_tool("rearm_otg", {}, context={"client": c}))
    assert out["ok"] is False


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
            break
        if secret in (root / f).read_text(errors="replace"):
            bad.append(f)
    assert bad == []
