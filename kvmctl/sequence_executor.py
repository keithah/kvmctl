"""Target-bound, explicitly-dispatched KVM sequence execution."""
from __future__ import annotations

from dataclasses import dataclass, replace
import secrets
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
    token: str = ""
    session_id: int = 0

    def __post_init__(self) -> None:
        if self.plan.target != self.target:
            raise ValueError("authorization plan target mismatch")


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


def _failure_reason(exc: BaseException, *, phase: str = "action") -> str:
    """Return a bounded reason that never includes an exception message."""
    if isinstance(exc, (KeyboardInterrupt, GeneratorExit, asyncio_cancelled_type())):
        return "cancelled"
    if isinstance(exc, TimeoutError):
        return "deadline exceeded"
    if isinstance(exc, RuntimeError) and str(exc) in {"screen assertion failed", "screen assertion unavailable"}:
        return str(exc)
    return f"{phase} failed"


def asyncio_cancelled_type():
    # Import lazily so this module remains usable in minimal environments.
    try:
        import asyncio
        return asyncio.CancelledError
    except ImportError:  # pragma: no cover
        return type(None)


class SequenceExecutor:
    def __init__(self, client, session: SessionState, journal: Journal,
                 *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 device_id: str | None = None, stream_owned: bool = False):
        self.client, self.session, self.journal = client, session, journal
        self.clock, self.sleep = clock, sleep
        # An explicit identity is preferred; a client-derived fallback avoids
        # making unrelated client instances contend on one process-global key.
        self.device_id = device_id if device_id is not None else self._client_identity(client)
        self.stream_owned = stream_owned
        self._authorizations: dict[str, SequenceAuthorization] = {}
        self._used_authorizations: set[str] = set()
        self._dispatch_table = {
            "key": self._dispatch_key,
            "text": self._dispatch_text,
            "hold_key": self._dispatch_hold_key,
            "release_all": self._dispatch_release_all,
            "mouse_move": self._dispatch_mouse_move,
            "mouse_move_pct": self._dispatch_mouse_move_pct,
            "mouse_click": self._dispatch_mouse_click,
            "mouse_scroll": self._dispatch_mouse_scroll,
            "wait": self._dispatch_wait,
            "assert_screen": self._dispatch_assert_screen,
        }

    @staticmethod
    def _client_identity(client) -> str:
        for attr in ("base_url", "url", "host"):
            value = getattr(client, attr, None)
            if isinstance(value, str) and value:
                return f"client:{attr}:{value}"
        return f"client:{id(client)}"

    def _checkpoint(self, transition: str, *, target: str | None, plan_hash: str, **details) -> None:
        current = self.session.current
        details.setdefault("target_verification", bool(current and current.verified and current.machine == target))
        details.setdefault("timestamp", time.time())
        self.journal.checkpoint(operation="sequence", target=target, transition=transition,
                                plan_hash=plan_hash, **details)

    def _abort_preflight(self, authorization: SequenceAuthorization, reason: str) -> None:
        self._checkpoint("aborted", target=authorization.target,
                         plan_hash=authorization.plan_hash, reason=reason,
                         final_result="failure", ended_at=time.time(), duration_ms=0)

    def _abort(self, *, target: str | None, reason: str,
               plan_hash_value: str | None = None) -> None:
        details = {"reason": reason}
        self._checkpoint("aborted", target=target, plan_hash=plan_hash_value or "", **details)

    def plan(self, plan: SequencePlan, *, workflow_revision: str | None = None) -> SequencePlanRecord:
        target = getattr(plan, "target", None)
        if isinstance(plan, dict):
            target = plan.get("target")
        try:
            canonical = validate_plan(plan)
        except (TypeError, ValueError, KeyError):
            self._abort(target=target if isinstance(target, str) else None,
                        reason="plan validation failed")
            raise
        current = self.session.current
        if current is None or not current.verified:
            self._abort(target=canonical.target, reason="target session is not verified")
            raise ValueError("target session is not verified")
        if current.machine != canonical.target:
            self._abort(target=canonical.target, reason="target mismatch")
            raise ValueError("target mismatch")
        digest = plan_hash(canonical)
        record = SequencePlanRecord(canonical, canonical.target, digest, len(canonical.actions),
                                    canonical.max_duration_ms, workflow_revision=workflow_revision)
        self._checkpoint("planned", target=record.target, plan_hash=digest,
                         action_count=record.action_count, max_duration_ms=record.max_duration_ms,
                         workflow_revision=workflow_revision)
        return record

    def authorize(self, planned: SequencePlanRecord, *, approved: bool,
                  ttl_s: float = 30.0) -> SequenceAuthorization:
        if not approved:
            self._abort(target=planned.target, plan_hash_value=planned.plan_hash,
                        reason="plan must be approved")
            raise ValueError("plan must be approved")
        if ttl_s <= 0:
            self._abort(target=planned.target, plan_hash_value=planned.plan_hash,
                        reason="authorization ttl must be positive")
            raise ValueError("authorization ttl must be positive")
        now = self.clock()
        auth = SequenceAuthorization(planned.plan, planned.target, planned.plan_hash,
                                     now + ttl_s, planned.workflow_revision,
                                     token=secrets.token_urlsafe(32), session_id=id(self.session))
        self._authorizations[auth.token] = auth
        self._checkpoint("authorized", target=auth.target, plan_hash=auth.plan_hash,
                         expires_at=auth.expires_at, workflow_revision=auth.workflow_revision)
        return auth

    def execute(self, authorization: SequenceAuthorization | str) -> SequenceExecutionResult:
        start = self.clock()
        if isinstance(authorization, str):
            authorization = self._authorizations.get(authorization)
        if not isinstance(authorization, SequenceAuthorization) or not authorization.token:
            return SequenceExecutionResult(False, True, "", "", error="authorization missing")
        registered = self._authorizations.get(authorization.token)
        if registered is not authorization or authorization.session_id != id(self.session):
            return SequenceExecutionResult(False, True, authorization.target, authorization.plan_hash,
                                           error="authorization invalid")
        if authorization.token in self._used_authorizations:
            return SequenceExecutionResult(False, True, authorization.target, authorization.plan_hash,
                                           error="authorization used")
        self._used_authorizations.add(authorization.token)
        result = SequenceExecutionResult(False, True, authorization.target, authorization.plan_hash)
        lock = device_lock(self.device_id)
        if not lock.acquire(blocking=False):
            result.error = "device lock conflict"
            self._abort_preflight(authorization, result.error)
            return result
        try:
            # Repeat the binding checks at the side-effect boundary.
            if authorization.plan.target != authorization.target:
                result.error = "authorization target mismatch"
                self._abort_preflight(authorization, result.error)
                return result
            if self.clock() >= authorization.expires_at:
                result.error = "authorization expired"
                self._abort_preflight(authorization, result.error)
                return result
            if plan_hash(authorization.plan) != authorization.plan_hash:
                result.error = "plan hash changed"
                self._abort_preflight(authorization, result.error)
                return result
            current = self.session.current
            if current is None or not current.verified or current.machine != authorization.target:
                result.error = "target mismatch or session not verified"
                self._abort_preflight(authorization, result.error)
                return result
            deadline = start + authorization.plan.max_duration_ms / 1000.0
            self._checkpoint("started", target=authorization.target, plan_hash=authorization.plan_hash)
            for index, action in enumerate(authorization.plan.actions):
                if self.clock() >= deadline:
                    raise TimeoutError
                self._checkpoint("step_started", target=authorization.target,
                                 plan_hash=authorization.plan_hash, step=index)
                self._dispatch(action)
                result.completed_steps = index + 1
                # Enforce the deadline after every action, including the last.
                if self.clock() >= deadline:
                    raise TimeoutError
                self._checkpoint("step_completed", target=authorization.target,
                                 plan_hash=authorization.plan_hash, step=index)
            if self.clock() >= deadline:
                raise TimeoutError
            result.ok = True
            self._checkpoint("completed", target=authorization.target,
                             plan_hash=authorization.plan_hash, steps=result.completed_steps,
                             started_at=start, ended_at=self.clock(),
                             duration_ms=max(0, int((self.clock() - start) * 1000)),
                             final_result="success")
        except BaseException as exc:
            result.error = _failure_reason(exc)
            self._abort_preflight(authorization, result.error)
        finally:
            cleanup_errors: list[str] = []
            try:
                self.client.release_all()
            except BaseException:
                cleanup_errors.append("release_all failed")
            try:
                if self.stream_owned:
                    close_stream = getattr(self.client, "close_stream", None)
                    if close_stream is not None:
                        close_stream()
            except BaseException:
                cleanup_errors.append("close_stream failed")
            result.cleanup_errors = tuple(cleanup_errors)
            result.cleanup_ok = not cleanup_errors
            if cleanup_errors:
                result.ok = False
                self._checkpoint("cleanup_failed", target=authorization.target,
                                 plan_hash=authorization.plan_hash, error_count=len(cleanup_errors))
            result.elapsed_ms = max(0, int((self.clock() - start) * 1000))
            try:
                lock.release()
            except BaseException:
                result.cleanup_ok = False
                result.ok = False
                result.cleanup_errors = (*result.cleanup_errors, "lock release failed")
        return result

    def _dispatch(self, action) -> None:
        try:
            handler = self._dispatch_table[action.kind]
        except KeyError:
            raise ValueError("unsupported action type") from None
        handler(action)

    def _dispatch_key(self, action) -> None:
        keys = action.value.split("+")
        for key in keys:
            self.client.key_down(key)
        try:
            self.client.key_up(keys[-1])
        finally:
            for key in reversed(keys[:-1]):
                self.client.key_up(key)

    def _dispatch_text(self, action) -> None: self.client.type_text(action.value)

    def _dispatch_hold_key(self, action) -> None:
        self.client.key_down(action.key)
        try:
            self.sleep(action.duration_ms / 1000)
        finally:
            self.client.key_up(action.key)

    def _dispatch_release_all(self, action) -> None: self.client.release_all()
    def _dispatch_mouse_move(self, action) -> None: self.client.mouse_move(action.x, action.y)
    def _dispatch_mouse_move_pct(self, action) -> None: self.client.mouse_move_pct(action.x_pct, action.y_pct)

    def _dispatch_mouse_click(self, action) -> None:
        for _ in range(action.count):
            self.client.mouse_button(action.button, True)
            self.client.mouse_button(action.button, False)

    def _dispatch_mouse_scroll(self, action) -> None: self.client.mouse_scroll(action.dx, action.dy)
    def _dispatch_wait(self, action) -> None: self.sleep(action.duration_ms / 1000)

    def _dispatch_assert_screen(self, action) -> None:
        snapshot = getattr(self.client, "snapshot_jpeg", None)
        ocr = getattr(self.client, "ocr", None)
        if snapshot is None or ocr is None:
            raise RuntimeError("screen assertion unavailable")
        text = ocr(snapshot())
        if not isinstance(text, str) or action.contains not in text:
            self._checkpoint("screen_assertion_failed", target=self.session.current.machine if self.session.current else None,
                             plan_hash="", evidence="mismatch")
            raise RuntimeError("screen assertion failed")

    def execute_workflow(self, workflow: WorkflowDefinition, *, approved: bool,
                         target: Optional[str] = None, ttl_s: float = 30.0) -> SequenceExecutionResult:
        actual_target = target or workflow.resolved_target or workflow.target
        if workflow._derived_revision() != workflow.revision:
            self._abort(target=actual_target, reason="workflow revision mismatch")
            raise ValueError("workflow revision mismatch")
        if actual_target is None:
            self._abort(target=None, reason="workflow invocation target required")
            raise ValueError("workflow invocation target required")
        if not workflow.target_independent and actual_target != workflow.target:
            self._abort(target=actual_target, reason="workflow target mismatch")
            raise ValueError("workflow target mismatch")
        bound = workflow.plan
        if bound.target != actual_target:
            bound = replace(bound, target=actual_target)
        planned = self.plan(bound, workflow_revision=workflow.revision)
        return self.execute(self.authorize(planned, approved=approved, ttl_s=ttl_s))
