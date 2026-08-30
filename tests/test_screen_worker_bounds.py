import threading

import pytest

from kvmctl.journal import Journal
from kvmctl.machines import RACK, SessionState
from kvmctl.sequence_executor import SequenceExecutor


def ready_session():
    session = SessionState()
    session.mark_selected(RACK["pve2"])
    session.mark_verified("pve2")
    return session


class SlowClient:
    base_url = "https://screen.test"

    def snapshot_jpeg(self):
        threading.Event().wait(1)
        return b"frame"

    def ocr(self, frame):
        return ""

    def release_all(self):
        return True


class ExpiringScreenClient:
    base_url = "https://screen-expiry.test"

    def __init__(self, now):
        self.now = now
        self.snapshots = 0
        self.ocr_calls = 0

    def snapshot_jpeg(self):
        self.snapshots += 1
        self.now[0] = 2.0
        return b"frame"

    def ocr(self, frame):
        self.ocr_calls += 1
        return "x"

    def release_all(self):
        return True


def test_timed_out_screen_worker_is_poisoned_and_not_replaced(tmp_path):
    session = SessionState()
    session.mark_selected(RACK["pve2"])
    session.mark_verified("pve2")
    ex = SequenceExecutor(SlowClient(), session, Journal(tmp_path / "j"),
                          clock=lambda: 0.0)
    ex._active_deadline = 0.01
    with pytest.raises(RuntimeError, match="screen assertion unavailable"):
        ex._dispatch_assert_screen(type("A", (), {"contains": "x"})())
    worker = ex._screen_executor
    assert ex._screen_poisoned is True
    with pytest.raises(RuntimeError, match="screen assertion unavailable"):
        ex._dispatch_assert_screen(type("A", (), {"contains": "x"})())
    assert ex._screen_executor is worker


def test_screen_snapshot_is_not_started_after_authorization_expiry(tmp_path):
    now = [1.0]
    client = ExpiringScreenClient(now)
    ex = SequenceExecutor(client, ready_session(), Journal(tmp_path / "j"),
                          clock=lambda: now[0])
    ex._active_expires_at = 1.0
    ex._active_deadline = 10.0

    with pytest.raises(RuntimeError, match="authorization expired"):
        ex._dispatch_assert_screen(type("A", (), {"contains": "x"})())
    assert client.snapshots == 0
    assert client.ocr_calls == 0


def test_screen_ocr_is_not_submitted_after_authorization_expiry(tmp_path):
    now = [1.0]
    client = ExpiringScreenClient(now)
    ex = SequenceExecutor(client, ready_session(), Journal(tmp_path / "j"),
                          clock=lambda: now[0])
    ex._active_expires_at = 1.5
    ex._active_deadline = 10.0

    with pytest.raises(RuntimeError, match="authorization expired"):
        ex._dispatch_assert_screen(type("A", (), {"contains": "x"})())
    assert client.snapshots == 1
    assert client.ocr_calls == 0


def test_blocked_screen_worker_is_shared_across_repeated_executors(tmp_path):
    workers = []
    for index in range(3):
        ex = SequenceExecutor(SlowClient(), ready_session(), Journal(tmp_path / f"j{index}"),
                              clock=lambda: 0.0)
        ex._active_deadline = 0.01
        with pytest.raises(RuntimeError, match="screen assertion unavailable"):
            ex._dispatch_assert_screen(type("A", (), {"contains": "x"})())
        workers.append(ex._screen_executor)
    assert workers[0] is workers[1] is workers[2]


def test_sequence_execution_cleans_up_screen_executor(tmp_path):
    class Client(ExpiringScreenClient):
        def snapshot_jpeg(self):
            self.snapshots += 1
            return b"frame"

    client = Client([0.0])
    ex = SequenceExecutor(client, ready_session(), Journal(tmp_path / "j"),
                          clock=lambda: 0.0)
    planned = ex.plan({"target": "pve2", "actions": [{"type": "assert_screen", "contains": "x"}]})
    auth = ex.authorize(planned, approved=True)
    result = ex.execute(auth.token)
    assert result.ok
    assert ex._screen_executor is None
