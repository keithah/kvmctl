"""Target-bound, explicitly-dispatched KVM sequence execution."""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable, Optional

from .journal import Journal
from .machines import SessionState, device_lock
from .sequences import SequencePlan, validate_plan, plan_hash
from .workflows import WorkflowDefinition


@dataclass(frozen=True)
class SequencePlanRecord:
    plan: SequencePlan
    target: str
    plan_hash: str
    action_count: int
    max_duration_ms: int
    expires_at: float | None = None
    workflow_revision: str | None = None


@dataclass(frozen=True)
class SequenceAuthorization:
    plan: SequencePlan
    target: str
    plan_hash: str
    expires_at: float
    workflow_revision: str | None = None


@dataclass
class SequenceExecutionResult:
    ok: bool
    cleanup_ok: bool
    target: str
    plan_hash: str
    elapsed_ms: int = 0
    error: str = ""
    completed_steps: int = 0
    cleanup_errors: tuple[str, ...] = ()


class SequenceExecutor:
    def __init__(self, client, session: SessionState, journal: Journal,
                 *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 device_id: str = "default", stream_owned: bool = False):
        self.client, self.session, self.journal = client, session, journal
        self.clock, self.sleep, self.device_id = clock, sleep, device_id
        self.stream_owned = stream_owned

    def plan(self, plan: SequencePlan, *, workflow_revision: str | None = None) -> SequencePlanRecord:
        canonical = validate_plan(plan)
        current = self.session.current
        if current is None or not current.verified:
            raise ValueError("target session is not verified")
        if current.machine != canonical.target:
            raise ValueError("target mismatch")
        digest = plan_hash(canonical)
        record = SequencePlanRecord(canonical, canonical.target, digest, len(canonical.actions),
                                    canonical.max_duration_ms, workflow_revision=workflow_revision)
        self.journal.checkpoint(operation="sequence", target=record.target, transition="planned",
                                plan_hash=digest, action_count=record.action_count,
                                max_duration_ms=record.max_duration_ms,
                                workflow_revision=workflow_revision)
        return record

    def authorize(self, planned: SequencePlanRecord, *, approved: bool,
                  ttl_s: float = 30.0) -> SequenceAuthorization:
        if not approved:
            raise ValueError("plan must be approved")
        if ttl_s <= 0:
            raise ValueError("authorization ttl must be positive")
        now = self.clock()
        auth = SequenceAuthorization(planned.plan, planned.target, planned.plan_hash,
                                     now + ttl_s, planned.workflow_revision)
        self.journal.checkpoint(operation="sequence", target=auth.target, transition="authorized",
                                plan_hash=auth.plan_hash, expires_at=auth.expires_at,
                                workflow_revision=auth.workflow_revision)
        return auth

    def execute(self, authorization: SequenceAuthorization) -> SequenceExecutionResult:
        start = self.clock()
        result = SequenceExecutionResult(False, True, authorization.target, authorization.plan_hash)
        lock = device_lock(self.device_id)
        if not lock.acquire(blocking=False):
            result.error = "device lock conflict"
            return result
        owned_stream = self.stream_owned
        try:
            if self.clock() >= authorization.expires_at:
                result.error = "authorization expired"
                return result
            if plan_hash(authorization.plan) != authorization.plan_hash:
                result.error = "plan hash changed"
                return result
            current = self.session.current
            if current is None or not current.verified or current.machine != authorization.target:
                result.error = "target mismatch or session not verified"
                return result
            self.journal.checkpoint(operation="sequence", target=authorization.target, transition="started",
                                    plan_hash=authorization.plan_hash)
            deadline = start + authorization.plan.max_duration_ms / 1000.0
            for index, action in enumerate(authorization.plan.actions):
                if self.clock() >= deadline:
                    raise TimeoutError("sequence deadline exceeded")
                self.journal.checkpoint(operation="sequence", target=authorization.target, transition="step_started",
                                        plan_hash=authorization.plan_hash, step=index)
                self._dispatch(action)
                result.completed_steps = index + 1
                self.journal.checkpoint(operation="sequence", target=authorization.target, transition="step_completed",
                                        plan_hash=authorization.plan_hash, step=index)
            result.ok = True
            self.journal.checkpoint(operation="sequence", target=authorization.target, transition="completed",
                                    plan_hash=authorization.plan_hash, steps=result.completed_steps)
        except BaseException as exc:
            result.error = "cancelled" if isinstance(exc, (KeyboardInterrupt, GeneratorExit)) else str(exc)[:300]
            self.journal.checkpoint(operation="sequence", target=authorization.target, transition="aborted",
                                    plan_hash=authorization.plan_hash, steps=result.completed_steps,
                                    reason=result.error)
        finally:
            errors = []
            try:
                self.client.release_all()
            except Exception as exc:
                errors.append(str(exc)[:200])
            if owned_stream:
                try: self.client.close_stream()
                except Exception as exc: errors.append(str(exc)[:200])
            result.cleanup_errors = tuple(errors)
            result.cleanup_ok = not errors
            if errors:
                result.ok = False
                self.journal.checkpoint(operation="sequence", target=authorization.target,
                                        transition="cleanup_failed", plan_hash=authorization.plan_hash,
                                        error_count=len(errors))
            result.elapsed_ms = max(0, int((self.clock() - start) * 1000))
            lock.release()
        return result

    def _dispatch(self, action) -> None:
        kind = action.kind
        if kind == "key":
            keys = action.value.split("+")
            for key in keys: self.client.key_down(key)
            try: self.client.key_up(keys[-1])
            finally:
                for key in reversed(keys[:-1]): self.client.key_up(key)
        elif kind == "text": self.client.type_text(action.value)
        elif kind == "hold_key":
            self.client.key_down(action.key)
            try: self.sleep(action.duration_ms / 1000)
            finally: self.client.key_up(action.key)
        elif kind == "release_all": self.client.release_all()
        elif kind == "mouse_move": self.client.mouse_move(action.x, action.y)
        elif kind == "mouse_move_pct": self.client.mouse_move_pct(action.x_pct, action.y_pct)
        elif kind == "mouse_click":
            for _ in range(action.count):
                self.client.mouse_button(action.button, True); self.client.mouse_button(action.button, False)
        elif kind == "mouse_scroll": self.client.mouse_scroll(action.dx, action.dy)
        elif kind == "wait": self.sleep(action.duration_ms / 1000)
        else: raise ValueError("unsupported action type")

    def execute_workflow(self, workflow: WorkflowDefinition, *, approved: bool,
                         target: Optional[str] = None, ttl_s: float = 30.0) -> SequenceExecutionResult:
        if workflow._derived_revision() != workflow.revision:
            raise ValueError("workflow revision mismatch")
        actual_target = target or workflow.resolved_target or workflow.target
        if actual_target is None:
            raise ValueError("workflow invocation target required")
        if not workflow.target_independent and actual_target != workflow.target:
            raise ValueError("workflow target mismatch")
        bound = workflow.plan
        if bound.target != actual_target:
            from dataclasses import replace
            bound = replace(bound, target=actual_target)
        planned = self.plan(bound, workflow_revision=workflow.revision)
        return self.execute(self.authorize(planned, approved=approved, ttl_s=ttl_s))
