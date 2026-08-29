"""MCP-facing tool registry for the kvmctl semantic surface.

Exposes exactly the semantic operations; no raw/arbitrary API passthrough.
Each tool declares its read-only or write-gated nature so MCP clients can
render consent UIs. ``dispatch_tool`` is the single JSON-in/JSON-out entry
point an MCP server shim would call.

Context dict keys:
    client:        configured KvmClient (required)
    session:       optional SessionState (shared across calls)
    ssh_runner:    callable(cmd) -> {rc, stdout, stderr}  (for exec_command)
    host_runner:   argv-only callable for named host probes/reboot
    ssh_allowlist: tuple of allowed base commands (for exec_command)
    write_enabled: bool gate, default False
"""
from __future__ import annotations

import json
import base64
import math
from collections.abc import Mapping
from typing import Optional

from kvmctl.client import KvmClient
from kvmctl.machines import SessionState
from kvmctl.operations import TOOL_SPEC as _BASE_TOOL_SPEC
from kvmctl.policy import PolicyError, TRANSPORTS
from kvmctl.semantics import SemanticSurface
from kvmctl.host import HostProbeProfile
from kvmctl.results import normalize_error, operation_result

TOOL_SPEC = list(_BASE_TOOL_SPEC)
_TOOL_NAMES = frozenset(t["name"] for t in TOOL_SPEC)


def _surface(context: dict) -> SemanticSurface:
    client: KvmClient = context["client"]
    executor = context.get("sequence_executor") or context.get("_sequence_executor")
    surface = SemanticSurface(
        client,
        session=context.get("session") or SessionState(),
        write_enabled=bool(context.get("write_enabled")),
        ssh_allowlist=tuple(context.get("ssh_allowlist") or ()),
        ssh_runner=context.get("ssh_runner"),
        host_runner=context.get("host_runner"),
        host_profile=context.get("host_profile"),
        workflow_repository=context.get("workflow_repository"),
        sequence_executor=executor,
        journal=context.get("journal"),
        authorization_store=context.get("authorization_store"),
    )
    if executor is None:
        context["_sequence_executor"] = surface.sequence_executor
    return surface


_SEQUENCE_TOOLS = frozenset({"kvm_sequence_plan", "kvm_sequence_authorize",
                             "kvm_sequence_execute", "kvm_workflow_authorize", "kvm_workflow_list",
                             "kvm_workflow_inspect", "kvm_workflow_execute"})

# Keep dispatcher validation explicit: unlike FastMCP's generated schemas,
# direct callers do not receive argument validation from the transport.
_PLAN_FIELDS = frozenset({"plan", "plan_b64", "target", "actions",
                          "max_duration_ms", "unexpected_screen_policy"})
_SEQUENCE_FIELDS = _PLAN_FIELDS | {"approved", "ttl_s", "approval_token"}
_WORKFLOW_SCHEMAS = {
    "kvm_sequence_plan": _PLAN_FIELDS,
    "kvm_sequence_authorize": _SEQUENCE_FIELDS,
    "kvm_sequence_execute": _SEQUENCE_FIELDS,
    "kvm_workflow_authorize": frozenset({"name", "revision", "approved", "target", "ttl_s"}),
    "kvm_workflow_list": frozenset(),
    "kvm_workflow_inspect": frozenset({"name", "revision", "target"}),
    "kvm_workflow_execute": frozenset({"name", "revision", "approved", "approval_token", "target", "ttl_s"}),
}


def _sequence_error(name: str, exc: BaseException) -> str:
    return json.dumps(operation_result(operation=name, transport="kvm",
        read_only=name in {"kvm_sequence_plan", "kvm_workflow_list", "kvm_workflow_inspect"},
        ok=False, state="aborted", error={"code": normalize_error(exc) or "operation failed"}))


def _decode_plan(arguments: dict) -> object:
    if "plan" in arguments:
        return arguments["plan"]
    # The dispatcher also accepts an inline plan as the argument object.
    # Sequence control fields belong to the dispatcher call, not the plan.
    if "target" in arguments or "actions" in arguments:
        return {key: value for key, value in arguments.items()
                if key not in {"approved", "ttl_s", "approval_token"}}
    encoded = arguments.get("plan_b64")
    if encoded is None:
        raise ValueError("plan is required")
    try:
        raw = base64.b64decode(encoded, validate=True)
        return json.loads(raw.decode("utf-8"))
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid base64 plan") from exc


def _validate_sequence_arguments(name: str, arguments: dict) -> None:
    unknown = set(arguments) - _WORKFLOW_SCHEMAS[name]
    if unknown:
        raise ValueError(f"unsupported argument field(s): {', '.join(sorted(unknown))}")
    if name in {"kvm_sequence_plan", "kvm_sequence_authorize", "kvm_sequence_execute"}:
        if "plan" in arguments and not isinstance(arguments["plan"], Mapping):
            raise TypeError("invalid argument plan: expected object")
        if "plan_b64" in arguments and not isinstance(arguments["plan_b64"], str):
            raise TypeError("invalid argument plan_b64: expected string")
        if "approved" in arguments and type(arguments["approved"]) is not bool:
            raise TypeError("invalid argument approved: expected boolean")
        if "ttl_s" in arguments:
            ttl = arguments["ttl_s"]
            if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or not math.isfinite(ttl):
                raise TypeError("invalid argument ttl_s: expected finite number")
        if "approval_token" in arguments and not isinstance(arguments["approval_token"], str):
            raise TypeError("invalid argument approval_token: expected string")
    if name == "kvm_workflow_inspect":
        if not isinstance(arguments.get("name"), str):
            raise TypeError("invalid argument name: expected string")
        for field in ("revision", "target"):
            if field in arguments and arguments[field] is not None and not isinstance(arguments[field], str):
                raise TypeError(f"invalid argument {field}: expected string")
    if name == "kvm_workflow_execute":
        for field in ("name", "revision"):
            if not isinstance(arguments.get(field), str):
                raise TypeError(f"invalid argument {field}: expected string")
        if "target" in arguments and arguments["target"] is not None and not isinstance(arguments["target"], str):
            raise TypeError("invalid argument target: expected string")
        if "approved" in arguments and type(arguments["approved"]) is not bool:
            raise TypeError("invalid argument approved: expected boolean")
        if "ttl_s" in arguments:
            ttl = arguments["ttl_s"]
            if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or not math.isfinite(ttl):
                raise TypeError("invalid argument ttl_s: expected finite number")
        if "approval_token" in arguments and not isinstance(arguments["approval_token"], str):
            raise TypeError("invalid argument approval_token: expected string")


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
        if name in _SEQUENCE_TOOLS:
            if not isinstance(arguments, dict):
                raise TypeError("invalid arguments: expected object")
            _validate_sequence_arguments(name, arguments)
            if name == "kvm_sequence_plan":
                out = surf.kvm_sequence_plan(_decode_plan(arguments))
            elif name == "kvm_sequence_authorize":
                out = surf.kvm_sequence_authorize(_decode_plan(arguments),
                    approved=bool(arguments.get("approved", False)), ttl_s=float(arguments.get("ttl_s", 30.0)))
            elif name == "kvm_sequence_execute":
                out = surf.kvm_sequence_execute(
                    _decode_plan(arguments) if ("plan" in arguments or "plan_b64" in arguments or "target" in arguments or "actions" in arguments) else None,
                    approval_token=arguments.get("approval_token"))
            elif name == "kvm_workflow_authorize":
                out = surf.kvm_workflow_authorize(arguments["name"], arguments["revision"], approved=bool(arguments.get("approved", False)), target=arguments.get("target"), ttl_s=float(arguments.get("ttl_s", 30.0)))
            elif name == "kvm_workflow_list":
                out = surf.kvm_workflow_list()
            elif name == "kvm_workflow_inspect":
                out = surf.kvm_workflow_inspect(arguments["name"], arguments.get("revision"), arguments.get("target"))
            else:
                out = surf.kvm_workflow_execute(arguments["name"], arguments["revision"],
                    approved=bool(arguments.get("approved", False)), approval_token=arguments.get("approval_token"), target=arguments.get("target"),
                    ttl_s=float(arguments.get("ttl_s", 30.0)))
            return json.dumps(out)
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
        elif name == "host.identity.inspect":
            out = surf.host_identity_inspect()
        elif name == "host.graphics.inspect":
            out = surf.host_graphics_inspect()
        elif name == "service.render_access.inspect":
            out = surf.service_render_access_inspect()
        elif name == "host.reboot":
            out = surf.host_reboot(arguments["target"], arguments["confirmation"],
                                    attempts=int(arguments.get("attempts", 5)),
                                    delay=float(arguments.get("delay", 1.0)),
                                    sleep=sleep or _no_sleep)
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
        elif name == "kvm_send_text":
            out = surf.kvm_send_text(arguments["text"], sleep=sleep or _no_sleep,
                                     interval_s=float(arguments.get("interval_s", 0.01)))
        elif name == "kvm_send_keys":
            out = surf.kvm_send_keys(arguments["combo"])
        elif name == "kvm_hold_key":
            out = surf.kvm_hold_key(arguments["key"], int(arguments["duration_ms"]),
                                    sleep=sleep or _no_sleep)
        elif name == "kvm_release_all":
            out = surf.kvm_release_all()
        elif name == "kvm_mouse_move":
            out = surf.kvm_mouse_move(int(arguments["x"]), int(arguments["y"]))
        elif name == "kvm_mouse_move_pct":
            out = surf.kvm_mouse_move_pct(float(arguments["x_pct"]), float(arguments["y_pct"]))
        elif name == "kvm_mouse_click":
            out = surf.kvm_mouse_click(str(arguments.get("button", "left")),
                                       int(arguments.get("count", 1)), sleep=sleep or _no_sleep)
        elif name == "kvm_mouse_scroll":
            out = surf.kvm_mouse_scroll(int(arguments.get("dx", 0)), int(arguments.get("dy", 0)))
        elif name == "kvm_status":
            out = surf.kvm_status()
        elif name == "kvm_screenshot_to_file":
            out = surf.kvm_screenshot_to_file(arguments["path"],
                                               preview_max_width=int(arguments.get("max_width", 1280)))
        elif name == "kvm_ocr_screenshot":
            out = surf.kvm_ocr_screenshot(str(arguments.get("search_text", "")))
        elif name == "kvm_ocr_click":
            out = surf.kvm_ocr_click(arguments["text"], str(arguments.get("button", "left")),
                                     int(arguments.get("count", 1)), sleep=sleep or _no_sleep)
        elif name == "exec_command":
            out = surf.exec_command(arguments["command"],
                                    transport=str(arguments.get("transport", "")))
        else:  # pragma: no cover
            raise AssertionError(name)
    except Exception as exc:
        # Policy refusals and device errors become structured payloads, never raises.
        if name in _SEQUENCE_TOOLS:
            try:
                surf.sequence_executor.reject(normalize_error(exc) or "operation rejected", target=arguments.get("target"))
            except Exception:
                pass
            return _sequence_error(name, exc)
        out = {"operation": name, "ok": False, "error": str(exc)[:300]}
    return json.dumps(out)


def _no_sleep(s):
    pass
