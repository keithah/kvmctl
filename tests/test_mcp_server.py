import asyncio
import base64

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from conftest import FakeKvmd
from kvmctl.client import KvmClient
from kvmctl.mcp_server import build_mcp_server, client_from_env, main


def make_client(fake=None):
    import httpx
    fake = fake or FakeKvmd()
    fake.add("GET", "/api/info", body={
        "ok": True,
        "result": {"hid": {"enabled": True}, "streamer": {}, "extras": {}},
    })
    fake.add("GET", "/api/hid", body={"ok": True, "result": {"enabled": True}})
    fake.add("POST", "/api/hid/reset")
    fake.add("GET", "/api/streamer/snapshot", content=b"\xff\xd8fake-jpeg")
    c = KvmClient("https://kvm.test", verify=False)
    c._transport = httpx.MockTransport(fake.handle)
    c.set_token("t0k")
    return c, fake


def test_client_from_env_requires_url(monkeypatch):
    monkeypatch.delenv("KVMCTL_URL", raising=False)
    monkeypatch.delenv("KVMCTL_TOKEN", raising=False)
    with pytest.raises(ValueError, match="KVMCTL_URL"):
        client_from_env()


def test_client_from_env_uses_token_and_tls_settings(monkeypatch):
    monkeypatch.setenv("KVMCTL_URL", "https://kvm.test")
    monkeypatch.setenv("KVMCTL_TOKEN", "secret-token")
    monkeypatch.setenv("KVMCTL_HOST", "kvm.local")
    monkeypatch.setenv("KVMCTL_CA_BUNDLE", "/tmp/ca.pem")
    client, settings = client_from_env(return_settings=True)
    assert client.base_url == "https://kvm.test"
    assert client.token == "secret-token"
    assert client.host == "kvm.local"
    assert client.verify == "/tmp/ca.pem"
    assert settings.write_enabled is False


@pytest.mark.parametrize("value", ["true", "yes", "on", " 1 ", "2"])
def test_write_gate_requires_exact_one(value, monkeypatch):
    monkeypatch.setenv("KVMCTL_URL", "https://kvm.test")
    monkeypatch.setenv("KVMCTL_TOKEN", "secret-token")
    monkeypatch.setenv("KVMCTL_WRITE_ENABLED", value)
    _, settings = client_from_env(return_settings=True)
    assert settings.write_enabled is (value.strip() == "1")


def test_fastmcp_registers_shared_tools():
    client, _ = make_client()
    server = build_mcp_server(client=client)
    async def exercise():
        async with create_connected_server_and_client_session(server) as session:
            await session.initialize()
            tools = await session.list_tools()
            return {tool.name for tool in tools.tools}
    names = asyncio.run(exercise())
    assert names == {"capabilities", "snapshot", "ocr", "verify",
                     "host.identity.inspect", "host.graphics.inspect",
                     "service.render_access.inspect", "host.reboot", "select",
                     "hid_reset", "rearm_otg", "exec_command"}


def test_mcp_snapshot_returns_native_image_content():
    client, _ = make_client()
    server = build_mcp_server(client=client)
    async def exercise():
        async with create_connected_server_and_client_session(server) as session:
            await session.initialize()
            result = await session.call_tool("snapshot", {"preview_max_width": 640})
            return result.content[0]
    content = asyncio.run(exercise())
    assert getattr(content, "type", None) == "image"
    assert base64.b64decode(content.data) == b"\xff\xd8fake-jpeg"
    assert content.mimeType == "image/jpeg"


def test_mcp_write_tool_defaults_to_refused():
    client, _ = make_client()
    server = build_mcp_server(client=client)
    async def exercise():
        async with create_connected_server_and_client_session(server) as session:
            await session.initialize()
            return await session.call_tool("hid_reset", {})
    result = asyncio.run(exercise())
    result = result.structuredContent
    assert result["ok"] is False
    assert "policy" in result["error"].lower()
