import json

import pytest

from kvmctl.journal import Journal
from kvmctl.machines import RACK, SessionState
from kvmctl.sequence_executor import (
    SequenceExecutor, SequenceAuthorization, SequenceExecutionResult,
)
from kvmctl.sequences import SequencePlan


class FakeClient:
    def __init__(self):
        self.calls = []
        self._held_keys = set()
        self._stream = None
        self.fail_on = None
        self.fail_cleanup = False

    def key_down(self, key):
        self.calls.append(("key_down", key)); self._held_keys.add(key)
        if self.fail_on == key: raise RuntimeError("hid failure")

    def key_up(self, key):
        self.calls.append(("key_up", key)); self._held_keys.discard(key)

    def type_text(self, text):
        self.calls.append(("text", text))
        if self.fail_on == "text": raise RuntimeError("text failure")

    def release_all(self):
        self.calls.append(("release_all",))
        if self.fail_cleanup: raise RuntimeError("cleanup failure")
        self._held_keys.clear(); return []

    def close_stream(self):
        self.calls.append(("close_stream",))
        if self.fail_cleanup: raise RuntimeError("stream cleanup failure")
        self._stream = None

    def mouse_move(self, x, y): self.calls.append(("mouse_move", x, y))
    def mouse_move_pct(self, x, y): self.calls.append(("mouse_move_pct", x, y))
    def mouse_button(self, button, state): self.calls.append(("mouse_button", button, state))
    def mouse_scroll(self, dx, dy): self.calls.append(("mouse_scroll", dx, dy))


def ready_session(target="pve2"):
    session = SessionState(); session.mark_selected(RACK[target]); session.mark_verified("test")
    return session


def make_executor(tmp_path, client=None, session=None, clock=None):
    return SequenceExecutor(client or FakeClient(), session or ready_session(),
                            Journal(tmp_path / "journal.jsonl"), clock=clock or (lambda: 0.0),
                            sleep=lambda _: None)


def test_plans_authorizes_and_executes_target_bound_plan(tmp_path):
    client = FakeClient(); executor = make_executor(tmp_path, client)
    plan = SequencePlan.from_mapping({"target": "pve2", "actions": [
        {"type": "key", "value": "Enter"}, {"type": "text", "value": "hostname"}]})
    planned = executor.plan(plan)
    assert planned.target == "pve2" and planned.plan_hash.startswith("sha256:")
    authorized = executor.authorize(planned, approved=True, ttl_s=30)
    result = executor.execute(authorized)
    assert result.ok is True and result.cleanup_ok is True
    assert ("text", "hostname") in client.calls


def test_rejects_unverified_or_mismatched_target_and_expired_authorization(tmp_path):
    with pytest.raises(ValueError, match="verified"):
        make_executor(tmp_path, session=SessionState()).plan({"target":"pve2","actions":[{"type":"release_all"}]})
    ex = make_executor(tmp_path); planned = ex.plan({"target":"pve2","actions":[{"type":"release_all"}]})
    with pytest.raises(ValueError, match="approved"):
        ex.authorize(planned, approved=False)
    auth = ex.authorize(planned, approved=True, ttl_s=1)
    ex.clock = lambda: 2.0
    result = ex.execute(auth)
    assert not result.ok and "expired" in result.error


@pytest.mark.parametrize("ttl", [float("nan"), float("inf"), float("-inf"), 30.001])
def test_authorize_rejects_nonfinite_and_overlong_ttl(tmp_path, ttl):
    ex = make_executor(tmp_path)
    planned = ex.plan({"target": "pve2", "actions": [{"type": "release_all"}]})
    with pytest.raises(ValueError, match="ttl"):
        ex.authorize(planned, approved=True, ttl_s=ttl)


def test_stops_after_action_failure_and_cleanup_failure_is_unsuccessful(tmp_path):
    client = FakeClient(); client.fail_on = "text"
    ex = make_executor(tmp_path, client)
    p = ex.plan({"target":"pve2","actions":[{"type":"text","value":"x"},{"type":"key","value":"Enter"}]})
    result = ex.execute(ex.authorize(p, approved=True))
    assert not result.ok and ("key_down", "Enter") not in client.calls
    client = FakeClient(); client.fail_cleanup = True
    ex = make_executor(tmp_path, client)
    p = ex.plan({"target":"pve2","actions":[{"type":"release_all"}]})
    result = ex.execute(ex.authorize(p, approved=True))
    assert not result.ok and not result.cleanup_ok


def test_workflow_revision_is_bound(tmp_path):
    from kvmctl.workflows import WorkflowDefinition
    definition = WorkflowDefinition.from_mapping({"name":"demo","target":"pve2","steps":[{"type":"release_all"}]})
    ex = make_executor(tmp_path)
    planned = ex.plan(definition.plan, workflow_revision=definition.revision)
    auth = ex.authorize(planned, approved=True)
    result = ex.execute(auth, expected_plan=definition.plan,
                        expected_workflow_revision=definition.revision,
                        expected_target=definition.target)
    assert isinstance(result, SequenceExecutionResult)
    assert result.ok


def test_changed_hash_and_authorization_target_are_rejected(tmp_path):
    ex = make_executor(tmp_path)
    planned = ex.plan({"target": "pve2", "actions": [{"type": "release_all"}]})
    auth = ex.authorize(planned, approved=True)
    object.__setattr__(auth, "plan_hash", "sha256:changed")
    result = ex.execute(auth)
    assert not result.ok and result.error == "plan hash changed"
    with pytest.raises(ValueError, match="target mismatch"):
        SequenceAuthorization(planned.plan, "pve1", planned.plan_hash, 30)


def test_execute_rejects_authorization_target_mismatch_and_journals_abort(tmp_path):
    ex = make_executor(tmp_path)
    planned = ex.plan({"target": "pve2", "actions": [{"type": "text", "value": "must-not-run"}]})
    authorization = ex.authorize(planned, approved=True)
    object.__setattr__(authorization, "target", "pve1")

    result = ex.execute(authorization)

    assert not result.ok
    assert result.error == "authorization target mismatch"
    assert ex.client.calls == [("release_all",)]
    records = [json.loads(line) for line in (tmp_path / "journal.jsonl").read_text().splitlines()]
    assert records[-1]["transition"] == "aborted"
    assert records[-1]["target"] == "pve1"
    assert records[-1]["reason"] == "authorization target mismatch"
    assert records[-1]["final_result"] == "failure"
    assert "ended_at" in records[-1] and "duration_ms" in records[-1]


def test_replayed_authorization_is_journaled_with_bound_identity(tmp_path):
    ex = make_executor(tmp_path)
    authorization = ex.authorize(
        ex.plan({"target": "pve2", "actions": [{"type": "release_all"}]}),
        approved=True,
    )
    assert ex.execute(authorization).ok
    replay = ex.execute(authorization.token)
    assert not replay.ok and replay.error == "authorization used"
    records = [json.loads(line) for line in (tmp_path / "journal.jsonl").read_text().splitlines()]
    assert records[-1]["transition"] == "aborted"
    assert records[-1]["target"] == authorization.target
    assert records[-1]["plan_hash"] == authorization.plan_hash
    assert records[-1]["final_result"] == "failure"
    assert "timestamp" in records[-1] and "duration_ms" in records[-1]
    assert "target_verification" in records[-1]


def test_lock_conflict_is_journaled_and_deadline_checked_after_last_action(tmp_path):
    client = FakeClient()
    ex = make_executor(tmp_path, client)
    ex.device_id = "shared-test-lock"
    lock = __import__("kvmctl.machines", fromlist=["device_lock"]).device_lock(ex.device_id)
    assert lock.acquire(blocking=False)
    try:
        result = ex.execute(ex.authorize(ex.plan({"target":"pve2", "actions":[{"type":"release_all"}]}), approved=True))
        assert result.error == "device lock conflict"
        records = [json.loads(line) for line in (tmp_path / "journal.jsonl").read_text().splitlines()]
        assert records[-1]["transition"] == "aborted"
        assert records[-1]["reason"] == "device lock conflict"
    finally:
        lock.release()
    ticks = iter([0.0, 0.0, 0.0, 0.002, 0.002])
    ex = SequenceExecutor(client, ready_session(), Journal(tmp_path / "deadline.jsonl"),
                          clock=lambda: next(ticks), sleep=lambda _: None,
                          device_id="deadline-test")
    plan = ex.plan({"target":"pve2", "max_duration_ms": 1, "actions":[{"type":"release_all"}]})
    result = ex.execute(ex.authorize(plan, approved=True))
    assert not result.ok and result.error == "deadline exceeded"


def test_cancellation_attempts_all_cleanup_and_redacts_exception(tmp_path):
    class CancelClient(FakeClient):
        def key_down(self, key):
            self.calls.append(("key_down", key))
            raise KeyboardInterrupt("contains secret-token")
        def release_all(self):
            self.calls.append(("release_all",))
            raise KeyboardInterrupt("cleanup secret")
        def close_stream(self):
            self.calls.append(("close_stream",))
            raise KeyboardInterrupt("stream secret")

    client = CancelClient()
    ex = SequenceExecutor(client, ready_session(), Journal(tmp_path / "cancel.jsonl"),
                          clock=lambda: 0.0, sleep=lambda _: None, device_id="cancel-test",
                          stream_owned=True)
    plan = ex.plan({"target":"pve2", "actions":[{"type":"key", "value":"Enter"}]})
    result = ex.execute(ex.authorize(plan, approved=True))
    assert result.error == "cancelled"
    assert result.cleanup_errors == ("release_all failed", "close_stream failed")
    assert ("release_all",) in client.calls and ("close_stream",) in client.calls
    text = (tmp_path / "cancel.jsonl").read_text()
    assert "secret" not in text and "contains" not in text


def test_workflow_revision_mismatch_is_aborted(tmp_path):
    from kvmctl.workflows import WorkflowDefinition
    workflow = WorkflowDefinition.from_mapping({"name":"demo2", "target":"pve2", "steps":[{"type":"release_all"}]})
    object.__setattr__(workflow, "revision", "sha256:changed")
    ex = make_executor(tmp_path)
    with pytest.raises(ValueError, match="workflow revision mismatch"):
        ex.execute_workflow(workflow, approved=True)
    records = [json.loads(line) for line in (tmp_path / "journal.jsonl").read_text().splitlines()]
    assert records[-1]["transition"] == "aborted"


def test_execute_workflow_target_rejection_is_journaled(tmp_path):
    from kvmctl.workflows import WorkflowDefinition
    workflow = WorkflowDefinition.from_mapping({"name":"bound", "target":"pve2", "steps":[{"type":"release_all"}]})
    ex = make_executor(tmp_path)
    with pytest.raises(ValueError, match="workflow target mismatch"):
        ex.execute_workflow(workflow, approved=True, target="pve1")
    records = [json.loads(line) for line in (tmp_path / "journal.jsonl").read_text().splitlines()]
    assert records[-1]["transition"] == "aborted"
    assert records[-1]["reason"] == "workflow target mismatch"


def test_execute_workflow_missing_target_is_journaled(tmp_path):
    from kvmctl.workflows import WorkflowDefinition
    workflow = WorkflowDefinition.from_mapping({"name":"independent", "target_independent":True, "steps":[{"type":"release_all"}]})
    ex = make_executor(tmp_path)
    with pytest.raises(ValueError, match="target required"):
        ex.execute_workflow(workflow, approved=True)
    records = [json.loads(line) for line in (tmp_path / "journal.jsonl").read_text().splitlines()]
    assert records[-1]["transition"] == "aborted"
    assert records[-1]["target"] is None


def test_plan_and_authorization_rejections_are_journaled(tmp_path):
    ex = make_executor(tmp_path, session=SessionState())
    with pytest.raises(ValueError, match="verified"):
        ex.plan({"target":"pve2","actions":[{"type":"release_all"}]})
    records = [json.loads(line) for line in (tmp_path / "journal.jsonl").read_text().splitlines()]
    assert records[-1]["transition"] == "aborted"
    assert records[-1]["reason"] == "target session is not verified"

    ex = make_executor(tmp_path)
    planned = ex.plan({"target":"pve2","actions":[{"type":"release_all"}]})
    with pytest.raises(ValueError, match="approved"):
        ex.authorize(planned, approved=False)
    records = [json.loads(line) for line in (tmp_path / "journal.jsonl").read_text().splitlines()]
    assert records[-1]["transition"] == "aborted"
    assert records[-1]["reason"] == "plan must be approved"


def test_unowned_stream_is_not_closed_but_owned_stream_is_closed(tmp_path):
    client = FakeClient()
    ex = make_executor(tmp_path, client)
    planned = ex.plan({"target":"pve2","actions":[{"type":"release_all"}]})
    ex.execute(ex.authorize(planned, approved=True))
    assert ("close_stream",) not in client.calls

    client = FakeClient()
    ex = SequenceExecutor(client, ready_session(), Journal(tmp_path / "owned.jsonl"),
                          clock=lambda: 0.0, sleep=lambda _: None,
                          device_id="owned-stream", stream_owned=True)
    planned = ex.plan({"target":"pve2","actions":[{"type":"release_all"}]})
    ex.execute(ex.authorize(planned, approved=True))
    assert ("close_stream",) in client.calls
