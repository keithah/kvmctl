"""Tests for the sequential switch protocol (TH41-3)."""
import httpx
import pytest

from kvmctl.client import KvmClient
from kvmctl.switch import (
    PROFILES,
    SwitchProtocolError,
    TH41_3,
    execute_switch,
    plan_switch,
)


class Fake:
    def __init__(self):
        self.requests = []
        self.token = "t"

    def add(self, method, path, handler):
        self._handler = handler
        self._route = (method, path)

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = dict(request.url.params)
        self.requests.append({"path": request.url.path, "params": body})
        status, payload = self._handler(request)
        return httpx.Response(status, json=payload)


@pytest.fixture
def fake():
    return Fake()


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


def test_plan_th41_3_event_order():
    plan = plan_switch(TH41_3, 2)
    assert [(e.key, e.state) for e in plan] == [
        ("ControlRight", "down"), ("ControlRight", "up"),
        ("ControlRight", "down"), ("ControlRight", "up"),
        ("Digit2", "down"), ("Digit2", "up"),
        ("Enter", "down"), ("Enter", "up"),
    ]


def test_events_are_discrete_not_simultaneous_chord():
    # The defining property: no two keys are ever held at once.
    plan = plan_switch(TH41_3, 4)
    held = set()
    for ev in plan:
        if ev.state == "down":
            assert not held, f"{ev.key} pressed while {held} still held"
            held.add(ev.key)
        else:
            held.discard(ev.key)
    assert not held


def test_port_validation():
    with pytest.raises(SwitchProtocolError):
        plan_switch(TH41_3, 0)
    with pytest.raises(SwitchProtocolError):
        plan_switch(TH41_3, 5)


def test_dry_run_sends_nothing(fake):
    fake.add("POST", "/api/hid/events/send_key", lambda r: (200, {"ok": True}))
    res = execute_switch(build(fake), TH41_3, 1, dry_run=True, sleep=lambda s: None)
    assert events_sent(fake) == []
    assert len(res.events) == 8


def test_execute_emits_sequential_discrete_events(fake):
    fake.add("POST", "/api/hid/events/send_key", lambda r: (200, {"ok": True}))
    sleeps = []
    execute_switch(
        build(fake), TH41_3, 3,
        dry_run=False, sleep=sleeps.append,
        inter_key_delay=0.15, settle_delay=2.0,
    )
    assert events_sent(fake) == [(k, s) for k, s in [
        ("ControlRight", "down"), ("ControlRight", "up"),
        ("ControlRight", "down"), ("ControlRight", "up"),
        ("Digit3", "down"), ("Digit3", "up"),
        ("Enter", "down"), ("Enter", "up"),
    ]]
    # 7 inter-key gaps + 1 settle = 8 sleeps; gaps use the configured delay.
    assert sleeps[:-1] == [0.15] * 7 and sleeps[-1] == 2.0


def test_profiles_registry_contains_th41_3():
    assert "terived-th41-3" in PROFILES
