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
    auth = ex.execute_workflow(definition, approved=True)
    assert isinstance(auth, SequenceExecutionResult)
    assert auth.ok
