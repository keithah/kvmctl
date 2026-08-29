import tempfile
from pathlib import Path

import pytest

from kvmctl.machines import RACK, SessionState
from kvmctl.policy import PolicyError
from kvmctl.semantics import SemanticSurface
from kvmctl.sequences import plan_hash, validate_plan
from kvmctl.sequence_executor import SequencePlanRecord
from kvmctl.workflows import WorkflowRepository


PLAN = {"target": "pve1", "actions": [{"type": "text", "value": "hello"}]}


class FakeExecutor:
    def __init__(self):
        self.plans = []
        self.authorizations = []
        self.executions = []

    def plan(self, plan, *, workflow_revision=None):
        self.plans.append((plan, workflow_revision))
        return SequencePlanRecord(plan, plan.target, plan_hash(plan), len(plan.actions), plan.max_duration_ms,
                                  workflow_revision=workflow_revision)

    def authorize(self, planned, *, approved, ttl_s=30.0):
        self.authorizations.append((planned, approved, ttl_s))
        return type("Authorization", (), {"plan": planned.plan, "target": planned.target, "plan_hash": planned.plan_hash, "expires_at": 9999999999, "workflow_revision": planned.workflow_revision})()

    def execute(self, authorization):
        self.executions.append(authorization)
        return type("Result", (), {"ok": True, "cleanup_ok": True, "target": authorization.target, "plan_hash": authorization.plan_hash, "elapsed_ms": 4, "completed_steps": 1, "error": "", "cleanup_errors": ()})()

    def execute_workflow(self, workflow, *, approved, target=None, ttl_s=30.0):
        planned = self.plan(workflow.plan, workflow_revision=workflow.revision)
        return self.execute(self.authorize(planned, approved=approved, ttl_s=ttl_s))


def surface(*, write_enabled=False, executor=None, repository=None):
    session = SessionState()
    session.mark_selected(RACK["pve1"])
    session.mark_verified("test")
    return SemanticSurface(object(), session=session, write_enabled=write_enabled,
                           sequence_executor=executor or FakeExecutor(),
                           workflow_repository=repository)


def test_sequence_plan_is_read_only_when_writes_disabled():
    executor = FakeExecutor()
    result = surface(executor=executor).kvm_sequence_plan(PLAN)
    assert result["operation"] == "kvm_sequence_plan"
    assert result["read_only"] is True
    assert result["ok"] is True
    assert result["evidence"]["action_count"] == 1
    assert len(executor.plans) == 1


def test_sequence_authorize_and_execute_require_write_gate():
    executor = FakeExecutor()
    surf = surface(executor=executor)
    planned = surf.kvm_sequence_plan(PLAN)
    with pytest.raises(PolicyError):
        surf.kvm_sequence_authorize(PLAN, approved=True)
    with pytest.raises(PolicyError):
        surf.kvm_sequence_execute(PLAN, approved=True)

    surf.write_enabled = True
    authorized = surf.kvm_sequence_authorize(PLAN, approved=True)
    assert authorized["operation"] == "kvm_sequence_authorize"
    executed = surf.kvm_sequence_execute(PLAN, approved=True)
    assert executed["operation"] == "kvm_sequence_execute"
    assert executed["evidence"]["cleanup_ok"] is True


def test_inline_and_named_workflow_share_executor_and_envelope():
    raw = {"name": "hello", "target": "pve1", "steps": PLAN["actions"]}
    repository = WorkflowRepository.from_mappings([raw])
    executor = FakeExecutor()
    surf = surface(write_enabled=True, executor=executor, repository=repository)
    inline = surf.kvm_sequence_execute(PLAN, approved=True)
    named = surf.kvm_workflow_execute("hello", repository.list()[0].revision, approved=True)
    assert inline["operation"] == "kvm_sequence_execute"
    assert named["operation"] == "kvm_workflow_execute"
    assert inline["evidence"]["plan_hash"] == named["evidence"]["plan_hash"]
    assert inline["evidence"]["action_count"] == named["evidence"]["action_count"] == 1
    assert len(executor.executions) == 2


def test_workflow_list_and_inspect_are_read_only():
    repository = WorkflowRepository.from_mappings([{"name": "hello", "target": "pve1", "steps": PLAN["actions"]}])
    surf = surface(repository=repository)
    listed = surf.kvm_workflow_list()
    inspected = surf.kvm_workflow_inspect("hello")
    assert listed["read_only"] and inspected["read_only"]
    assert listed["evidence"]["workflows"][0]["name"] == "hello"
    assert inspected["evidence"]["workflow"]["name"] == "hello"


def test_execution_error_is_top_level_and_redacted():
    class FailingExecutor(FakeExecutor):
        def execute(self, authorization):
            return type("Result", (), {"ok": False, "cleanup_ok": True, "target": authorization.target,
                                        "plan_hash": authorization.plan_hash, "elapsed_ms": 4,
                                        "completed_steps": 0, "error": "secret backend detail",
                                        "cleanup_errors": ()})()

    result = surface(write_enabled=True, executor=FailingExecutor()).kvm_sequence_execute(PLAN, approved=True)
    assert result["error"] == {"code": "secret backend detail", "retryable": False, "requires_human": False}
    assert "error" not in result["evidence"]


def test_read_only_catalog_entries_explicitly_disable_write_gate():
    from kvmctl.operations import TOOL_SPEC

    entries = {entry["name"]: entry for entry in TOOL_SPEC}
    for name in ("kvm_sequence_plan", "kvm_workflow_list", "kvm_workflow_inspect"):
        assert entries[name]["write_gate"] is False


def test_authorize_rejects_forged_or_mismatched_plan_records():
    executor = FakeExecutor()
    surf = surface(write_enabled=True, executor=executor)
    surf.kvm_sequence_plan(PLAN)
    record = executor.plan(validate_plan(PLAN))

    with pytest.raises(TypeError):
        surf.kvm_sequence_authorize(type("Forged", (), {"plan_hash": record.plan_hash})(), approved=True)

    forged = SequencePlanRecord(record.plan, "other-target", record.plan_hash, record.action_count,
                                record.max_duration_ms)
    with pytest.raises(ValueError):
        surf.kvm_sequence_authorize(forged, approved=True)
