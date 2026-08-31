import hashlib
import hmac
import json
import time

from kvmctl.journal import _add_note
from kvmctl.machines import RACK, SessionState
from kvmctl.session_store import load_session, save_session
from kvmctl.workflows import WorkflowRepository


def test_journal_note_guard_accepts_legacy_exception_object():
    class LegacyError(Exception):
        pass
    exc = LegacyError("failure")
    exc.add_note = None
    _add_note(exc, "diagnostic")


def test_atomic_persistence_does_not_use_private_tempfile_api(tmp_path, monkeypatch):
    import kvmctl.session_store as store
    monkeypatch.setattr(store, "tempfile", None, raising=False)
    store._atomic_write(tmp_path / "state", "value")
    assert (tmp_path / "state").read_text() == "value"


def test_future_session_timestamp_is_not_trusted(tmp_path):
    session = SessionState()
    session.mark_selected(RACK["pve2"])
    session.mark_verified("verified")
    path = tmp_path / "session.json"
    save_session(session, str(path), endpoint="https://kvm.test")
    envelope = json.loads(path.read_text())
    envelope["payload"]["at"] = time.time() + 7200
    key = (tmp_path / "session.json.key").read_bytes()
    raw = json.dumps(envelope["payload"], sort_keys=True, separators=(",", ":")).encode()
    envelope["mac"] = hmac.new(key, raw, hashlib.sha256).hexdigest()
    path.write_text(json.dumps(envelope))
    assert load_session(str(path), endpoint="https://kvm.test").current is None


def test_workflow_inspect_redacts_assert_screen_text():
    repo = WorkflowRepository.from_mappings([{
        "name": "screen", "target": "pve2",
        "steps": [{"type": "assert_screen", "contains": "password=secret-value"}],
    }])
    inspected = repo.list()[0].to_mapping()
    assert inspected["actions"][0]["contains"] == "[REDACTED]"
