import json

import pytest

from kvmctl.cli import build_parser, main
from kvmctl.machines import RACK, SessionState


PLAN = {"target": "pve2", "actions": [{"type": "key", "key": "ENTER"}], "max_duration_ms": 1000}


class FakeSurface:
    calls = []

    def __init__(self, client, session=None, host_runner=None):
        self.write_enabled = False

    def kvm_sequence_plan(self, plan):
        self.calls.append(("plan", plan))
        return {"operation": "kvm_sequence_plan", "ok": True, "evidence": {"target": plan["target"]}}

    def kvm_sequence_authorize(self, plan, *, approved, ttl_s):
        self.calls.append(("authorize", plan, approved, ttl_s))
        return {"operation": "kvm_sequence_authorize", "ok": True}

    def kvm_sequence_execute(self, plan, *, approved, ttl_s):
        self.calls.append(("execute", plan, approved, ttl_s))
        return {"operation": "kvm_sequence_execute", "ok": True}

    def kvm_workflow_list(self):
        self.calls.append(("workflow-list",))
        return {"operation": "kvm_workflow_list", "ok": True}

    def kvm_workflow_inspect(self, name, revision=None, target=None):
        self.calls.append(("workflow-inspect", name, revision, target))
        return {"operation": "kvm_workflow_inspect", "ok": True}

    def kvm_workflow_execute(self, name, revision, *, approved, target=None, ttl_s=30.0):
        self.calls.append(("workflow-execute", name, revision, approved, target, ttl_s))
        return {"operation": "kvm_workflow_execute", "ok": True}


def test_sequence_plan_accepts_inline_json_without_yes(monkeypatch, capsys):
    FakeSurface.calls = []
    monkeypatch.setattr("kvmctl.cli.SemanticSurface", FakeSurface)
    rc = main(["--url", "https://kvm.test", "sequence-plan", "--plan", json.dumps(PLAN)], client=object())
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["operation"] == "kvm_sequence_plan"
    assert FakeSurface.calls == [("plan", PLAN)]


def test_sequence_execute_requires_yes_before_delegating(monkeypatch, capsys):
    FakeSurface.calls = []
    monkeypatch.setattr("kvmctl.cli.SemanticSurface", FakeSurface)
    with pytest.raises(SystemExit):
        main(["--url", "https://kvm.test", "sequence-execute", "--plan", json.dumps(PLAN)], client=object())
    assert "--yes" in capsys.readouterr().err
    assert FakeSurface.calls == []


def test_invalid_plan_is_structured_nonzero_error(monkeypatch, capsys):
    monkeypatch.setattr("kvmctl.cli.SemanticSurface", FakeSurface)
    rc = main(["--url", "https://kvm.test", "sequence-plan", "--plan", "not-json"], client=object())
    out = json.loads(capsys.readouterr().out)
    assert rc != 0
    assert out["ok"] is False
    assert "error" in out


def test_named_and_inline_workflow_commands_have_json_envelopes(monkeypatch, capsys):
    FakeSurface.calls = []
    monkeypatch.setattr("kvmctl.cli.SemanticSurface", FakeSurface)
    for argv, operation in [
        (["workflow-list"], "kvm_workflow_list"),
        (["workflow-inspect", "demo", "--revision", "r1", "--target", "pve2"], "kvm_workflow_inspect"),
        (["--yes", "workflow-execute", "demo", "--revision", "r1", "--target", "pve2"], "kvm_workflow_execute"),
    ]:
        rc = main(["--url", "https://kvm.test", *argv], client=object())
        assert rc == 0
        assert json.loads(capsys.readouterr().out)["operation"] == operation


def test_sequence_plan_reads_file_and_writes_optional_output(monkeypatch, tmp_path, capsys):
    FakeSurface.calls = []
    monkeypatch.setattr("kvmctl.cli.SemanticSurface", FakeSurface)
    source = tmp_path / "plan.json"
    destination = tmp_path / "result.json"
    source.write_text(json.dumps(PLAN), encoding="utf-8")
    rc = main(["--url", "https://kvm.test", "sequence-plan", "--plan", str(source),
               "--out", str(destination)], client=object())
    assert rc == 0
    assert json.loads(destination.read_text(encoding="utf-8"))["operation"] == "kvm_sequence_plan"
    assert json.loads(capsys.readouterr().out)["operation"] == "kvm_sequence_plan"


def test_sequence_execute_passes_supplied_plan_for_exact_validation(monkeypatch, capsys):
    FakeSurface.calls = []
    monkeypatch.setattr("kvmctl.cli.SemanticSurface", FakeSurface)
    rc = main(["--url", "https://kvm.test", "--yes", "sequence-execute",
               "--plan", json.dumps(PLAN), "--approval-token", "opaque"], client=object())
    assert rc == 0
    assert FakeSurface.calls == [("execute", PLAN, True, 30.0)]


def test_cli_uses_supplied_verified_session_context(monkeypatch):
    seen = []
    class Surface(FakeSurface):
        def __init__(self, client, session=None, host_runner=None):
            seen.append(session)
            super().__init__(client, session, host_runner)
    monkeypatch.setattr("kvmctl.cli.SemanticSurface", Surface)
    session = SessionState()
    session.mark_selected(RACK["pve2"])
    session.mark_verified("test")
    assert main(["--url", "https://kvm.test", "sequence-plan", "--plan", json.dumps(PLAN)],
                client=object(), session=session) == 0
    assert seen == [session]


def test_cli_output_failure_is_structured_envelope(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("kvmctl.cli.SemanticSurface", FakeSurface)
    destination = tmp_path / "missing" / "result.json"
    rc = main(["--url", "https://kvm.test", "sequence-plan", "--plan", json.dumps(PLAN),
               "--out", str(destination)], client=object())
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["operation"] == "kvm_sequence_plan"
    assert {"target", "transport", "read_only", "ok", "changed", "state", "evidence",
            "warnings", "error", "next_actions"} <= out.keys()
    assert out["ok"] is False and out["error"]["code"]


def test_cli_semantic_failures_preserve_operation_result_envelope(capsys):
    class BrokenSurface:
        def __init__(self, *args, **kwargs):
            self.write_enabled = False
        def kvm_sequence_plan(self, plan):
            raise ValueError("target mismatch")
    # This verifies the real CLI error adapter, independent of semantic details.
    import kvmctl.cli
    old = kvmctl.cli.SemanticSurface
    kvmctl.cli.SemanticSurface = BrokenSurface
    try:
        rc = main(["--url", "https://kvm.test", "sequence-plan", "--plan", json.dumps(PLAN)], client=object())
    finally:
        kvmctl.cli.SemanticSurface = old
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["operation"] == "kvm_sequence_plan"
    assert out["error"]["code"] == "target mismatch"
    assert out["state"] == "aborted"
