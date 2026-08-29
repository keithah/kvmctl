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
from kvmctl.workflows import WorkflowRepository
from kvmctl.machines import SessionState


def client_from_env(*, return_settings: bool = False):
    settings = settings_from_env()
    client = client_from_settings(settings)
    return (client, settings) if return_settings else client


def build_mcp_server(*, client=None, settings: Settings | None = None,
                     host_runner=None, workflow_repository=None,
                     sequence_executor=None, journal=None) -> FastMCP:
    if client is None:
        client, loaded = client_from_env(return_settings=True)
        settings = settings or loaded
    settings = settings or Settings(url="injected", write_enabled=False)
    server = FastMCP("kvmctl", instructions=(
        "Safe KVMD semantic operations. Read-only by default; writes require "
        "KVMCTL_WRITE_ENABLED and remain subject to operation policy."
    ))
    session = SessionState()

    def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raw = dispatch_tool(name, arguments, context={
            "client": client,
            "session": session,
            "write_enabled": settings.write_enabled,
            "ssh_allowlist": settings.ssh_allowlist,
            "sleep": time.sleep,
            "host_runner": host_runner,
            "workflow_repository": workflow_repository or WorkflowRepository(()),
            "sequence_executor": sequence_executor,
            "journal": journal,
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

    @server.tool(name="kvm_send_text", description="Type text through the selected KVM target; requires write authorization.")
    def kvm_send_text(text: str, interval_s: float = 0.01) -> dict[str, Any]:
        return call("kvm_send_text", {"text": text, "interval_s": interval_s})

    @server.tool(name="kvm_send_keys", description="Send a validated key chord; requires write authorization.")
    def kvm_send_keys(combo: str) -> dict[str, Any]:
        return call("kvm_send_keys", {"combo": combo})

    @server.tool(name="kvm_hold_key", description="Hold a key for a bounded duration; requires write authorization.")
    def kvm_hold_key(key: str, duration_ms: int) -> dict[str, Any]:
        return call("kvm_hold_key", {"key": key, "duration_ms": duration_ms})

    @server.tool(name="kvm_release_all", description="Release all keys tracked as held; requires write authorization.")
    def kvm_release_all() -> dict[str, Any]:
        return call("kvm_release_all", {})

    @server.tool(name="kvm_mouse_move", description="Move the selected target mouse in normalized coordinates; requires write authorization.")
    def kvm_mouse_move(x: int, y: int) -> dict[str, Any]:
        return call("kvm_mouse_move", {"x": x, "y": y})

    @server.tool(name="kvm_mouse_move_pct", description="Move the selected target mouse by screen percentage; requires write authorization.")
    def kvm_mouse_move_pct(x_pct: float, y_pct: float) -> dict[str, Any]:
        return call("kvm_mouse_move_pct", {"x_pct": x_pct, "y_pct": y_pct})

    @server.tool(name="kvm_mouse_click", description="Click the selected target mouse; requires write authorization.")
    def kvm_mouse_click(button: str = "left", count: int = 1) -> dict[str, Any]:
        return call("kvm_mouse_click", {"button": button, "count": count})

    @server.tool(name="kvm_mouse_scroll", description="Scroll the selected target mouse wheel; requires write authorization.")
    def kvm_mouse_scroll(dx: int = 0, dy: int = 0) -> dict[str, Any]:
        return call("kvm_mouse_scroll", {"dx": dx, "dy": dy})

    @server.tool(name="kvm_status", description="Report KVM authentication, stream, and held-key state.")
    def kvm_status() -> dict[str, Any]:
        return call("kvm_status", {})

    @server.tool(name="kvm_screenshot_to_file", description="Save a current JPEG screenshot to a file.")
    def kvm_screenshot_to_file(path: str, max_width: int = 1280) -> dict[str, Any]:
        return call("kvm_screenshot_to_file", {"path": path, "max_width": max_width})

    @server.tool(name="kvm_ocr_screenshot", description="OCR the current screenshot and return text with coordinates.")
    def kvm_ocr_screenshot(search_text: str = "") -> dict[str, Any]:
        return call("kvm_ocr_screenshot", {"search_text": search_text})

    @server.tool(name="kvm_ocr_click", description="Find text with OCR and click its best match; requires write authorization.")
    def kvm_ocr_click(text: str, button: str = "left", count: int = 1) -> dict[str, Any]:
        return call("kvm_ocr_click", {"text": text, "button": button, "count": count})

    @server.tool(name="exec_command", description="Run an allowlisted SSH command; requires explicit SSH transport.")
    def exec_command(command: str, transport: str = "") -> dict[str, Any]:
        return call("exec_command", {"command": command, "transport": transport})

    @server.tool(name="host.reboot", description="Reboot a named host; requires write authorization and target-bound confirmation.")
    def host_reboot(target: str, confirmation: str, attempts: int = 5,
                    delay: float = 1.0) -> dict[str, Any]:
        return call("host.reboot", {"target": target, "confirmation": confirmation,
                                     "attempts": attempts, "delay": delay})

    @server.tool(name="kvm_sequence_plan", description="Validate and plan a target-bound KVM action sequence.")
    def kvm_sequence_plan(plan: dict[str, Any] | None = None, plan_b64: str | None = None) -> dict[str, Any]:
        return call("kvm_sequence_plan", {"plan": plan} if plan is not None else {"plan_b64": plan_b64})

    @server.tool(name="kvm_sequence_authorize", description="Authorize a planned target-bound KVM sequence.")
    def kvm_sequence_authorize(plan: dict[str, Any], approved: bool = False, ttl_s: float = 30.0) -> dict[str, Any]:
        return call("kvm_sequence_authorize", {"plan": plan, "approved": approved, "ttl_s": ttl_s})

    @server.tool(name="kvm_sequence_execute", description="Execute an approved target-bound KVM sequence.")
    def kvm_sequence_execute(plan: dict[str, Any], approved: bool = False, ttl_s: float = 30.0) -> dict[str, Any]:
        return call("kvm_sequence_execute", {"plan": plan, "approved": approved, "ttl_s": ttl_s})

    @server.tool(name="kvm_workflow_list", description="List redacted named KVM workflows.")
    def kvm_workflow_list() -> dict[str, Any]:
        return call("kvm_workflow_list", {})

    @server.tool(name="kvm_workflow_inspect", description="Inspect a redacted named KVM workflow.")
    def kvm_workflow_inspect(name: str, revision: str | None = None, target: str | None = None) -> dict[str, Any]:
        return call("kvm_workflow_inspect", {"name": name, "revision": revision, "target": target})

    @server.tool(name="kvm_workflow_execute", description="Execute an approved named KVM workflow.")
    def kvm_workflow_execute(name: str, revision: str, approved: bool = False,
                             target: str | None = None, ttl_s: float = 30.0) -> dict[str, Any]:
        return call("kvm_workflow_execute", {"name": name, "revision": revision,
                                             "approved": approved, "target": target, "ttl_s": ttl_s})

    return server


def main() -> None:
    build_mcp_server().run(transport="stdio")


if __name__ == "__main__":
    main()
