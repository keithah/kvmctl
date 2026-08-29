import json
from pathlib import Path

from kvmctl.journal import Journal
from kvmctl.machines import RACK, SessionState
from kvmctl.sequence_executor import SequenceExecutor

from test_sequence_executor import FakeClient, ready_session


def test_authorization_is_opaque_single_use_and_bound(tmp_path):
    ex = SequenceExecutor(FakeClient(), ready_session(), Journal(tmp_path / "j.jsonl"),
                          clock=lambda: 0.0, sleep=lambda _: None)
    planned = ex.plan({"target": "pve2", "actions": [{"type": "release_all"}]})
    auth = ex.authorize(planned, approved=True)
    assert auth.token and auth.token not in repr(auth.plan)
    result = ex.execute(auth.token)
    assert result.ok
    reused = ex.execute(auth.token)
    assert not reused.ok
    assert "used" in reused.error


def test_journal_records_verification_timestamps_and_final_result(tmp_path):
    client = FakeClient()
    journal = Journal(tmp_path / "j.jsonl")
    ex = SequenceExecutor(client, ready_session(), journal, clock=lambda: 0.001, sleep=lambda _: None)
    result = ex.execute(ex.authorize(ex.plan({"target": "pve2", "actions": [{"type": "release_all"}]}), approved=True).token)
    assert result.ok
    records = [json.loads(x) for x in (tmp_path / "j.jsonl").read_text().splitlines()]
    completed = records[-1]
    assert completed["transition"] == "completed"
    assert completed["target_verification"] is True
    assert "started_at" in completed and "ended_at" in completed and "duration_ms" in completed
    assert completed["final_result"] == "success"
