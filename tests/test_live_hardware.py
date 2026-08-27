"""Opt-in, read-only smoke test for a reachable KVMD/GLKVM device."""
import os

import pytest

from kvmctl.client import KvmClient


pytestmark = pytest.mark.live


def test_live_capabilities_and_snapshot_are_available():
    url = os.environ.get("KVMCTL_LIVE_URL")
    token = os.environ.get("KVMCTL_LIVE_TOKEN")
    if not url or not token:
        pytest.skip("set KVMCTL_LIVE_URL and KVMCTL_LIVE_TOKEN to enable")
    verify = not bool(os.environ.get("KVMCTL_LIVE_INSECURE"))
    client = KvmClient(url, verify=verify, timeout=15,
                       host=os.environ.get("KVMCTL_LIVE_HOST"))
    client.set_token(token)
    caps = client.capabilities()
    assert caps["hid"] is True
    assert caps["stream"] is True
    try:
        frame = client.snapshot_jpeg()
    except KvmClient.ApiError as exc:
        # Some Comet firmware returns 503 until its authenticated stream
        # websocket is open; this is a reachable-device result, not auth or
        # transport failure. Full stream recovery remains a separate workflow.
        if exc.status != 503:
            raise
        pytest.skip("streamer reachable but snapshot needs an active websocket")
    assert frame.startswith(b"\xff\xd8")