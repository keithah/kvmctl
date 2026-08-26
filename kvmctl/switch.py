"""Generic sequential HID KVM switch protocols.

A sequential protocol switches ports by sending discrete key events in order
(each key pressed and released fully before the next), never a simultaneous
chord — some switches' hotkey engines only recognize that shape.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from kvmctl.client import KvmClient


class SwitchProtocolError(ValueError):
    pass


@dataclass
class KeyEvent:
    """One discrete HID event."""

    key: str
    state: str  # "down" | "up"

    def __repr__(self) -> str:
        return f"{self.key}:{self.state}"


@dataclass
class SwitchProfile:
    """Declarative description of a sequential hotkey protocol.

    ``sequence`` is a tuple of (key, action) steps where action is "tap"
    (down then up) or "press"/"release" for explicit halves.
    """

    name: str
    min_port: int
    max_port: int
    # Steps with "{port}" substituted by str(port).
    sequence: tuple[tuple[str, str], ...]
    inter_key_delay: float = 0.2  # seconds between discrete events
    settle_delay: float = 1.0  # seconds after the final event
    # When set, each tap holds the key down for this many seconds before
    # releasing (verified 0.120 s on TH41-3: taps are filtered by its hotkey
    # detector). None preserves plain down/up taps.
    hold_ms: Optional[float] = None

    def build_events(self, port: int) -> list[KeyEvent]:
        if not (self.min_port <= port <= self.max_port):
            raise SwitchProtocolError(
                f"port {port} out of range {self.min_port}-{self.max_port} "
                f"for profile {self.name}"
            )
        events: list[KeyEvent] = []
        for key_tmpl, action in self.sequence:
            key = key_tmpl.format(port=port)
            if action == "tap":
                events.append(KeyEvent(key, "down"))
                events.append(KeyEvent(key, "up"))
            elif action == "press":
                events.append(KeyEvent(key, "down"))
            elif action == "release":
                events.append(KeyEvent(key, "up"))
            else:
                raise SwitchProtocolError(f"unknown action {action!r}")
        return events


# Terived TH41-3: Right Ctrl tapped twice, port digit, Enter (manual grammar).
TH41_3 = SwitchProfile(
    name="terived-th41-3",
    min_port=1,
    max_port=4,
    sequence=(
        ("ControlRight", "tap"),
        ("ControlRight", "tap"),
        ("Digit{port}", "tap"),
        ("Enter", "tap"),
    ),
)

PROFILES = {p.name: p for p in (TH41_3,)}


@dataclass
class SwitchResult:
    profile: str
    port: int
    dry_run: bool
    events: list[KeyEvent] = field(default_factory=list)
    ok: bool = True


def plan_switch(profile: SwitchProfile, port: int) -> list[KeyEvent]:
    """Validate the port and return the exact event plan without emitting."""
    return profile.build_events(port)


def execute_switch(
    client: KvmClient,
    profile: SwitchProfile,
    port: int,
    *,
    dry_run: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    inter_key_delay: Optional[float] = None,
    settle_delay: Optional[float] = None,
) -> SwitchResult:
    """Emit the profile's discrete key events to select ``port``.

    Events are strictly sequential: every key is released before the next is
    pressed, separated by ``inter_key_delay``. With ``dry_run=True`` nothing is
    sent; only the validated plan is returned.

    If ``profile.hold_ms`` is set (e.g. the TH41-3 resolved recipe, 120 ms),
    each tap holds its key down for that duration before releasing; the
    inter-key gap then separates one full press from the next.
    """
    ikd = profile.inter_key_delay if inter_key_delay is None else inter_key_delay
    sd = profile.settle_delay if settle_delay is None else settle_delay
    events = plan_switch(profile, port)
    result = SwitchResult(profile=profile.name, port=port, dry_run=dry_run, events=events)
    if dry_run:
        return result
    hold = profile.hold_ms
    if hold:
        # Held-key mode: each tap is down -> hold -> up -> inter-key gap.
        # Strictly sequential; never two keys held at once.
        taps = list(zip(events[0::2], events[1::2]))
        for n, (down, up) in enumerate(taps):
            assert down.state == "down" and up.key == down.key
            client.key_down(down.key)
            sleep(hold)
            client.key_up(up.key)
            if n < len(taps):
                sleep(ikd)  # gap after every tap, including the last
    else:
        # Legacy tap mode: gap before every event after the first.
        for i, ev in enumerate(events):
            if i:
                sleep(ikd)
            if ev.state == "down":
                client.key_down(ev.key)
            else:
                client.key_up(ev.key)
    sleep(sd)
    return result


def describe_plan(events: Sequence[KeyEvent]) -> str:
    return " ".join(repr(e) for e in events)


# -- OTG re-arm (TH41-3 resolved recipe step 1) ------------------------------
#
# The TH41-3 hotkey engine arms on USB (re)enumeration. Bouncing the Comet
# storage gadget (start_cdrom/start_flash true, wait ~8 s, then false, wait
# ~12 s) produces that attach event and must precede the held-key sequence.

OTG_ON_WAIT_S = 8.0
OTG_OFF_WAIT_S = 12.0


def rearm_otg(
    client: KvmClient,
    *,
    dry_run: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    on_wait: float = OTG_ON_WAIT_S,
    off_wait: float = OTG_OFF_WAIT_S,
) -> bool:
    """Toggle /api/system/otg_functions true then false to arm the hotkey engine.

    Returns True when the bounce was performed, False for a dry run.
    """
    if dry_run:
        return False
    client._request(
        "POST",
        "/api/system/otg_functions",
        params={"start_cdrom": "true", "start_flash": "true"},
    )
    sleep(on_wait)
    client._request(
        "POST",
        "/api/system/otg_functions",
        params={"start_cdrom": "false", "start_flash": "false"},
    )
    sleep(off_wait)
    return True
