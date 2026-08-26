"""RED: HID key down/up, discrete press, text typing, HID reset."""
import httpx

from kvmctl.client import KvmClient


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


def test_key_down_up_sends_state_params(fake):
    fake.token = "t"
    fake.add("POST", "/api/hid/events/send_key", lambda r: (200, {"ok": True}))
    c = build(fake)
    c.key_down("ControlRight")
    c.key_up("ControlRight")
    assert events_sent(fake) == [("ControlRight", "down"), ("ControlRight", "up")]


def test_press_key_is_discrete_down_then_up(fake):
    fake.token = "t"
    fake.add("POST", "/api/hid/events/send_key", lambda r: (200, {"ok": True}))
    build(fake).press_key("Enter")
    assert events_sent(fake) == [("Enter", "down"), ("Enter", "up")]


def test_type_text_plain_and_shifted(fake):
    fake.token = "t"
    fake.add("POST", "/api/hid/events/send_key", lambda r: (200, {"ok": True}))
    build(fake).type_text("aB2")
    assert events_sent(fake) == [
        ("KeyA", "down"), ("KeyA", "up"),
        ("ShiftLeft", "down"), ("KeyB", "down"), ("KeyB", "up"), ("ShiftLeft", "up"),
        ("Digit2", "down"), ("Digit2", "up"),
    ]


def test_type_text_releases_shift_on_error(fake):
    # An unmapped character mid-string must not leave ShiftLeft stuck down.
    fake.token = "t"
    fake.add("POST", "/api/hid/events/send_key", lambda r: (200, {"ok": True}))
    c = build(fake)
    try:
        c.type_text("\x01")
    except ValueError:
        pass
    assert all(k != "ShiftLeft" for k, _ in events_sent(fake))


def test_hid_reset_hits_endpoint(fake):
    fake.token = "t"
    hit = []
    fake.add("POST", "/api/hid/reset", lambda r: (hit.append(r["path"]), (200, {"ok": True}))[1])
    build(fake).hid_reset()
    assert hit == ["/api/hid/reset"]


def test_char_to_key_browser_names():
    from kvmctl.keys import char_to_key
    assert char_to_key("2") == ("Digit2", False)
    assert char_to_key("A") == ("KeyA", True)
    assert char_to_key("@") == ("Digit2", True)
