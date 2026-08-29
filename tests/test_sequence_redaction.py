import json

from kvmctl.journal import Journal
from kvmctl.machines import RACK, SessionState
from kvmctl.mcp_surface import dispatch_tool
from kvmctl.results import operation_result
from kvmctl.workflows import WorkflowRepository


SECRET_FIELDS = {
    "token": "token-value-do-not-log",
    "password": "password-value-do-not-log",
    "secret": "secret-value-do-not-log",
    "authorization": "authorization-value-do-not-log",
    "cookie": "cookie-value-do-not-log",
}


def test_journal_omits_all_sensitive_named_fields_and_values(tmp_path):
    journal = Journal(tmp_path / "journal.jsonl")
    journal.append({"operation": "sequence", "target": "pve2", **SECRET_FIELDS,
                    "nested": {name: value for name, value in SECRET_FIELDS.items()}})
    serialized = (tmp_path / "journal.jsonl").read_text(encoding="utf-8")
    assert all(name not in serialized for name in SECRET_FIELDS)
    assert all(value not in serialized for value in SECRET_FIELDS.values())


def test_sequence_result_serialization_excludes_secret_plan_values(tmp_path):
    session = SessionState()
    session.mark_selected(RACK["pve2"])
    session.mark_verified("test")
    plan = {"target": "pve2", "actions": [
        {"type": "text", "value": f"{name}={value}"}
        for name, value in SECRET_FIELDS.items()
    ]}
    context = {
        "client": object(), "session": session, "write_enabled": False,
        "workflow_repository": WorkflowRepository(()),
        "journal": Journal(tmp_path / "result-journal.jsonl"),
        "test_mode": True,
    }
    result = json.loads(dispatch_tool("kvm_sequence_plan", {"plan": plan}, context=context))
    serialized = json.dumps(result, sort_keys=True)
    assert result["ok"] is True
    assert all(value not in serialized for value in SECRET_FIELDS.values())


def test_operation_result_serialization_redacts_sensitive_named_fields():
    result = operation_result(
        operation="sequence", transport="kvm", read_only=False,
        evidence={**SECRET_FIELDS, "nested": SECRET_FIELDS},
        error={"code": "failed", "secret": SECRET_FIELDS["secret"]},
    )
    serialized = json.dumps(result, sort_keys=True)
    assert all(name not in serialized for name in SECRET_FIELDS)
    assert all(value not in serialized for value in SECRET_FIELDS.values())


def test_workflow_and_journal_outputs_are_deterministically_ordered(tmp_path):
    repo = WorkflowRepository.from_mappings([
        {"name": "zeta", "target": "pve2", "steps": [{"type": "wait", "duration_ms": 1}]},
        {"name": "alpha", "target": "pve2", "steps": [{"type": "wait", "duration_ms": 1}]},
    ])
    assert [item.name for item in repo.list()] == ["alpha", "zeta"]
    journal = Journal(tmp_path / "ordered.jsonl")
    journal.append({"z": 1, "a": 2, "sequence": 1})
    journal.append({"z": 3, "a": 4, "sequence": 2})
    lines = (tmp_path / "ordered.jsonl").read_text(encoding="utf-8").splitlines()
    assert lines == [
        '{"a":2,"sequence":1,"z":1}',
        '{"a":4,"sequence":2,"z":3}',
    ]
