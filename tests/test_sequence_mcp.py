import json

import pytest

from conftest import FakeKvmd
from kvmctl.client import KvmClient
from kvmctl.journal import Journal
from kvmctl.machines import RACK, SessionState
from kvmctl.mcp_surface import dispatch_tool
from kvmctl.workflows import WorkflowRepository


def client_and_context(tmp_path, *, write_enabled=False, workflows=()):
    import httpx
    fake = FakeKvmd()
    fake.add("POST", "/api/hid/events/send_key")
    fake.add("POST", "/api/hid/events/send_mouse")
    fake.add("POST", "/api/hid/reset")
    c = KvmClient("https://kvm.test", verify=False)
    c._transport = httpx.MockTransport(fake.handle)
    c.set_token("token")
    session = SessionState(); session.mark_selected(RACK["pve2"]); session.mark_verified("pve2")
    return c, {"client": c, "session": session, "write_enabled": write_enabled,
               "workflow_repository": WorkflowRepository.from_mappings(workflows),
               "journal": Journal(str(tmp_path / "journal.jsonl")), "sleep": lambda _: None,
               "test_mode": True}


def plan():
    return {"target": "pve2", "actions": [{"type": "wait", "duration_ms": 1}]}


def call(name, args, context):
    return json.loads(dispatch_tool(name, args, context=context))


def test_dispatch_sequence_plan_has_stable_envelope(tmp_path):
    c, ctx = client_and_context(tmp_path)
    out = call("kvm_sequence_plan", {"plan": plan()}, ctx)
    assert out["operation"] == "kvm_sequence_plan"
    assert {"target", "transport", "read_only", "ok", "state", "evidence", "error"} <= out.keys()
    assert out["ok"] is True
    assert out["evidence"]["action_count"] == 1


def test_dispatch_sequence_authorize_and_execute_preserves_structured_errors(tmp_path):
    _, ctx = client_and_context(tmp_path, write_enabled=True)
    out = call("kvm_sequence_authorize", {"plan": plan(), "approved": False}, ctx)
    assert out["ok"] is False and out["error"] is not None
    out = call("kvm_sequence_execute", {"plan": plan(), "approved": True}, ctx)
    assert out["ok"] is True


def test_dispatch_rejects_invalid_encoded_plan_without_raise(tmp_path):
    _, ctx = client_and_context(tmp_path)
    out = call("kvm_sequence_plan", {"plan_b64": "%%%"}, ctx)
    assert out["ok"] is False and out["error"] is not None


def test_dispatch_rejects_invalid_action_data_without_raise(tmp_path):
    _, ctx = client_and_context(tmp_path)
    invalid = {"target": "pve2", "actions": [{"type": "wait", "duration_ms": "1"}]}
    out = call("kvm_sequence_plan", {"plan": invalid}, ctx)
    assert out["ok"] is False
    assert "duration_ms must be finite numeric" in out["error"]["code"]


def test_workflow_tools_list_and_inspect_redact_secret(tmp_path):
    workflow = {"name": "safe", "target": "pve2", "steps": [{"type": "text", "value": "token=secret"}]}
    _, ctx = client_and_context(tmp_path, workflows=[workflow])
    listed = call("kvm_workflow_list", {}, ctx)
    assert listed["ok"] and listed["evidence"]["workflows"][0]["actions"][0]["value"] == "[REDACTED]"
    rev = listed["evidence"]["workflows"][0]["revision"]
    inspected = call("kvm_workflow_inspect", {"name": "safe", "revision": rev, "target": "pve2"}, ctx)
    assert inspected["ok"] and "secret" not in json.dumps(inspected)


def test_workflow_execute_matches_inline_plan(tmp_path):
    workflow = {"name": "safe", "target": "pve2", "steps": [{"type": "wait", "duration_ms": 1}]}
    _, ctx = client_and_context(tmp_path, write_enabled=True, workflows=[workflow])
    listed = call("kvm_workflow_list", {}, ctx)
    rev = listed["evidence"]["workflows"][0]["revision"]
    out = call("kvm_workflow_execute", {"name": "safe", "revision": rev, "approved": True}, ctx)
    assert out["ok"] is True
    inline = call("kvm_sequence_execute", {"plan": plan(), "approved": True}, ctx)
    assert inline["evidence"]["plan_hash"] == out["evidence"]["plan_hash"]


@pytest.mark.parametrize("name,args", [
    ("kvm_sequence_plan", {"plan": plan(), "extra": True}),
    ("kvm_sequence_authorize", {"plan": plan(), "extra": True}),
    ("kvm_sequence_execute", {"plan": plan(), "extra": True}),
    ("kvm_workflow_list", {"extra": True}),
    ("kvm_workflow_inspect", {"name": "safe", "extra": True}),
    ("kvm_workflow_execute", {"name": "safe", "revision": "x", "extra": True}),
])
def test_new_dispatchers_reject_unknown_top_level_fields(tmp_path, name, args):
    _, ctx = client_and_context(tmp_path)
    out = call(name, args, ctx)
    assert out["ok"] is False
    assert "unsupported argument" in out["error"]["code"]


@pytest.mark.parametrize("args", [
    {"plan": plan(), "approved": "false"},
    {"plan": plan(), "approved": 1},
    {"plan": plan(), "ttl_s": "30"},
    {"plan": plan(), "ttl_s": float("inf")},
])
def test_sequence_dispatch_rejects_coerced_or_nonfinite_types(tmp_path, args):
    _, ctx = client_and_context(tmp_path, write_enabled=True)
    out = call("kvm_sequence_execute", args, ctx)
    assert out["ok"] is False
    assert "invalid argument" in out["error"]["code"]


def test_dispatch_propagates_target_mismatch_and_revision_mismatch(tmp_path):
    workflow = {"name": "safe", "target": "pve2", "steps": [{"type": "wait", "duration_ms": 1}]}
    _, ctx = client_and_context(tmp_path, write_enabled=True, workflows=[workflow])
    mismatch = call("kvm_sequence_execute", {"plan": {**plan(), "target": "pve1"}, "approved": True}, ctx)
    assert "target mismatch" in mismatch["error"]["code"]
    bad_revision = call("kvm_workflow_execute", {"name": "safe", "revision": "sha256:bad", "approved": True, "target": "pve2"}, ctx)
    assert "workflow revision mismatch" in bad_revision["error"]["code"]
