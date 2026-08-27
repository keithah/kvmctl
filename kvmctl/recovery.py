"""Post-switch streamer recovery (TH41-3 resolved recipe, step 3).

The OTG bounce that re-arms the TH41-3 hotkey engine also kills the
ustreamer: the first snapshot attempt after the bounce returns HTTP 503.
The verified revival recipe is:

    1. expect (tolerate) one 503,
    2. nudge the encoder via /api/streamer/set_params desired_fps=40 quality=80,
    3. retry the snapshot until it succeeds,
    4. open a fresh stream WebSocket at /api/ws?stream=1 (required to keep the
       ustreamer alive; without it snapshots 503 again).

The stream WebSocket opener is injectable so this module can be exercised in
tests without a live device.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from kvmctl.client import KvmClient

DEFAULT_NUDGE = {"desired_fps": 40, "quality": 80}
STREAM_WS_PATH = "/api/ws?stream=1"


def recover_streamer(
    client: KvmClient,
    *,
    open_ws: Optional[Callable[[str], object]] = None,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = 5,
    delay: float = 1.0,
    nudge: Optional[dict] = None,
    dry_run: bool = False,
) -> bool:
    """Revive the streamer after an OTG bounce.

    Tolerates exactly the expected first-snapshot 503, applies the encoder
    nudge, retries until a snapshot succeeds, then opens a fresh stream
    WebSocket via ``open_ws(STREAM_WS_PATH)`` when provided.

    Returns True on success, False if no snapshot succeeded within ``attempts``.
    With ``dry_run=True`` nothing is sent and True is returned.
    """
    if dry_run:
        return True

    def snapshot_ok() -> bool:
        try:
            client.snapshot_jpeg()
            return True
        except client.ApiError as exc:
            if exc.status == 503:
                return False  # expected right after the bounce
            raise

    # Probe once immediately after the bounce. A 503 is expected and tolerated;
    # a successful probe is also fine, and any other API error propagates.
    snapshot_ok()

    params = dict(DEFAULT_NUDGE if nudge is None else nudge)
    client._request("POST", "/api/streamer/set_params", params=params)

    for i in range(attempts):
        if i:
            sleep(delay)
        if snapshot_ok():
            break
    else:
        return False

    if open_ws is not None:
        open_ws(STREAM_WS_PATH)
    return True
