"""Semantic operations surface shared by the CLI and MCP front-ends.

Every operation returns a machine-readable evidence dict:
    {"operation": ..., "transport": ..., "read_only": bool, "ok": bool,
     "evidence": {...}}

There is deliberately NO raw/arbitrary API passthrough: only the named
semantic operations below exist.
"""
from __future__ import annotations

import base64
import math
import os
import tempfile
from dataclasses import replace
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
from kvmctl.results import normalize_error, operation_result
from kvmctl.journal import Journal
from kvmctl.sequences import validate_plan, plan_hash
from kvmctl.sequence_executor import SequenceExecutor, SequencePlanRecord
from kvmctl.workflows import WorkflowRepository, list_workflows, inspect_workflow, resolve_workflow


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
        workflow_repository: Optional[WorkflowRepository] = None,
        sequence_executor: Optional[SequenceExecutor] = None,
        journal: Optional[Journal] = None, authorization_store=None
    ):
        self.client = client
        self.session = session or SessionState()
        self.policy = TransportPolicy(
            write_enabled=write_enabled,
            ssh_allowlist=ssh_allowlist,
            ssh_runner=ssh_runner,
        )
        self.host = HostAdapter(host_runner, profile=host_profile) if host_runner is not None else None
        self.workflow_repository = workflow_repository or WorkflowRepository(())
        if journal is None:
            journal = Journal(os.path.join(tempfile.gettempdir(), "kvmctl-semantic-journal.jsonl"))
        self.journal = journal
        self.sequence_executor = sequence_executor or SequenceExecutor(
            client, self.session, journal, authorization_store=authorization_store)

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

    # -- target-bound sequence and workflow operations -----------------------

    @staticmethod
    def _sequence_envelope(operation: str, *, read_only: bool, target=None,
                           ok=True, state="planned", error=None, **evidence) -> dict:
        return operation_result(operation=operation, transport="kvm",
                                read_only=read_only, target=target, ok=ok,
                                state=state, evidence=evidence, error=error)

    def _validated_sequence_record(self, plan) -> SequencePlanRecord:
        """Accept only canonical records produced by the executor planner."""
        expected = None
        if not isinstance(plan, SequencePlanRecord):
            if isinstance(plan, dict):
                expected = validate_plan(plan)
                planned = self.sequence_executor.plan(expected)
                if not isinstance(planned, SequencePlanRecord):
                    raise TypeError("invalid sequence plan record")
            else:
                raise TypeError("authorization requires a validated sequence plan record")
        else:
            planned = plan
        try:
            canonical = validate_plan(planned.plan)
        except (TypeError, ValueError, KeyError) as exc:
            raise TypeError("invalid sequence plan record") from exc
        if expected is not None and canonical != expected:
            raise ValueError("invalid sequence plan record")
        if (planned.target != canonical.target
                or planned.plan_hash != plan_hash(canonical)
                or planned.action_count != len(canonical.actions)
                or planned.max_duration_ms != canonical.max_duration_ms):
            raise ValueError("invalid sequence plan record")
        current = self.session.current
        if current is None or not current.verified or current.machine != canonical.target:
            raise ValueError("target mismatch or session not verified")
        return planned

    def _sequence_write_gate(self, operation: str, *, target=None, plan_hash_value="") -> None:
        try:
            self.policy.require_write(operation)
        except PolicyError as exc:
            reject = getattr(self.sequence_executor, "reject", None)
            if reject is not None:
                try:
                    reject(normalize_error(exc) or "operation rejected", target=target, plan_hash_value=plan_hash_value)
                except BaseException:
                    pass
            raise

    def _sequence_reject(self, reason: str, *, target=None, plan_hash_value="") -> None:
        reject = getattr(self.sequence_executor, "reject", None)
        if reject is not None:
            try:
                reject(reason, target=target, plan_hash_value=plan_hash_value)
            except BaseException:
                pass

    @staticmethod
    def _validate_authorization_inputs(approved, ttl_s) -> None:
        if type(approved) is not bool:
            raise TypeError("approved must be a boolean")
        if (isinstance(ttl_s, bool) or not isinstance(ttl_s, (int, float))
                or not math.isfinite(ttl_s) or not float(ttl_s).is_integer()
                or not 0 < ttl_s <= SequenceExecutor.MAX_AUTHORIZATION_TTL_S):
            raise ValueError("authorization ttl must be finite, integral, and between 0 and 30 seconds")

    def kvm_sequence_plan(self, plan) -> dict:
        try:
            planned = self.sequence_executor.plan(validate_plan(plan))
        except (TypeError, ValueError, KeyError):
            target = plan.get("target") if isinstance(plan, dict) and isinstance(plan.get("target"), str) else None
            self._sequence_reject("plan validation failed", target=target)
            raise
        return self._sequence_envelope(
            "kvm_sequence_plan", read_only=True, target=planned.target,
            plan_hash=planned.plan_hash, action_count=planned.action_count,
            max_duration_ms=planned.max_duration_ms)

    def kvm_sequence_authorize(self, plan, *, approved: bool,
                               ttl_s: float = 30.0) -> dict:
        self._validate_authorization_inputs(approved, ttl_s)
        try:
            canonical_input = validate_plan(plan) if isinstance(plan, dict) else None
        except (TypeError, ValueError, KeyError):
            target = plan.get("target") if isinstance(plan, dict) and isinstance(plan.get("target"), str) else None
            self._sequence_reject("plan validation failed", target=target)
            raise
        target = canonical_input.target if canonical_input is not None else getattr(plan, "target", None)
        exact_hash = plan_hash(canonical_input) if canonical_input is not None else getattr(plan, "plan_hash", "")
        self._sequence_write_gate("kvm_sequence_authorize", target=target,
                                  plan_hash_value=exact_hash)
        try:
            planned = self._validated_sequence_record(plan)
        except (TypeError, ValueError, KeyError) as exc:
            reason = "invalid sequence plan record" if isinstance(exc, TypeError) else (str(exc) or "invalid sequence plan record")
            self._sequence_reject(reason, target=target, plan_hash_value=exact_hash)
            raise
        authorization = self.sequence_executor.authorize(planned, approved=approved, ttl_s=ttl_s)
        result = self._sequence_envelope(
            "kvm_sequence_authorize", read_only=False, target=authorization.target,
            state="authorized", plan_hash=authorization.plan_hash, action_count=len(authorization.plan.actions), expires_at=authorization.expires_at)
        # The token is an opaque capability, not a credential; it must be
        # returned to the caller while never entering the journal.
        result["evidence"]["approval_token"] = authorization.token
        return result

    def kvm_sequence_execute(self, plan=None, *, approval_token: str | None = None,
                             approved: bool = False, ttl_s: float = 30.0) -> dict:
        try:
            canonical_input = validate_plan(plan) if plan is not None else None
        except (TypeError, ValueError, KeyError):
            target = plan.get("target") if isinstance(plan, dict) and isinstance(plan.get("target"), str) else None
            self._sequence_reject("plan validation failed", target=target)
            raise
        target = canonical_input.target if canonical_input is not None else None
        self._sequence_write_gate("kvm_sequence_execute", target=target,
                                  plan_hash_value=(plan_hash(canonical_input) if canonical_input is not None else ""))
        if not approval_token:
            if plan is not None:
                # Preserve deterministic plan/target errors ahead of the
                # missing-token error, without authorizing or executing.
                try:
                    self._validated_sequence_record(plan)
                except (TypeError, ValueError, KeyError) as exc:
                    reason = "invalid sequence plan record" if isinstance(exc, TypeError) else (str(exc) or "invalid sequence plan record")
                    self._sequence_reject(reason, target=target,
                                          plan_hash_value=plan_hash(validate_plan(plan)))
                    raise
            target = canonical_input.target if canonical_input is not None else None
            self._sequence_reject("authorization missing", target=target,
                                          plan_hash_value=plan_hash(canonical_input) if canonical_input is not None else "")
            raise ValueError("approval_token is required; authorize the exact plan first")
        expected = canonical_input
        result = self.sequence_executor.execute(approval_token, expected_plan=expected)
        return self._sequence_envelope(
            "kvm_sequence_execute", read_only=False, target=result.target,
            ok=result.ok, state="completed" if result.ok else "aborted",
            plan_hash=result.plan_hash, action_count=result.completed_steps,
            elapsed_ms=result.elapsed_ms, execution_ok=result.ok,
            execution_status="completed" if result.ok else "aborted",
            completed_steps=result.completed_steps,
            cleanup_ok=result.cleanup_ok,
            cleanup_status="ok" if result.cleanup_ok else "failed",
            cleanup_errors=list(result.cleanup_errors), error=result.error or None)

    def kvm_workflow_list(self) -> dict:
        workflows = list_workflows(self.workflow_repository)
        return self._sequence_envelope("kvm_workflow_list", read_only=True,
                                       state="observed", workflows=workflows)

    def kvm_workflow_inspect(self, name: str, revision: str | None = None,
                             target: str | None = None) -> dict:
        workflow = inspect_workflow(self.workflow_repository, name, revision, target)
        return self._sequence_envelope("kvm_workflow_inspect", read_only=True,
                                       target=workflow.get("target"), state="observed",
                                       workflow=workflow)

    def kvm_workflow_authorize(self, name: str, revision: str, *, approved: bool,
                               target: str | None = None, ttl_s: float = 30.0) -> dict:
        self._validate_authorization_inputs(approved, ttl_s)
        invocation_target = target
        if invocation_target is None:
            for definition in self.workflow_repository.list():
                if definition.name == name and not definition.target_independent: invocation_target = definition.target
        try:
            workflow = resolve_workflow(self.workflow_repository, name, revision, invocation_target)
        except (TypeError, ValueError, KeyError) as exc:
            self._sequence_reject(normalize_error(exc) or "operation rejected", target=invocation_target)
            raise
        actual = invocation_target or workflow.target
        bound = workflow.plan if workflow.plan.target == actual else replace(workflow.plan, target=actual)
        self._sequence_write_gate("kvm_sequence_authorize", target=actual, plan_hash_value=plan_hash(bound))
        auth = self.sequence_executor.authorize(self.sequence_executor.plan(bound, workflow_revision=workflow.revision), approved=approved, ttl_s=ttl_s)
        result = self._sequence_envelope("kvm_workflow_authorize", read_only=False, target=auth.target, state="authorized", plan_hash=auth.plan_hash, workflow_revision=workflow.revision, expires_at=auth.expires_at)
        result["evidence"]["approval_token"] = auth.token
        return result

    def kvm_workflow_execute(self, name: str, revision: str, *, approved: bool = False,
                             approval_token: str | None = None, target: str | None = None, ttl_s: float = 30.0) -> dict:
        invocation_target = target
        if invocation_target is None:
            for definition in self.workflow_repository.list():
                if definition.name == name and not definition.target_independent: invocation_target = definition.target
        try:
            workflow = resolve_workflow(self.workflow_repository, name, revision, invocation_target)
        except (TypeError, ValueError, KeyError) as exc:
            self._sequence_reject(normalize_error(exc) or "operation rejected", target=invocation_target)
            raise
        self._sequence_write_gate("kvm_workflow_execute", target=invocation_target or workflow.target,
                                  plan_hash_value=plan_hash(workflow.plan))
        if not approval_token:
            self._sequence_reject("authorization missing", target=invocation_target or workflow.target,
                                          plan_hash_value=plan_hash(workflow.plan))
            raise ValueError("approval_token is required; authorize the exact workflow first")
        result = self.sequence_executor.execute(
            approval_token, expected_plan=workflow.plan,
            expected_workflow_revision=workflow.revision,
            expected_target=invocation_target or workflow.target)
        return self._sequence_envelope(
            "kvm_workflow_execute", read_only=False, target=result.target,
            ok=result.ok, state="completed" if result.ok else "aborted",
            plan_hash=result.plan_hash, action_count=len(workflow.plan.actions),
            elapsed_ms=result.elapsed_ms, execution_ok=result.ok,
            execution_status="completed" if result.ok else "aborted",
            completed_steps=result.completed_steps, cleanup_ok=result.cleanup_ok,
            cleanup_status="ok" if result.cleanup_ok else "failed",
            cleanup_errors=list(result.cleanup_errors), error=result.error or None)
