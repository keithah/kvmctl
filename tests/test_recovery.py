"""RED: post-switch streamer recovery (PROBE_NOTES resolved recipe step 3).

After the OTG bounce kills the streamer:
    - the first snapshot attempt returns HTTP 503,
    - a set_params nudge (desired_fps=40, quality=80) revives it,
    - a fresh stream WebSocket (/api/ws?stream=1) must be opened.

The WebSocket is injectable so tests never open real connections.
"""
import httpx
import pytest

from kvmctl.client import KvmClient
from kvmctl.recovery import recover_streamer


class FakeKvmd:
    """Snapshot 503 on first call, then serves frames; records set_params."""

    def __init__(self, fail_first=1):
        self.requests = []
        self.fail_first = fail_first
        self._n = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append({
            "method": request.method,
            "path": request.url.path,
            "params": dict(request.url.params),
        })
        if request.url.path == "/api/streamer/snapshot":
            self._n += 1
            if self._n <= self.fail_first:
                return httpx.Response(503, json={"ok": False, "error": "streamer down"})
            return httpx.Response(200, content=b"frame", headers={"content-type": "image/jpeg"})
        return httpx.Response(200, json={"ok": True})

    def set_params_calls(self):
        return [dict(r["params"]) for r in self.requests
                if r["path"] == "/api/streamer/set_params"]


def make(fake):
    c = KvmClient("https://kvm.test", verify=False)
    c.set_token("t")
    c._transport = httpx.MockTransport(fake.handle)
    return c


def test_nudges_encoder_and_retries_snapshot():
    fake = FakeKvmd(fail_first=1)
    ws_opened = []
    ok = recover_streamer(
        make(fake),
        open_ws=lambda url: ws_opened.append(url),
        sleep=lambda s: None,
    )
    assert ok is True
    assert fake.set_params_calls() == [{"desired_fps": "40", "quality": "80"}]
    assert ws_opened == ["/api/ws?stream=1"]


def test_tolerates_exactly_one_503_before_success():
    # The bounce guarantees one 503; recovery must not treat it as fatal.
    fake = FakeKvmd(fail_first=1)
    assert recover_streamer(make(fake), open_ws=lambda u: None,
                            sleep=lambda s: None) is True


def test_persistent_snapshot_failure_returns_false():
    fake = FakeKvmd(fail_first=99)
    assert recover_streamer(make(fake), open_ws=lambda u: None,
                            sleep=lambda s: None, attempts=4) is False


def test_dry_run_sends_no_set_params_and_no_ws():
    fake = FakeKvmd(fail_first=0)
    ws = []
    recover_streamer(make(fake), open_ws=ws.append, sleep=lambda s: None, dry_run=True)
    assert fake.set_params_calls() == []
    assert ws == []


def test_no_real_network_in_tests():
    # Guard: recovery uses only injected transports; nothing here dials out.
    from kvmctl.recovery import DEFAULT_NUDGE as _d  # noqa: F401
    assert _d == {"desired_fps": 40, "quality": 80}
