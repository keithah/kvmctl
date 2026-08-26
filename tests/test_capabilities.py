"""RED: /api/info and capability discovery."""
import httpx
import pytest

from kvmctl.client import KvmClient


def build(fake, token="t"):
    c = KvmClient("https://kvm.test", verify=False)
    c.set_token(token)
    c._transport = httpx.MockTransport(fake.handle)
    return c


GLKVM_INFO = {
    "ok": True,
    "result": {
        "model": "Comet",
        "version": {"kwbd": "1.4.0", "kvmd": "4.82", "ustreamer": "6.13"},
        "platform": {"type": "arm-rv1126"},
        "hid": {"enabled": True, "connected": True, "busy": False},
        "streamer": {"active": False},
        "extras": {
            "ocr": {"enabled": False, "languages": {}},
            "janus": {"enabled": True},
        },
        # PiKVM style: meta/switch absent here -> switch capability off
    },
}

PIKVM_WITH_SWITCH = dict(GLKVM_INFO)
PIKVM_WITH_SWITCH = {
    "ok": True,
    "result": {
        **GLKVM_INFO["result"],
        "extras": {
            "ocr": {"enabled": True, "languages": {"eng-US": {}, "--": {}}},
            "switch": {"enabled": True},
        },
    },
}


def test_get_info_returns_parsed_payload_and_hits_endpoint(fake):
    fake.token = "t"
    fake.add("GET", "/api/info", lambda r: (200, GLKVM_INFO))
    c = build(fake)
    info = c.get_info()
    assert info["model"] == "Comet"
    assert fake.requests[-1]["path"] == "/api/info"


def test_capabilities_glkvm_no_switch_no_ocr(fake):
    fake.token = "t"
    fake.add("GET", "/api/info", lambda r: (200, GLKVM_INFO))
    caps = build(fake).capabilities()
    assert caps == {"hid": True, "stream": True, "ocr": False, "switch": False}


def test_capabilities_ocr_requires_enabled_and_languages(fake):
    fake.token = "t"
    fake.add("GET", "/api/info", lambda r: (200, PIKVM_WITH_SWITCH))
    caps = build(fake).capabilities()
    assert caps["ocr"] is True
    assert caps["switch"] is True


def test_operation_on_missing_capability_raises(fake):
    fake.token = "t"
    fake.add("GET", "/api/info", lambda r: (200, GLKVM_INFO))
    fake.add("POST", "/api/ocr", status=404)
    c = build(fake)
    with pytest.raises(Exception):
        c.ocr(b"\xff\xd8jpegbytes")
