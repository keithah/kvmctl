"""Target-bound, explicitly-dispatched KVM sequence execution."""
from __future__ import annotations

from dataclasses import dataclass, replace
import secrets
import time
from typing import Callable, Optional
import hashlib
import math
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from .journal import Journal
from .machines import SessionState, device_lock
from .sequences import SequencePlan, validate_plan, plan_hash
from .workflows import WorkflowDefinition
from .results import normalize_error


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
    binding: str = ""

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
    return normalize_error(exc, default=f"{phase} failed") or f"{phase} failed"


def asyncio_cancelled_type():
    # Import lazily so this module remains usable in minimal environments.
    try:
        import asyncio
        return asyncio.CancelledError
    except ImportError:  # pragma: no cover
        return type(None)


class SequenceExecutor:
    SCREEN_MAX_BYTES = 4 * 1024 * 1024
    SCREEN_MAX_TEXT = 1 * 1024 * 1024
    MAX_AUTHORIZATION_TTL_S = 30.0

    def __init__(self, client, session: SessionState, journal: Journal,
                 *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 device_id: str | None = None, stream_owned: bool = False,
                 authorization_store=None):
        self.client, self.session, self.journal = client, session, journal
        self.authorization_store = authorization_store
        self.clock, self.sleep = clock, sleep
        # An explicit identity is preferred; a client-derived fallback avoids
        # making unrelated client instances contend on one process-global key.
        self.device_id = device_id if device_id is not None else self._client_identity(client)
        self.stream_owned = stream_owned
        self._authorizations: dict[str, SequenceAuthorization] = {}
        self._used_authorizations: set[str] = set()
        self._active_target = None
        self._active_plan_hash = ""
        self._active_deadline = None
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

    def _binding_identity(self) -> str:
        rec = self.session.current
        endpoint = self._client_identity(self.client)
        material = f"{endpoint}|{rec.machine if rec else ''}|{rec.port if rec else ''}|{rec.detail if rec else ''}|{rec.at if rec else ''}"
        return "sha256:" + hashlib.sha256(material.encode()).hexdigest()

    def reject(self, reason: str, *, target=None, plan_hash_value="", start=None) -> None:
        # Rejections may happen before a canonical plan exists, but the journal
        # still gets a non-empty, deterministic evidence identifier.
        reason = normalize_error(reason, default="operation rejected") or "operation rejected"
        if not plan_hash_value:
            plan_hash_value = "sha256:" + hashlib.sha256(reason.encode("utf-8")).hexdigest()
        ended = time.time()
        began = self.clock() if start is None else start
        self._checkpoint("aborted", target=target, plan_hash=plan_hash_value,
                         reason=reason, final_result="failure", started_at=began,
                         ended_at=ended, duration_ms=max(0, int((self.clock() - began) * 1000)))

    def _checkpoint(self, transition: str, *, target: str | None, plan_hash: str, **details) -> None:
        current = self.session.current
        details.setdefault("target_verification", bool(current and current.verified and current.machine == target))
        details.setdefault("timestamp", time.time())
        self.journal.checkpoint(operation="sequence", target=target, transition=transition,
                                plan_hash=plan_hash, **details)

    def _abort_preflight(self, authorization: SequenceAuthorization, reason: str) -> None:
        self._checkpoint("aborted", target=authorization.target,
                         plan_hash=authorization.plan_hash, reason=reason,
                         target_verification=(authorization.plan.target == authorization.target),
                         final_result="failure", started_at=time.time(), ended_at=time.time(), duration_ms=0)

    def _abort(self, *, target: str | None, reason: str,
               plan_hash_value: str | None = None) -> None:
        details = {"reason": reason, "final_result": "failure",
                   "ended_at": time.time(), "duration_ms": 0}
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
        if (isinstance(ttl_s, bool) or not isinstance(ttl_s, (int, float))
                or not math.isfinite(ttl_s)
                or not (0 < ttl_s <= self.MAX_AUTHORIZATION_TTL_S)):
            self._abort(target=planned.target, plan_hash_value=planned.plan_hash,
                        reason="authorization ttl invalid")
            raise ValueError("authorization ttl must be finite and between 0 and 30 seconds")
        now = self.clock()
        auth = SequenceAuthorization(planned.plan, planned.target, planned.plan_hash,
                                     now + ttl_s, planned.workflow_revision,
                                     token=secrets.token_urlsafe(32), session_id=id(self.session), binding=self._binding_identity())
        self._authorizations[auth.token] = auth
        if self.authorization_store is not None:
            self.authorization_store.put(auth)
        self._checkpoint("authorized", target=auth.target, plan_hash=auth.plan_hash,
                         expires_at=auth.expires_at, workflow_revision=auth.workflow_revision)
        return auth

    def execute(self, authorization: SequenceAuthorization | str, *,
                expected_plan: SequencePlan | None = None,
                expected_workflow_revision: str | None = None,
                expected_target: str | None = None) -> SequenceExecutionResult:
        start = self.clock()
        token = authorization if isinstance(authorization, str) else None
        from_store = False
        if token is not None:
            authorization = self._authorizations.get(token)
            if authorization is None and self.authorization_store is not None:
                peek = getattr(self.authorization_store, "peek", None)
                authorization = peek(token, binding=self._binding_identity()) if peek is not None else self.authorization_store.take(token, binding=self._binding_identity())
                from_store = authorization is not None
        if not isinstance(authorization, SequenceAuthorization) or not authorization.token:
            reason = "authorization invalid" if token is not None and self.authorization_store is not None else "authorization missing"
            self.reject(reason, target=None, plan_hash_value="", start=start)
            return SequenceExecutionResult(False, True, "", "", error=reason)
        # Validate the caller's control fields before consuming the single-use
        # record.  This prevents a valid token being burned by a mismatched
        # workflow invocation or an inline plan.
        if expected_plan is not None and plan_hash(validate_plan(expected_plan)) != authorization.plan_hash:
            self.reject("plan mismatch", target=authorization.target, plan_hash_value=authorization.plan_hash, start=start)
            return SequenceExecutionResult(False, True, authorization.target, authorization.plan_hash,
                                           error="plan mismatch")
        if expected_workflow_revision is not None and authorization.workflow_revision != expected_workflow_revision:
            self.reject("workflow revision mismatch", target=authorization.target, plan_hash_value=authorization.plan_hash, start=start)
            return SequenceExecutionResult(False, True, authorization.target, authorization.plan_hash,
                                           error="workflow revision mismatch")
        if expected_target is not None and authorization.target != expected_target:
            self.reject("workflow target mismatch", target=authorization.target, plan_hash_value=authorization.plan_hash, start=start)
            return SequenceExecutionResult(False, True, authorization.target, authorization.plan_hash,
                                           error="workflow target mismatch")
        if self.authorization_store is not None:
            consumed = self.authorization_store.take(authorization.token, binding=self._binding_identity())
            if consumed is None:
                reason = "authorization invalid" if token not in self._authorizations else "authorization used"
                self.reject(reason, target=authorization.target, plan_hash_value=authorization.plan_hash, start=start)
                return SequenceExecutionResult(False, True, authorization.target, authorization.plan_hash,
                                               error=reason)
            # Use the authenticated persisted record, not caller state.
            authorization = consumed
        registered = self._authorizations.get(authorization.token)
        if self.authorization_store is not None and registered is None:
            registered = authorization

        if self.authorization_store is None and (registered is not authorization or authorization.session_id != id(self.session)):
            self._abort_preflight(authorization, "authorization invalid")
            return SequenceExecutionResult(False, True, authorization.target, authorization.plan_hash,
                                           error="authorization invalid")
        if authorization.token in self._used_authorizations:
            self._abort_preflight(authorization, "authorization used")
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
            self._active_target, self._active_plan_hash, self._active_deadline = authorization.target, authorization.plan_hash, deadline
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
            self._active_target, self._active_plan_hash, self._active_deadline = None, "", None
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
        remaining = (self._active_deadline or (self.clock() + 1.0)) - self.clock()
        if remaining <= 0:
            raise TimeoutError("sequence deadline expired")
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            frame = executor.submit(snapshot).result(timeout=remaining)
            if not isinstance(frame, (bytes, bytearray)) or len(frame) > self.SCREEN_MAX_BYTES:
                raise RuntimeError("screen assertion unavailable")
            remaining = (self._active_deadline or (self.clock() + 1.0)) - self.clock()
            if remaining <= 0:
                raise TimeoutError("sequence deadline expired")
            text = executor.submit(ocr, bytes(frame)).result(timeout=remaining)
        except (FutureTimeout, TimeoutError, OSError, ValueError, TypeError) as exc:
            raise RuntimeError("screen assertion unavailable") from exc
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        if not isinstance(text, str) or len(text) > self.SCREEN_MAX_TEXT:
            raise RuntimeError("screen assertion unavailable")
        if action.contains not in text:
            self._checkpoint("screen_assertion_failed", target=self._active_target,
                             plan_hash=self._active_plan_hash, evidence="mismatch")
            raise RuntimeError("screen assertion failed")

    def execute_workflow(self, workflow: WorkflowDefinition, *, approval_token: str | None = None,
                         approved: bool = False, target: Optional[str] = None,
                         ttl_s: float = 30.0) -> SequenceExecutionResult:
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
        if not approval_token:
            raise ValueError("approval_token is required; authorize the exact workflow first")
        return self.execute(approval_token, expected_plan=bound,
                            expected_workflow_revision=workflow.revision,
                            expected_target=actual_target)
