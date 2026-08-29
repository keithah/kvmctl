"""Tests for the CLI and MCP-facing registries."""
import json

import pytest

from conftest import FakeKvmd
from kvmctl.client import KvmClient
from kvmctl.cli import build_parser, main
from kvmctl.mcp_surface import TOOL_SPEC, dispatch_tool


def make_client(fake=None):
    import httpx
    fake = fake or FakeKvmd()
    fake.add("GET", "/api/info", body={
        "ok": True,
        "result": {"hid": {"enabled": True}, "streamer": {}, "extras": {}},
    })
    fake.add("POST", "/api/hid/reset")
    fake.add("POST", "/api/hid/events/send_key")
    c = KvmClient("https://kvm.test", verify=False)
    c._transport = httpx.MockTransport(fake.handle)
    c.set_token("t0k")
    return c, fake


def test_cli_capabilities_json(capsys):
    c, _ = make_client()
    rc = main(["--url", "https://kvm.test", "capabilities"], client=c)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["operation"] == "capabilities"
    assert out["evidence"]["caps"]["hid"] is True


def test_cli_passes_virtual_host_to_client():
    parser = build_parser()
    args = parser.parse_args(["--url", "https://kvm.test", "--host", "glkvm.local", "capabilities"])
    assert args.host == "glkvm.local"


def test_cli_write_op_requires_yes_flag():
    c, _ = make_client()
    with pytest.raises(SystemExit):
        main(["--url", "https://kvm.test", "hid-reset"], client=c)


def test_cli_hid_reset_with_flag():
    c, _ = make_client()
    rc = main(["--url", "https://kvm.test", "--yes", "hid-reset"], client=c)
    assert rc == 0


def test_cli_select_requires_transport():
    c, _ = make_client()
    with pytest.raises(SystemExit):
        main(["--url", "https://kvm.test", "--yes", "select", "pve2",
              "--verify-policy", "none", "--no-rearm", "--settle", "0"],
             client=c)


def test_cli_select_with_explicit_kvm_transport():
    c, _ = make_client()
    rc = main(["--url", "https://kvm.test", "--yes", "--transport", "kvm",
               "select", "pve2", "--verify-policy", "none",
               "--no-rearm", "--settle", "0"], client=c)
    assert rc == 0


def test_cli_select_rejects_ssh_transport(capsys):
    c, _ = make_client()
    with pytest.raises(SystemExit):
        main(["--url", "https://kvm.test", "--yes", "--transport", "ssh",
              "select", "pve2", "--verify-policy", "none", "--no-rearm",
              "--settle", "0"], client=c, sleep=lambda _: None)
    assert "requires --transport kvm" in capsys.readouterr().err


def test_mcp_spec_declares_readonly_and_gates():
    for tool in TOOL_SPEC:
        if tool["name"] in ("capabilities", "snapshot", "ocr", "verify",
                             "host.identity.inspect", "host.graphics.inspect",
                             "service.render_access.inspect", "kvm_status",
                             "kvm_screenshot_to_file", "kvm_ocr_screenshot",
                             "kvm_sequence_plan", "kvm_workflow_list", "kvm_workflow_inspect"):
            assert tool.get("read_only") is True
        else:
            assert tool.get("write_gate") is True
    names = {t["name"] for t in TOOL_SPEC}
    assert names == {"capabilities", "snapshot", "ocr", "verify",
                     "host.identity.inspect", "host.graphics.inspect",
                     "service.render_access.inspect", "host.reboot", "select",
                     "hid_reset", "rearm_otg", "exec_command",
                     "kvm_send_text", "kvm_send_keys", "kvm_hold_key",
                     "kvm_release_all", "kvm_mouse_move", "kvm_mouse_move_pct",
                     "kvm_mouse_click", "kvm_mouse_scroll", "kvm_status",
                     "kvm_screenshot_to_file", "kvm_ocr_screenshot", "kvm_ocr_click",
                     "kvm_sequence_plan", "kvm_sequence_authorize", "kvm_sequence_execute",
                     "kvm_workflow_list", "kvm_workflow_inspect", "kvm_workflow_execute"}


def test_mcp_dispatch_enforces_policy():
    c, _ = make_client()
    ctx = {"client": c}
    out = json.loads(dispatch_tool("capabilities", {}, context=ctx))
    assert out["operation"] == "capabilities"
    # write op without gate -> error payload, not exception
    out = json.loads(dispatch_tool("hid_reset", {}, context=ctx))
    assert out["ok"] is False and "policy" in out["error"].lower()


def test_mcp_dispatch_exec_requires_ssh_transport():
    c, _ = make_client()
    ctx = {"client": c,
           "ssh_runner": lambda cmd: {"rc": 0, "stdout": ""},
           "ssh_allowlist": ("uptime",)}
    out = json.loads(dispatch_tool("exec_command", {"command": "uptime"},
                                   context=ctx))
    assert out["ok"] is False  # no transport given


def test_mcp_dispatch_select_requires_kvm_transport():
    c, _ = make_client()
    out = json.loads(dispatch_tool(
        "select", {"machine": "pve2", "transport": "ssh"},
        context={"client": c, "write_enabled": True, "test_mode": True},
    ))
    assert out["ok"] is False
    assert "transport='kvm'" in out["error"]
