"""MCP-facing tool registry for the kvmctl semantic surface.

Exposes exactly the semantic operations; no raw/arbitrary API passthrough.
Each tool declares its read-only or write-gated nature so MCP clients can
render consent UIs. ``dispatch_tool`` is the single JSON-in/JSON-out entry
point an MCP server shim would call.

Context dict keys:
    client:        configured KvmClient (required)
    session:       optional SessionState (shared across calls)
    ssh_runner:    callable(cmd) -> {rc, stdout, stderr}  (for exec_command)
    ssh_allowlist: tuple of allowed base commands (for exec_command)
    write_enabled: bool gate, default False
"""
from __future__ import annotations

import json
from typing import Optional

from kvmctl.client import KvmClient
from kvmctl.machines import SessionState
from kvmctl.semantics import SemanticSurface

TOOL_SPEC = [
    {"name": "capabilities", "description": "Report device capabilities and identity.",
     "read_only": True},
    {"name": "snapshot", "description": "Capture a JPEG snapshot; returns bytes/SHA-256.",
     "read_only": True,
     "params": {"path": "str (optional save path)", "preview_max_width": "int"}},
    {"name": "ocr", "description": "OCR the screen (or a provided image); returns text.",
     "read_only": True, "params": {}},
    {"name": "verify", "description": "Verify which machine is on screen.",
     "read_only": True,
     "params": {"machine": "str", "policy": "none|frame_change|ocr_identity|prompt_pattern"}},
    {"name": "select", "description": "Switch KVM port to a named machine (held-key recipe).",
     "write_gate": True,
     "params": {"machine": "str", "verify_policy": "str", "rearm": "bool",
                "settle_s": "float"}},
    {"name": "hid_reset", "description": "Reset the HID subsystem.", "write_gate": True},
    {"name": "rearm_otg", "description": "OTG gadget bounce to re-arm hotkey engine.",
     "write_gate": True},
    {"name": "exec_command", "description":
        "Run an allowlisted command over SSH. Requires transport='ssh' explicitly.",
     "write_gate": True, "params": {"command": "str", "transport": "'ssh' required"}},
]

_TOOL_NAMES = frozenset(t["name"] for t in TOOL_SPEC)


def _surface(context: dict) -> SemanticSurface:
    client: KvmClient = context["client"]
    return SemanticSurface(
        client,
        session=context.get("session") or SessionState(),
        write_enabled=bool(context.get("write_enabled")),
        ssh_allowlist=tuple(context.get("ssh_allowlist") or ()),
        ssh_runner=context.get("ssh_runner"),
    )


def dispatch_tool(name: str, arguments: Optional[dict], *,
                  context: dict) -> str:
    """Single JSON entry point for an MCP server shim."""
    arguments = arguments or {}
    if name not in _TOOL_NAMES:
        return json.dumps({"ok": False,
                           "error": f"unknown tool {name!r}; available: {sorted(_TOOL_NAMES)}"})
    surf = _surface(context)
    sleep = context.get("sleep")
    try:
        if name == "capabilities":
            out = surf.capabilities()
        elif name == "snapshot":
            out = surf.snapshot(path=arguments.get("path"),
                                preview_max_width=int(arguments.get("preview_max_width", 1280)))
        elif name == "ocr":
            img = arguments.get("image_b64")
            data = __import__("base64").b64decode(img) if img else None
            out = surf.ocr(data)
        elif name == "verify":
            kw = {}
            if sleep and name in ("verify",):
                kw["sleep"] = sleep
            out = surf.verify(arguments["machine"], policy=arguments.get("policy"), **kw)
        elif name == "select":
            out = surf.select(
                arguments["machine"],
                verify_policy=arguments.get("verify_policy"),
                rearm=bool(arguments.get("rearm", True)),
                settle_s=float(arguments.get("settle_s", 5.0)),
                sleep=sleep or _no_sleep,
            )
        elif name == "hid_reset":
            out = surf.hid_reset()
        elif name == "rearm_otg":
            out = surf.rearm_otg(sleep=sleep or _no_sleep)
        elif name == "exec_command":
            out = surf.exec_command(arguments["command"],
                                    transport=str(arguments.get("transport", "")))
        else:  # pragma: no cover
            raise AssertionError(name)
    except Exception as exc:
        # Policy refusals and device errors become structured payloads, never raises.
        out = {"operation": name, "ok": False, "error": str(exc)[:300]}
    return json.dumps(out)


def _no_sleep(s):
    pass
