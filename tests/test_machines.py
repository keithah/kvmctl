"""Tests for machine profiles, selection state, and verification policies."""
import re

import httpx
import pytest

from kvmctl.client import KvmClient
from kvmctl.machines import (
    DEFAULT_VERIFY_POLICY,
    RACK,
    SelectOptions,
    SelectionState,
    SessionState,
    SwitchFailure,
    VerifyPolicy,
    frames_differ,
    MachineProfile,
    run_verify_policy,
    select_machine,
    verify_frame_change,
)
import kvmctl.machines as machines_mod


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


class FakeKvmd:
    """Records HID/OTG/snapshot traffic; scripted OCR and snapshots."""

    def __init__(self):
        self.requests = []
        self.snapshots = [b"frame-a", b"frame-b"]  # popped per snapshot call
        self.ocr_text_by_frame = {b"frame-a": "pve2 login:", b"frame-b": "pve2 login:"}
        self.fail_otg = False
        self._snap_i = 0
        self.last_snapshot = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append({
            "method": request.method,
            "path": request.url.path,
            "params": dict(request.url.params),
        })
        if request.url.path == "/api/system/otg_functions" and self.fail_otg:
            return httpx.Response(500, json={"ok": False})
        if request.url.path == "/api/streamer/snapshot":
            frame = self.snapshots[min(self._snap_i, len(self.snapshots) - 1)]
            self._snap_i += 1
            self.last_snapshot = frame
            return httpx.Response(200, content=frame, headers={"content-type": "image/jpeg"})
        return httpx.Response(200, json={"ok": True})

    def keys_sent(self):
        return [
            r["params"].get("key")
            for r in self.requests
            if r["path"] == "/api/hid/events/send_key"
            for _ in [r["params"].get("state")]
        ]

    def key_events(self):
        return [
            (r["params"].get("key"), r["params"].get("state"))
            for r in self.requests
            if r["path"] == "/api/hid/events/send_key"
        ]

    def otg_calls(self):
        return [
            r["params"]
            for r in self.requests
            if r["path"] == "/api/system/otg_functions"
        ]


@pytest.fixture
def fake():
    return FakeKvmd()


def make_client(fake):
    c = KvmClient("https://kvm.test", verify=False)
    c.set_token("t")
    c._transport = httpx.MockTransport(fake.handle)
    # Route client.ocr through the fake's scripted per-frame text instead of
    # real/local OCR.
    c.ocr = lambda image_bytes, _f=fake: _f.ocr_text_by_frame.get(_f.last_snapshot, "")
    return c


def fast_options(**kw):
    opts = SelectOptions(
        rearm=False,
        hold_ms=0,
        gap_ms=0,
        settle_s=0.0,
        verify_attempts=1,
        verify_delay=0.0,
    )
    for k, v in kw.items():
        setattr(opts, k, v)
    return opts


# --------------------------------------------------------------------------
# Rack configuration
# --------------------------------------------------------------------------


def test_rack_mapping_matches_probe_notes():
    expected = {"pve1": 1, "pve2": 2, "kodi-build": 3, "pve3": 4}
    assert {m.name: m.port for m in RACK.values()} == expected


def test_pve1_is_enabled_after_live_verification():
    assert RACK["pve1"].enabled is True


def test_unknown_machine_refused_without_hid_traffic():
    fake = FakeKvmd()
    session = SessionState()
    with pytest.raises(SwitchFailure, match="unknown machine"):
        select_machine(make_client(fake), session, "nope", options=fast_options())
    assert fake.keys_sent() == []
    assert session.current is None


# --------------------------------------------------------------------------
# Held-key sequence (per PROBE_NOTES recipe, via key_down/key_up)
# --------------------------------------------------------------------------


def test_held_key_event_shape_and_order():
    fake = FakeKvmd()
    client = make_client(fake)
    select_machine(client, SessionState(), "pve2", options=fast_options())
    events = fake.key_events()
    # strictly alternating down/up, no simultaneous holds, correct order
    assert [(k, s) for k, s in events] == [
        ("ControlRight", "down"), ("ControlRight", "up"),
        ("ControlRight", "down"), ("ControlRight", "up"),
        ("Digit2", "down"), ("Digit2", "up"),
        ("Enter", "down"), ("Enter", "up"),
    ]


def test_rearm_bounce_uses_otg_functions_with_expected_params():
    fake = FakeKvmd()
    fake.snapshots = [b"f1", b"f1", b"f2"]
    fake.ocr_text_by_frame = {b"f1": "pve3 login:", b"f2": "pve3 login:"}
    client = make_client(fake)
    rec = select_machine(client, SessionState(), "pve3",
                         options=fast_options(rearm=True, verify_policy=VerifyPolicy.PROMPT_PATTERN))
    calls = fake.otg_calls()
    assert len(calls) == 2
    assert calls[0] == {"start_cdrom": "true", "start_flash": "true"}
    assert calls[1] == {"start_cdrom": "false", "start_flash": "false"}
    assert rec.verified


def test_otg_failure_before_keys_is_safe():
    fake = FakeKvmd()
    fake.fail_otg = True
    session = SessionState()
    with pytest.raises(SwitchFailure, match="OTG bounce failed"):
        select_machine(make_client(fake), session, "pve2",
                       options=fast_options(rearm=True))
    assert fake.keys_sent() == []      # no keystrokes leaked to the console
    assert session.current is None     # prior state untouched


# --------------------------------------------------------------------------
# Selection state semantics
# --------------------------------------------------------------------------


def test_selected_unverified_distinct_from_verified():
    s = SessionState()
    m = RACK["pve2"]
    rec = s.mark_selected(m)
    assert rec.state is SelectionState.SELECTED_UNVERIFIED
    assert rec.selected and not rec.verified
    v = s.mark_verified("prompt seen")
    assert v.state is SelectionState.VERIFIED
    assert v.verified


def test_verify_requires_prior_selection():
    s = SessionState()
    with pytest.raises(RuntimeError):
        s.mark_verified()


def test_failed_verification_recorded_as_verify_failed():
    # pve2 OCR never shows the prompt -> verify fails, record kept.
    fake = FakeKvmd()
    fake.snapshots = [b"blank"]
    fake.ocr_text_by_frame = {b"blank": ""}
    session = SessionState()
    with pytest.raises(SwitchFailure, match="NOT verified"):
        select_machine(make_client(fake), session, "pve2",
                       options=fast_options(verify_policy=VerifyPolicy.PROMPT_PATTERN))
    rec = session.current
    assert rec is not None
    assert rec.state is SelectionState.VERIFY_FAILED
    assert rec.port == 2
    assert not rec.verified


# --------------------------------------------------------------------------
# Verification policies
# --------------------------------------------------------------------------


def test_frames_differ_policy():
    fake = FakeKvmd()
    fake.snapshots = [b"a", b"a", b"a", b"changed"]
    client = make_client(fake)
    assert verify_frame_change(client, b"a", attempts=5, delay=0) is True
    fake2 = FakeKvmd()
    fake2.snapshots = [b"a"]
    assert verify_frame_change(make_client(fake2), b"a", attempts=3, delay=0) is False


def test_ocr_identity_policy():
    m = RACK["kodi-build"]
    fake = FakeKvmd()
    fake.snapshots = [b"x"]
    fake.ocr_text_by_frame = {b"x": "Welcome to macOS — Kodi"}
    ok, _ = run_verify_policy(VerifyPolicy.OCR_IDENTITY, make_client(fake), m, None, delay=0)
    assert ok
    fake2 = FakeKvmd()
    fake2.snapshots = [b"x"]
    fake2.ocr_text_by_frame = {b"x": "pve2 login:"}
    ok2, detail = run_verify_policy(VerifyPolicy.OCR_IDENTITY, make_client(fake2), m, None, delay=0)
    assert not ok2 and "mismatch" in detail


def test_prompt_pattern_policy_regexes():
    m = RACK["pve2"]
    text = "Proxmox VE  pve2 login:"
    assert m.prompt_matches(text)
    assert not RACK["pve3"].prompt_matches(text)


def test_snapshot_errors_during_verify_do_not_crash(monkeypatch):
    # streamer may 503 right after OTG bounce; policy should keep retrying
    class Flaky:
        def __init__(self):
            self.n = 0

        def snapshot_jpeg(self, *a, **kw):
            self.n += 1
            if self.n < 3:
                raise KvmClient.ApiError(503, "snapshot")
            return b"f"

        def ocr(self, image_bytes):
            return "pve3 login:"

    ok, _ = run_verify_policy(
        VerifyPolicy.PROMPT_PATTERN, Flaky(), RACK["pve3"], None,
        attempts=5, delay=0,
    )
    assert ok


def test_default_policies_cover_all_machines():
    assert set(DEFAULT_VERIFY_POLICY) == set(RACK)
    assert DEFAULT_VERIFY_POLICY["kodi-build"] is VerifyPolicy.FRAME_CHANGE


def test_machine_profile_supports_switches_larger_than_verified_four_port_rack():
    profile = MachineProfile(port=8, name="lab-node-8", port_limit=8)
    assert profile.port == 8


def test_select_does_not_mutate_options_default_policy():
    fake = FakeKvmd()
    client = make_client(fake)
    opts = fast_options()
    assert opts.verify_policy is None
    select_machine(client, SessionState(), "pve2", options=opts)
    assert opts.verify_policy is None


def test_frame_comparison_ignores_jpeg_encoding_noise():
    from io import BytesIO
    from PIL import Image
    first = Image.new("RGB", (8, 8), "red")
    second = Image.new("RGB", (8, 8), "red")
    buf1, buf2 = BytesIO(), BytesIO()
    first.save(buf1, format="JPEG", quality=70)
    second.save(buf2, format="JPEG", quality=90)
    assert frames_differ(buf1.getvalue(), buf2.getvalue()) is False
    second.putpixel((0, 0), (0, 0, 255))
    buf3 = BytesIO()
    second.save(buf3, format="JPEG", quality=90)
    assert frames_differ(buf1.getvalue(), buf3.getvalue()) is True


# --------------------------------------------------------------------------
# Safe failure behavior
# --------------------------------------------------------------------------


def test_mid_sequence_hid_error_marks_verify_failed(monkeypatch):
    fake = FakeKvmd()
    client = make_client(fake)
    session = SessionState()
    orig = machines_mod.send_held_key
    calls = []

    def flaky(c, key, hold_ms, gap_ms, sleep):
        calls.append(key)
        if key == "Digit2":
            raise RuntimeError("hid busy")
        return orig(c, key, hold_ms, gap_ms, sleep)

    monkeypatch.setattr(machines_mod, "send_held_key", flaky)
    with pytest.raises(SwitchFailure, match="mid-way"):
        select_machine(client, session, "pve2", options=fast_options())
    rec = session.current
    # Enter was never sent, so the switch did NOT happen; record says failed
    assert "Enter" not in calls
    assert rec is not None
    assert rec.state is SelectionState.VERIFY_FAILED


def test_no_sleep_in_fast_tests_but_holds_present():
    # sanity: default timing matches the verified recipe constants
    assert machines_mod.HOLD_MS == 120 and machines_mod.GAP_MS == 150
