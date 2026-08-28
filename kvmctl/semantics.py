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

    def exec_command(self, command: str, *, transport: str = "") -> dict:
        self.policy.require_write("exec_command")
        if transport != "ssh":
            raise PolicyError(
                f"exec_command requires an explicit transport='ssh' "
                f"(got {transport!r}); allowed transports: {TRANSPORTS}"
            )
        result = self.policy.run_ssh(command)
        return _evidence("exec_command", "ssh", read_only=False, **result)
