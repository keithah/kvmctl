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
from kvmctl.operations import TOOL_SPEC
from kvmctl.policy import PolicyError, TRANSPORTS
from kvmctl.semantics import SemanticSurface

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
            transport = str(arguments.get("transport", ""))
            if transport != "kvm":
                raise PolicyError(
                    "select requires an explicit transport='kvm' "
                    f"(got {transport!r}); allowed transports: {TRANSPORTS}"
                )
            # The verified recipe depends on real timing (120 ms holds / 150 ms
            # gaps / 8 s + 12 s OTG waits). Never default to a no-op sleep:
            # require an explicit sleep callable when not running against a
            # test context.
            if sleep is None and not bool(context.get("test_mode")):
                return json.dumps({
                    "operation": "select", "ok": False,
                    "error": ("no sleep callable in context; select uses real "
                              "timing (held keys, OTG waits) and refuses to run "
                              "with zero delays. Pass sleep or test_mode=true."),
                })
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
            if sleep is None and not bool(context.get("test_mode")):
                return json.dumps({
                    "operation": "rearm_otg", "ok": False,
                    "error": ("no sleep callable in context; rearm_otg needs "
                              "real 8 s/12 s waits. Pass sleep or "
                              "test_mode=true."),
                })
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
