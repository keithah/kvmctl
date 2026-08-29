import threading

import pytest

from kvmctl.journal import Journal
from kvmctl.machines import RACK, SessionState
from kvmctl.sequence_executor import SequenceExecutor


class SlowClient:
    base_url = "https://screen.test"

    def snapshot_jpeg(self):
        threading.Event().wait(1)
        return b"frame"

    def ocr(self, frame):
        return ""

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
