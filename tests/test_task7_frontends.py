import asyncio
import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from conftest import FakeKvmd
from kvmctl.cli import build_parser, main
from kvmctl.client import KvmClient
from kvmctl.host import reboot_confirmation
from kvmctl.mcp_server import build_mcp_server
from kvmctl.mcp_surface import dispatch_tool
from kvmctl.policy import PolicyError
from kvmctl.semantics import SemanticSurface


class HostRunner:
    def __init__(self):
        self.calls = []
        self.identities = ["edge-01\n", OSError, "edge-01\n"]

    def __call__(self, argv):
        argv = tuple(argv)
        self.calls.append(argv)
        if argv == ("hostname",):
            value = self.identities.pop(0) if self.identities else "edge-01\n"
            if value is OSError:
                raise OSError("offline")
            return value
        if argv == ("cat", "/etc/os-release"):
            return 'NAME=Ubuntu\nVERSION_ID="24.04"\nPRETTY_NAME="Ubuntu 24.04 LTS"\n'
        if argv == ("lspci", "-nnk"):
            return ""
        if argv == ("find", "/dev/dri", "-maxdepth", "1", "-type", "c", "-printf", "%f\\n"):
            return "renderD128\n"
        if argv == ("systemctl", "is-active", "--quiet", "kvm-render"):
            return (0, "")
        if argv == ("test", "-r", "/dev/dri/renderD128"):
            return (0, "")
        if argv == ("test", "-w", "/dev/dri/renderD128"):
            return (1, "")
        if argv == ("systemctl", "reboot"):
            return (0, "")
        raise AssertionError(argv)


def make_client():
    import httpx
    fake = FakeKvmd()
    fake.add("GET", "/api/info", body={"ok": True, "result": {}})
    client = KvmClient("https://kvm.test", verify=False)
    client._transport = httpx.MockTransport(fake.handle)
    client.set_token("token")
    return client


def test_semantic_host_probes_are_structured_and_read_only():
    runner = HostRunner()
    surface = SemanticSurface(object(), host_runner=runner)
    identity = surface.host_identity_inspect()
    graphics = surface.host_graphics_inspect()
    render = surface.service_render_access_inspect()
    assert identity["operation"] == "host.identity.inspect"
    assert identity["read_only"] is True and identity["evidence"]["hostname"] == "edge-01"
    assert graphics["operation"] == "host.graphics.inspect"
    assert render["operation"] == "service.render_access.inspect"
    assert render["evidence"]["writable"] is False


def test_semantic_host_probe_requires_runner():
    with pytest.raises(PolicyError, match="configured host runner"):
        SemanticSurface(object()).host_identity_inspect()


def test_mcp_dispatch_exposes_probe_and_reboot_with_gates():
    probe_runner = HostRunner()
    context = {"client": make_client(), "host_runner": probe_runner, "write_enabled": True}
    probe = json.loads(dispatch_tool("host.identity.inspect", {}, context=context))
    assert probe["ok"] is True and probe["read_only"] is True
    context["host_runner"] = HostRunner()
    reboot = json.loads(dispatch_tool("host.reboot", {
        "target": "edge-01", "confirmation": reboot_confirmation("edge-01")
    }, context=context))
    assert reboot["operation"] == "host.reboot"
    assert reboot["ok"] is True


def test_cli_parser_and_dispatch_support_host_operations(capsys):
    parser = build_parser()
    args = parser.parse_args(["--url", "https://kvm.test", "host-identity-inspect"])
    assert args.command == "host-identity-inspect"
    runner = HostRunner()
    rc = main(["--url", "https://kvm.test", "host-identity-inspect"],
              client=make_client(), host_runner=runner)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["operation"] == "host.identity.inspect"


def test_cli_reboot_requires_yes_and_confirmation(capsys):
    runner = HostRunner()
    with pytest.raises(SystemExit):
        main(["--url", "https://kvm.test", "host-reboot", "edge-01",
              "--confirmation", reboot_confirmation("edge-01")],
             client=make_client(), host_runner=runner)
    rc = main(["--url", "https://kvm.test", "--yes", "host-reboot", "edge-01",
               "--confirmation", reboot_confirmation("edge-01")],
              client=make_client(), host_runner=runner, sleep=lambda _: None)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["operation"] == "host.reboot"


def test_fastmcp_registers_named_host_tools():
    server = build_mcp_server(client=make_client(), host_runner=HostRunner())

    async def exercise():
        async with create_connected_server_and_client_session(server) as session:
            await session.initialize()
            tools = await session.list_tools()
            return {tool.name for tool in tools.tools}

    names = asyncio.run(exercise())
    assert {"host.identity.inspect", "host.graphics.inspect",
            "service.render_access.inspect", "host.reboot"} <= names
