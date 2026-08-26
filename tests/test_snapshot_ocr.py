"""RED: snapshot retrieval and OCR response handling."""
import httpx
import pytest

from kvmctl.client import KvmClient


def build(fake):
    c = KvmClient("https://kvm.test", verify=False)
    c.set_token("t")
    c._transport = httpx.MockTransport(fake.handle)
    return c


JPEG = b"\xff\xd8\xff\xe0fakejpeg"


def test_snapshot_jpeg_hits_streamer_endpoint_with_params_and_returns_bytes(fake):
    fake.token = "t"
    seen = {}

    def snap(req):
        seen["params"] = req["params"]
        return httpx.Response(200, content=JPEG)

    # snapshot returns raw bytes, not JSON
    def handle(request):
        if request.url.path == "/api/streamer/snapshot":
            seen["params"] = dict(request.url.params)
            return httpx.Response(200, content=JPEG)
        raise AssertionError("unexpected path " + request.url.path)

    fake.routes[("GET", "/api/streamer/snapshot")] = None
    c = build(fake)
    c._transport = httpx.MockTransport(handle)
    data = c.snapshot_jpeg()
    assert data == JPEG
    assert seen["params"]["preview"] == "true"
    assert seen["params"]["preview_max_width"] == "1280"
    assert seen["params"]["preview_quality"] == "70"


def test_snapshot_503_when_stream_down_raises_api_error(fake):
    fake.token = "t"

    def handle(request):
        return httpx.Response(503, json={"ok": False, "error": "streamer not active"})

    c = build(fake)
    c._transport = httpx.MockTransport(handle)
    with pytest.raises(KvmClient.ApiError) as exc:
        c.snapshot_jpeg()
    assert exc.value.status == 503


def test_ocr_remote_when_capability_present(fake):
    fake.token = "t"
    INFO = {
        "ok": True,
        "result": {
            "hid": {"enabled": True},
            "extras": {"ocr": {"enabled": True, "languages": {"eng-US": {}}}},
        },
    }
    captured = {}
    fake.add("GET", "/api/info", lambda r: (200, INFO))
    fake.add("POST", "/api/ocr", lambda r: (
        captured.update(content=r["content"]),
        (200, {"ok": True, "result": {"text": "pve2 login:"}}),
    )[1])
    text = build(fake).ocr(JPEG)
    assert text == "pve2 login:"
    assert JPEG in captured["content"]


def test_ocr_falls_back_to_local_engine_when_disabled(fake, monkeypatch):
    fake.token = "t"
    INFO = {
        "ok": True,
        "result": {"hid": {"enabled": True}, "extras": {"ocr": {"enabled": False, "languages": {}}}},
    }
    fake.add("GET", "/api/info", lambda r: (200, INFO))
    calls = []
    monkeypatch.setattr(KvmClient, "_local_ocr", staticmethod(lambda b: calls.append(b) or "local text"))
    text = build(fake).ocr(JPEG)
    assert text == "local text"
    assert calls == [JPEG]


def test_local_ocr_without_engine_raises_helpful_error():
    import kvmctl.client as mod
    real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def fake_import(name, *a, **k):
        if name in ("pytesseract", "PIL"):
            raise ImportError(name)
        return real_import(name, *a, **k)

    import builtins
    monkey = builtins.__import__
    builtins.__import__ = fake_import
    try:
        with pytest.raises(RuntimeError, match="OCR unavailable"):
            KvmClient._local_ocr(JPEG)
    finally:
        builtins.__import__ = monkey
