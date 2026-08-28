"""Installable stdio MCP adapter for the safe semantic surface."""
from __future__ import annotations

import json
import time
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent

from kvmctl.config import Settings, client_from_settings, settings_from_env
from kvmctl.mcp_surface import dispatch_tool
from kvmctl.semantics import SemanticSurface


def client_from_env(*, return_settings: bool = False):
    settings = settings_from_env()
    client = client_from_settings(settings)
    return (client, settings) if return_settings else client


def build_mcp_server(*, client=None, settings: Settings | None = None,
                     host_runner=None) -> FastMCP:
    if client is None:
        client, loaded = client_from_env(return_settings=True)
        settings = settings or loaded
    settings = settings or Settings(url="injected", write_enabled=False)
    server = FastMCP("kvmctl", instructions=(
        "Safe KVMD semantic operations. Read-only by default; writes require "
        "KVMCTL_WRITE_ENABLED and remain subject to operation policy."
    ))
    session = None

    def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raw = dispatch_tool(name, arguments, context={
            "client": client,
            "session": session,
            "write_enabled": settings.write_enabled,
            "ssh_allowlist": settings.ssh_allowlist,
            "sleep": time.sleep,
            "host_runner": host_runner,
        })
        return json.loads(raw)

    @server.tool(name="capabilities", description="Report device capabilities and identity.")
    def capabilities() -> dict[str, Any]:
        return call("capabilities", {})

    @server.tool(name="snapshot", description="Capture the current screen as native JPEG image content.")
    def snapshot(preview_max_width: int = 1280) -> ImageContent:
        surface = SemanticSurface(client, session=session, write_enabled=settings.write_enabled,
                                  ssh_allowlist=settings.ssh_allowlist)
        data = surface.snapshot(preview_max_width=preview_max_width)
        encoded = data.get("evidence", {}).get("data_base64")
        if not encoded:
            raise ValueError("snapshot did not return image data")
        return ImageContent(type="image", data=encoded, mimeType="image/jpeg")

    @server.tool(name="ocr", description="OCR the current screen or a provided base64 image.")
    def ocr(image_b64: str | None = None) -> dict[str, Any]:
        args = {"image_b64": image_b64} if image_b64 is not None else {}
        return call("ocr", args)

    @server.tool(name="verify", description="Verify which machine is on screen.")
    def verify(machine: str, policy: str = "none") -> dict[str, Any]:
        return call("verify", {"machine": machine, "policy": policy})

    @server.tool(name="host.identity.inspect", description="Inspect configured host identity.")
    def host_identity_inspect() -> dict[str, Any]:
        return call("host.identity.inspect", {})

    @server.tool(name="host.graphics.inspect", description="Inspect configured host graphics and DRM nodes.")
    def host_graphics_inspect() -> dict[str, Any]:
        return call("host.graphics.inspect", {})

    @server.tool(name="service.render_access.inspect", description="Inspect render service and device access.")
    def service_render_access_inspect() -> dict[str, Any]:
        return call("service.render_access.inspect", {})

    @server.tool(name="select", description="Switch KVM port; requires explicit KVM transport and write authorization.")
    def select(machine: str, transport: str = "", verify_policy: str = "none",
               rearm: bool = True, settle_s: float = 5.0) -> dict[str, Any]:
        return call("select", {"machine": machine, "transport": transport,
                                "verify_policy": verify_policy, "rearm": rearm, "settle_s": settle_s})

    @server.tool(name="hid_reset", description="Reset the HID subsystem; requires write authorization.")
    def hid_reset(transport: str = "") -> dict[str, Any]:
        return call("hid_reset", {"transport": transport})

    @server.tool(name="rearm_otg", description="Re-arm OTG; requires write authorization.")
    def rearm_otg(transport: str = "") -> dict[str, Any]:
        return call("rearm_otg", {"transport": transport})

    @server.tool(name="exec_command", description="Run an allowlisted SSH command; requires explicit SSH transport.")
    def exec_command(command: str, transport: str = "") -> dict[str, Any]:
        return call("exec_command", {"command": command, "transport": transport})

    @server.tool(name="host.reboot", description="Reboot a named host; requires write authorization and target-bound confirmation.")
    def host_reboot(target: str, confirmation: str, attempts: int = 5,
                    delay: float = 1.0) -> dict[str, Any]:
        return call("host.reboot", {"target": target, "confirmation": confirmation,
                                     "attempts": attempts, "delay": delay})

    return server


def main() -> None:
    build_mcp_server().run(transport="stdio")


if __name__ == "__main__":
    main()
