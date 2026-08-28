import asyncio
import base64

import httpx
from mcp.shared.memory import create_connected_server_and_client_session

from conftest import FakeKvmd
from kvmctl.client import KvmClient
from kvmctl.mcp_server import build_mcp_server


def build(fake):
    c = KvmClient("https://kvm.test", verify=False)
    c._transport = httpx.MockTransport(fake.handle)
    c.set_token("t0k")
    return c


def test_client_keyboard_and_mouse_helpers_emit_kvmd_requests(fake):
    for path in (
        "/api/hid/events/send_key",
        "/api/hid/events/send_shortcut",
        "/api/hid/events/send_mouse_move",
        "/api/hid/events/send_mouse_button",
        "/api/hid/events/send_mouse_wheel",
    ):
        fake.add("POST", path)
    c = build(fake)

    c.send_keys("ControlLeft,AltLeft,Delete")
    c.mouse_move(12, -34)
    c.mouse_button("left", True)
    c.mouse_scroll(3, -4)

    requests = fake.requests
    shortcut = next(r for r in requests if r["path"] == "/api/hid/events/send_shortcut")
    assert shortcut["params"]["keys"] == "ControlLeft,AltLeft,Delete"
    move = next(r for r in requests if r["path"] == "/api/hid/events/send_mouse_move")
    assert move["params"] == {"to_x": "12", "to_y": "-34"}
    button = next(r for r in requests if r["path"] == "/api/hid/events/send_mouse_button")
    assert button["params"] == {"button": "left", "state": "true"}
    wheel = next(r for r in requests if r["path"] == "/api/hid/events/send_mouse_wheel")
    assert wheel["params"] == {"delta_x": "3", "delta_y": "-4"}


def test_mcp_registers_reference_control_tools(fake):
    fake.add("GET", "/api/streamer/snapshot", content=b"\xff\xd8fake-jpeg")
    server = build_mcp_server(client=build(fake))

    async def exercise():
        async with create_connected_server_and_client_session(server) as session:
            await session.initialize()
            tools = await session.list_tools()
            return {tool.name for tool in tools.tools}

    names = asyncio.run(exercise())
    assert {
        "kvm_send_text", "kvm_send_keys", "kvm_hold_key", "kvm_release_all",
        "kvm_mouse_move", "kvm_mouse_move_pct", "kvm_mouse_click", "kvm_mouse_scroll",
        "kvm_screenshot_to_file", "kvm_ocr_screenshot", "kvm_ocr_click", "kvm_status",
    } <= names


def test_mcp_control_tools_require_write_authorization(fake):
    for path in ("/api/hid/events/send_key", "/api/hid/events/send_shortcut"):
        fake.add("POST", path)
    server = build_mcp_server(client=build(fake))

    async def exercise():
        async with create_connected_server_and_client_session(server) as session:
            await session.initialize()
            return await session.call_tool("kvm_send_text", {"text": "ls"})

    result = asyncio.run(exercise()).structuredContent
    assert result["ok"] is False
    assert "write" in result["error"].lower()
    assert not [r for r in fake.requests if r["path"].startswith("/api/hid/events")]
