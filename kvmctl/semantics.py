"""Semantic operations surface shared by the CLI and MCP front-ends.

Every operation returns a machine-readable evidence dict:
    {"operation": ..., "transport": ..., "read_only": bool, "ok": bool,
     "evidence": {...}}

There is deliberately NO raw/arbitrary API passthrough: only the named
semantic operations below exist.
"""
from __future__ import annotations

import base64
import time
from typing import Callable, Optional, Sequence

from kvmctl.client import KvmClient
from kvmctl.machines import (
    RACK,
    SessionState,
    SelectOptions,
    VerifyPolicy,
    DEFAULT_VERIFY_POLICY,
    run_verify_policy,
    select_machine,
)
from kvmctl.policy import PolicyError, TransportPolicy, TRANSPORTS
from kvmctl.host import ArgvRunner, HostAdapter, HostProbeProfile, run_probe
from kvmctl.results import operation_result


def _evidence(operation: str, transport: str, read_only: bool,
              ok: bool = True, **data) -> dict:
    return {
        "operation": operation,
        "transport": transport,
        "read_only": read_only,
        "ok": ok,
        "evidence": data,
    }


class SemanticSurface:
    """Semantic KVM operations with explicit policy gates."""

    def __init__(
        self,
        client: KvmClient,
        session: Optional[SessionState] = None,
        *,
        write_enabled: bool = False,
        ssh_allowlist: tuple[str, ...] = (),
        ssh_runner: Optional[Callable[[Sequence[str]], dict]] = None,
        host_runner: Optional[ArgvRunner] = None,
        host_profile: Optional[HostProbeProfile] = None,
    ):
        self.client = client
        self.session = session or SessionState()
        self.policy = TransportPolicy(
            write_enabled=write_enabled,
            ssh_allowlist=ssh_allowlist,
            ssh_runner=ssh_runner,
        )
        self.host = HostAdapter(host_runner, profile=host_profile) if host_runner is not None else None

    # -- policy conveniences -------------------------------------------------

    @property
    def write_enabled(self) -> bool:
        return self.policy.write_enabled

    @write_enabled.setter
    def write_enabled(self, value: bool) -> None:
        self.policy.write_enabled = value

    @property
    def transport(self) -> str:
        """The transport this surface operates over (KVM API)."""
        return "kvm"

    # -- read-only operations -------------------------------------------------

    def capabilities(self) -> dict:
        caps = self.client.capabilities()
        info = self.client.get_info()
        return _evidence(
            "capabilities", "kvm", read_only=True,
            caps=caps,
            device={
                "model": (info.get("platform") or {}).get("model", ""),
                "kvmd_version": (info.get("system") or {}).get("kvmd_version", ""),
            },
        )

    def snapshot(self, path: Optional[str] = None,
                 preview_max_width: int = 1280) -> dict:
        data = self.client.snapshot_jpeg(preview_max_width=preview_max_width)
        saved_to = None
        if path:
            with open(path, "wb") as fh:
                fh.write(data)
            saved_to = path
        return _evidence(
            "snapshot", "kvm", read_only=True,
            bytes=len(data),
            sha256=__import__("hashlib").sha256(data).hexdigest(),
            saved_to=saved_to,
            data_base64=None if path else base64.b64encode(data).decode("ascii"),
        )

    def ocr(self, image_bytes: Optional[bytes] = None) -> dict:
        if image_bytes is None:
            image_bytes = self.client.snapshot_jpeg()
        text = self.client.ocr(image_bytes)
        return _evidence("ocr", "kvm", read_only=True,
                         text=text, bytes=len(image_bytes))

    def verify(self, machine: str, policy: Optional[str] = None,
               baseline: Optional[bytes] = None, attempts: int = 5,
               delay: float = 1.0, sleep: Callable[[float], None] = time.sleep) -> dict:
        try:
            prof = RACK[machine]
        except KeyError:
            raise PolicyError(f"unknown machine {machine!r}") from None
        pol = VerifyPolicy(policy) if policy else DEFAULT_VERIFY_POLICY[machine]
        ok, detail = run_verify_policy(pol, self.client, prof, baseline,
                                       attempts=attempts, delay=delay, sleep=sleep)
        rec = self.session.current
        state_before = rec.state.value if rec else "unknown"
        if rec and rec.selected:
            if ok:
                rec = self.session.mark_verified(detail)
            else:
                rec = self.session.mark_verify_failed(detail)
        return _evidence(
            "verify", "kvm", read_only=True, ok=True,  # op succeeded; result in evidence
            verified=ok, machine=machine, policy=pol.value,
            state_before=state_before,
            state_after=(self.session.current.state.value
                         if self.session.current else "unknown"),
            detail=detail[:300],
        )

    def _host_inspect(self, operation: str) -> dict:
        if self.host is None:
            raise PolicyError(f"{operation} requires a configured host runner")
        evidence = run_probe(operation, self.host.runner,
                             max_output_bytes=self.host.max_output_bytes,
                             profile=self.host.profile)
        return operation_result(operation=operation, transport="host",
                                read_only=True, state="observed",
                                evidence=evidence)

    def host_identity_inspect(self) -> dict:
        return self._host_inspect("host.identity.inspect")

    def host_graphics_inspect(self) -> dict:
        return self._host_inspect("host.graphics.inspect")

    def service_render_access_inspect(self) -> dict:
        return self._host_inspect("service.render_access.inspect")

    # -- mutating (write-gated) operations ------------------------------------

    def select(self, machine: str, *, verify_policy: Optional[str] = None,
               rearm: bool = True, settle_s: float = 5.0,
               sleep: Callable[[float], None] = time.sleep) -> dict:
        self.policy.require_write("select")
        opts = SelectOptions(rearm=rearm, settle_s=settle_s)
        if verify_policy is not None:
            opts.verify_policy = VerifyPolicy(verify_policy)
        try:
            rec = select_machine(self.client, self.session, machine,
                                 options=opts, sleep=sleep)
        except Exception as exc:
            cur = self.session.current
            return _evidence(
                "select", "kvm", read_only=False, ok=False,
                error=str(exc)[:300],
                machine=machine,
                state=cur.state.value if cur else "unknown",
            )
        return _evidence(
            "select", "kvm", read_only=False,
            machine=machine, port=rec.port, verified=rec.verified,
            state=rec.state.value, detail=rec.detail[:300],
        )

    def hid_reset(self) -> dict:
        self.policy.require_write("hid_reset")
        self.client.hid_reset()
        return _evidence("hid_reset", "kvm", read_only=False)

    def rearm_otg(self, sleep: Callable[[float], None] = time.sleep) -> dict:
        from kvmctl.machines import otg_bounce
        self.policy.require_write("rearm_otg")
        otg_bounce(self.client, sleep=sleep)
        return _evidence("rearm_otg", "kvm", read_only=False)

    def host_reboot(self, target: str, confirmation: str, *,
                    attempts: int = 5, delay: float = 1.0,
                    sleep: Callable[[float], None] = time.sleep) -> dict:
        self.policy.require_write("host.reboot")
        if self.host is None:
            raise PolicyError("host.reboot requires a configured host runner")
        return self.host.reboot(target, confirmation, write_enabled=True,
                                attempts=attempts, delay=delay, sleep=sleep)

    def kvm_send_text(self, text: str, *, sleep: Callable[[float], None] = time.sleep,
                      interval_s: float = 0.01) -> dict:
        self.policy.require_write("kvm_send_text")
        from kvmctl.input import send_text
        if not (0 <= interval_s <= 10):
            raise PolicyError("interval_s must be between 0 and 10 seconds")
        result = send_text(self.client, text, sleep=sleep, inter_char_s=interval_s)
        return _evidence("kvm_send_text", "kvm", read_only=False, **result)

    def kvm_send_keys(self, combo: str) -> dict:
        self.policy.require_write("kvm_send_keys")
        from kvmctl.input import parse_combo
        modifiers, key = parse_combo(combo)
        for modifier in modifiers:
            self.client.key_down(modifier)
        try:
            self.client.press_key(key)
        finally:
            for modifier in reversed(modifiers):
                self.client.key_up(modifier)
        return _evidence("kvm_send_keys", "kvm", read_only=False,
                         combo=combo, modifiers=modifiers, key=key)

    def kvm_hold_key(self, key: str, duration_ms: int, *,
                     sleep: Callable[[float], None] = time.sleep) -> dict:
        self.policy.require_write("kvm_hold_key")
        from kvmctl.input import resolve_key
        if not (1 <= duration_ms <= 5000):
            raise PolicyError("duration_ms must be between 1 and 5000")
        canonical = resolve_key(key)
        self.client.key_down(canonical)
        try:
            sleep(duration_ms / 1000)
        finally:
            self.client.key_up(canonical)
        return _evidence("kvm_hold_key", "kvm", read_only=False,
                         key=canonical, duration_ms=duration_ms)

    def kvm_release_all(self) -> dict:
        self.policy.require_write("kvm_release_all")
        return _evidence("kvm_release_all", "kvm", read_only=False,
                         released=self.client.release_all())

    def kvm_mouse_move(self, x: int, y: int) -> dict:
        self.policy.require_write("kvm_mouse_move")
        self.client.mouse_move(x, y)
        return _evidence("kvm_mouse_move", "kvm", read_only=False, x=x, y=y)

    def kvm_mouse_move_pct(self, x_pct: float, y_pct: float) -> dict:
        self.policy.require_write("kvm_mouse_move_pct")
        x, y = self.client.mouse_move_pct(x_pct, y_pct)
        return _evidence("kvm_mouse_move_pct", "kvm", read_only=False,
                         x=x, y=y, x_pct=x_pct, y_pct=y_pct)

    def kvm_mouse_click(self, button: str = "left", count: int = 1,
                        *, sleep: Callable[[float], None] = time.sleep) -> dict:
        self.policy.require_write("kvm_mouse_click")
        if not (1 <= count <= 5):
            raise PolicyError("count must be between 1 and 5")
        for _ in range(count):
            self.client.mouse_button(button, True)
            try:
                sleep(0.025)
            finally:
                self.client.mouse_button(button, False)
            sleep(0.03)
        return _evidence("kvm_mouse_click", "kvm", read_only=False,
                         button=button, count=count)

    def kvm_mouse_scroll(self, dx: int = 0, dy: int = 0) -> dict:
        self.policy.require_write("kvm_mouse_scroll")
        self.client.mouse_scroll(dx, dy)
        return _evidence("kvm_mouse_scroll", "kvm", read_only=False, dx=dx, dy=dy)

    def kvm_status(self) -> dict:
        return _evidence("kvm_status", "kvm", read_only=True,
                         authenticated=bool(self.client.token),
                         held_keys=sorted(self.client._held_keys),
                         stream_open=self.client._stream is not None)

    def kvm_screenshot_to_file(self, path: str, *, preview_max_width: int = 1280) -> dict:
        data = self.client.snapshot_jpeg(preview_max_width=preview_max_width)
        with open(path, "wb") as fh:
            fh.write(data)
        return _evidence("kvm_screenshot_to_file", "kvm", read_only=True,
                         path=path, bytes=len(data))

    def kvm_ocr_screenshot(self, search_text: str = "") -> dict:
        from kvmctl.ocr import analyze
        data = self.client.snapshot_jpeg()
        result = analyze(data, search_text)
        return _evidence("kvm_ocr_screenshot", "kvm", read_only=True, **result)

    def kvm_ocr_click(self, text: str, button: str = "left", count: int = 1,
                      *, sleep: Callable[[float], None] = time.sleep) -> dict:
        self.policy.require_write("kvm_ocr_click")
        from kvmctl.ocr import analyze
        result = analyze(self.client.snapshot_jpeg(), text)
        if not result["elements"]:
            return _evidence("kvm_ocr_click", "kvm", read_only=False,
                             ok=False, found=False, text=text)
        best = result["elements"][0]
        self.kvm_mouse_move_pct(best["x_pct"], best["y_pct"])
        self.kvm_mouse_click(button, count, sleep=sleep)
        return _evidence("kvm_ocr_click", "kvm", read_only=False,
                         found=True, text=best["text"],
                         confidence=best["confidence"], pixel=best["pixel"],
                         x_pct=best["x_pct"], y_pct=best["y_pct"], count=count,
                         button=button)

    def exec_command(self, command: str, *, transport: str = "") -> dict:
        self.policy.require_write("exec_command")
        if transport != "ssh":
            raise PolicyError(
                f"exec_command requires an explicit transport='ssh' "
                f"(got {transport!r}); allowed transports: {TRANSPORTS}"
            )
        result = self.policy.run_ssh(command)
        return _evidence("exec_command", "ssh", read_only=False, **result)
