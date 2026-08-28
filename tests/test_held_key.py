"""RED: held-key sequences (TH41-3 verified recipe) + OTG re-arm."""
import httpx
import pytest

from kvmctl.client import KvmClient
from kvmctl.switch import TH41_3, SwitchProfile, execute_switch, plan_switch


def build(fake):
    c = KvmClient("https://kvm.test", verify=False)
    c.set_token("t")
    c._transport = httpx.MockTransport(fake.handle)
    return c


def events_sent(fake):
    return [
        (r["params"].get("key"), r["params"].get("state"))
        for r in fake.requests
        if r["path"] == "/api/hid/events/send_key"
    ]


HOLD_MS = 0.12
GAP_S = 0.15


def test_profile_defaults_to_tap_mode():
    # Existing behavior preserved: no hold_ms means plain taps.
    assert TH41_3.hold_ms is None


HELD = SwitchProfile(
    name="th41-3-held", min_port=1, max_port=4,
    sequence=(("ControlRight", "tap"), ("ControlRight", "tap"),
              ("Digit{port}", "tap"), ("Enter", "tap")),
    hold_ms=HOLD_MS,
)


def test_held_key_profile_plan_is_still_down_up_pairs():
    plan = [(e.key, e.state) for e in plan_switch(HELD, 2)]
    assert plan == [
        ("ControlRight", "down"), ("ControlRight", "up"),
        ("ControlRight", "down"), ("ControlRight", "up"),
        ("Digit2", "down"), ("Digit2", "up"),
        ("Enter", "down"), ("Enter", "up"),
    ]


def test_held_key_execution_times_hold_and_gap(fake):
    from kvmctl.switch import plan_switch
    p = SwitchProfile(
        name="th41-3-held", min_port=1, max_port=4,
        sequence=(("ControlRight", "tap"), ("Digit{port}", "tap")),
        hold_ms=HOLD_MS, inter_key_delay=GAP_S,
    )
    fake.add("POST", "/api/hid/events/send_key", lambda r: (200, {"ok": True}))
    sleeps = []
    execute_switch(build(fake), p, 4, sleep=sleeps.append, settle_delay=0.5)
    # per key: down, hold 120ms, up, gap 150ms -> [0.12, 0.15] * 2 keys, then settle
    assert sleeps == [HOLD_MS, GAP_S, HOLD_MS, GAP_S, 0.5]
    assert events_sent(fake) == [
        ("ControlRight", "true"), ("ControlRight", "false"),
        ("Digit4", "true"), ("Digit4", "false"),
    ]


def test_held_key_invariant_no_two_keys_held(fake):
    """Even with holds, every key is released before the next is pressed."""
    p = SwitchProfile(
        name="th41-3-held", min_port=1, max_port=4,
        sequence=(("ControlRight", "tap"), ("ControlRight", "tap"),
                  ("Digit{port}", "tap"), ("Enter", "tap")),
        hold_ms=HOLD_MS,
    )
    fake.add("POST", "/api/hid/events/send_key", lambda r: (200, {"ok": True}))
    res = execute_switch(build(fake), p, 2, sleep=lambda s: None)
    held = set()
    for ev in res.events:
        if ev.state == "down":
            assert not held
            held.add(ev.key)
        else:
            held.discard(ev.key)
    assert not held


def test_dry_run_with_hold_sends_nothing(fake):
    p = SwitchProfile(
        name="th41-3-held", min_port=1, max_port=4,
        sequence=(("Enter", "tap"),), hold_ms=HOLD_MS,
    )
    res = execute_switch(build(fake), p, 1, dry_run=True, sleep=lambda s: None)
    assert events_sent(fake) == []
    assert len(res.events) == 2


def test_rearm_otg_bounces_gadget(fake):
    import time as _t
    from kvmctl.switch import rearm_otg
    fake.add("GET", "/api/system/otg_functions", lambda r: (200, {"ok": True})) \
        if False else None
    def handler(req):
        return (200, {"ok": True})
    fake.routes[("POST", "/api/system/otg_functions")] = handler
    sleeps = []
    rearm_otg(build(fake), sleep=sleeps.append)
    calls = [dict(r["params"]) for r in fake.requests
             if r["path"] == "/api/system/otg_functions"]
    assert calls == [
        {"start_cdrom": "true", "start_flash": "true"},
        {"start_cdrom": "false", "start_flash": "false"},
    ]
    assert sleeps == [8.0, 12.0]


def test_rearm_otg_dry_run_sends_nothing(fake):
    from kvmctl.switch import rearm_otg
    rearm_otg(build(fake), sleep=lambda s: None, dry_run=True)
    assert not [r for r in fake.requests
                if r["path"] == "/api/system/otg_functions"]
