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
    out = call("kvm_sequence_authorize", {"plan": plan(), "approved": True}, ctx)
    assert out["ok"] is True
    token = out["evidence"]["approval_token"]
    out = call("kvm_sequence_execute", {"approval_token": token}, ctx)
    assert out["ok"] is True


@pytest.mark.parametrize("name", ["kvm_sequence_authorize", "kvm_sequence_execute"])
def test_dispatch_sequence_accepts_inline_plan_with_control_fields(tmp_path, name):
    _, ctx = client_and_context(tmp_path, write_enabled=True)
    if name == "kvm_sequence_authorize":
        out = call(name, {**plan(), "approved": True, "ttl_s": 30.0}, ctx)
    else:
        auth = call("kvm_sequence_authorize", {**plan(), "approved": True, "ttl_s": 30.0}, ctx)
        out = call(name, {"approval_token": auth["evidence"]["approval_token"]}, ctx)
    assert out["ok"] is True
    assert "approved" not in out["evidence"]
    assert "ttl_s" not in out["evidence"]


def test_dispatch_sequence_inline_authorize_rejects_fractional_ttl(tmp_path):
    _, ctx = client_and_context(tmp_path, write_enabled=True)

    out = call("kvm_sequence_authorize", {**plan(), "approved": True, "ttl_s": 1.5}, ctx)

    assert out["ok"] is False
    assert "invalid argument" in out["error"]["code"]


def test_dispatch_sequence_rejects_unknown_inline_plan_field(tmp_path):
    _, ctx = client_and_context(tmp_path, write_enabled=True)
    out = call("kvm_sequence_execute", {**plan(), "approvald": True}, ctx)
    assert out["ok"] is False
    assert "unsupported argument" in out["error"]["code"]


def test_dispatch_rejects_invalid_encoded_plan_without_raise(tmp_path):
    _, ctx = client_and_context(tmp_path)
    out = call("kvm_sequence_plan", {"plan_b64": "%%%"}, ctx)
    assert out["ok"] is False and out["error"] is not None


def test_dispatch_rejects_invalid_action_data_without_raise(tmp_path):
    _, ctx = client_and_context(tmp_path)
    invalid = {"target": "pve2", "actions": [{"type": "wait", "duration_ms": "1"}]}
    out = call("kvm_sequence_plan", {"plan": invalid}, ctx)
    assert out["ok"] is False
    assert out["error"]["code"] == "invalid input"


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
    auth = call("kvm_workflow_authorize", {"name": "safe", "revision": rev, "approved": True}, ctx)
    out = call("kvm_workflow_execute", {"name": "safe", "revision": rev, "approval_token": auth["evidence"]["approval_token"]}, ctx)
    assert out["ok"] is True
    inline_auth = call("kvm_sequence_authorize", {"plan": plan(), "approved": True}, ctx)
    inline = call("kvm_sequence_execute", {"approval_token": inline_auth["evidence"]["approval_token"]}, ctx)
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


def test_workflow_authorize_requires_actual_boolean_approval(tmp_path):
    workflow = {"name": "safe", "target": "pve2", "steps": [{"type": "wait", "duration_ms": 1}]}
    _, ctx = client_and_context(tmp_path, write_enabled=True, workflows=[workflow])
    listed = call("kvm_workflow_list", {}, ctx)
    revision = listed["evidence"]["workflows"][0]["revision"]

    rejected = call("kvm_workflow_authorize", {
        "name": "safe", "revision": revision, "approved": "false",
        "target": "pve2", "ttl_s": 30,
    }, ctx)

    assert rejected["ok"] is False
    assert "invalid argument" in rejected["error"]["code"]


@pytest.mark.parametrize("arguments,field", [
    ({"name": "", "revision": "r", "approved": True, "target": "pve2", "ttl_s": 30}, "name"),
    ({"name": "safe", "revision": "", "approved": True, "target": "pve2", "ttl_s": 30}, "revision"),
    ({"name": "safe", "revision": "r", "target": "pve2", "ttl_s": 30}, "approved"),
    ({"name": "safe", "revision": "r", "approved": None, "target": "pve2", "ttl_s": 30}, "approved"),
    ({"name": "safe", "revision": "r", "approved": True, "target": 7, "ttl_s": 30}, "target"),
    ({"name": "safe", "revision": "r", "approved": True, "target": "pve2", "ttl_s": "30"}, "ttl_s"),
    ({"name": "safe", "revision": "r", "approved": True, "target": "pve2", "ttl_s": 1.5}, "ttl_s"),
    ({"name": "safe", "revision": "r", "approved": True, "target": "pve2", "ttl_s": float("inf")}, "ttl_s"),
])
def test_workflow_authorize_rejects_malformed_strict_inputs(tmp_path, arguments, field):
    _, ctx = client_and_context(tmp_path, write_enabled=True)
    rejected = call("kvm_workflow_authorize", arguments, ctx)

    assert rejected["ok"] is False
    assert "invalid argument" in rejected["error"]["code"]


def test_workflow_authorize_accepts_valid_explicit_inputs(tmp_path):
    workflow = {"name": "safe", "target": "pve2", "steps": [{"type": "wait", "duration_ms": 1}]}
    _, ctx = client_and_context(tmp_path, write_enabled=True, workflows=[workflow])
    listed = call("kvm_workflow_list", {}, ctx)
    revision = listed["evidence"]["workflows"][0]["revision"]

    authorized = call("kvm_workflow_authorize", {
        "name": "safe", "revision": revision, "approved": True,
        "target": "pve2", "ttl_s": 30,
    }, ctx)

    assert authorized["ok"] is True
    assert authorized["evidence"]["approval_token"]
