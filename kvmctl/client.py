"""KVMD-compatible client core.

Capability-driven client for PiKVM / GLKVM style APIs. Verified against a live
GLKVM (KVMD 4.82): URL-encoded form login, lowercase ``token`` header auth,
``/api/info`` capability discovery, HID events, and streamer snapshots.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

REDACTED = "***"


class KvmClient:
    """HTTP client abstraction over KVMD-compatible APIs."""

    class AuthError(RuntimeError):
        pass

    class ApiError(RuntimeError):
        def __init__(self, status: int, message: str):
            super().__init__(f"HTTP {status}: {message}")
            self.status = status

    def __init__(
        self,
        base_url: str,
        *,
        verify: bool | str = True,
        timeout: float = 10.0,
    ):
        # TLS verification is explicit: True, False, or a CA bundle path.
        self.verify = verify
        self.base_url = base_url.rstrip("/")
        self.token: Optional[str] = None
        self._credentials: Optional[tuple] = None
        self._transport: Optional[httpx.BaseTransport] = None  # test injection point
        self._timeout = timeout

    # -- plumbing ---------------------------------------------------------

    def _http(self) -> httpx.Client:
        headers = {}
        if self.token:
            headers["token"] = self.token
        self._last_headers = dict(headers)
        return httpx.Client(
            base_url=self.base_url,
            verify=self.verify,
            timeout=self._timeout,
            headers=headers,
            transport=self._transport,
        )

    @staticmethod
    def redact(secret: str) -> str:
        return REDACTED

    def __repr__(self) -> str:
        return f"KvmClient(base_url={self.base_url!r}, token={REDACTED}, verify={self.verify!r})"

    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        with self._http() as http:
            resp = http.request(method, path, **kwargs)
        if resp.status_code in (401, 403):
            raise self.AuthError(f"{method} {path} -> HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise self.ApiError(resp.status_code, f"{method} {path}")
        data = resp.json()
        if isinstance(data, dict) and data.get("ok") is False:
            raise self.ApiError(resp.status_code, str(data.get("error", "unknown error")))
        return data

    # -- auth -------------------------------------------------------------

    def login(self, user: str, password: str) -> str:
        resp = self._request(
            "POST",
            "/api/auth/login",
            content="user=%s&passwd=%s" % (
                httpx.QueryParams({"user": user}).get("user"),
                httpx.QueryParams({"passwd": password}).get("passwd"),
            ),
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        token = (resp.get("result") or {}).get("token")
        if not token:
            raise self.AuthError("login response missing token")
        self.token = token
        self._credentials = (user, password)
        return token

    def set_token(self, token: str) -> None:
        self.token = token
        self._credentials = None

    def require_capabilities(self, *needed: str) -> dict[str, bool]:
        caps = self.capabilities()
        missing = [c for c in needed if not caps.get(c)]
        if missing:
            raise self.ApiError(0, f"capability unavailable: {', '.join(missing)}")
        return caps

    def check_auth(self) -> dict:
        return self._request("GET", "/api/auth/check")

    def refresh(self) -> str:
        """Re-authenticate. Uses stored credentials; otherwise no-op."""
        if not self._credentials:
            return self.token or ""
        user, password = self._credentials
        return self.login(user, password)

    # -- info & capabilities ----------------------------------------------

    def get_info(self) -> dict:
        data = self._request("GET", "/api/info")
        return data.get("result", data)

    def capabilities(self) -> dict[str, bool]:
        info = self.get_info()
        extras = info.get("extras") or {}
        caps = {
            "hid": bool(info.get("hid", {}).get("enabled")),
            "stream": bool(info.get("streamer", {})),
            "ocr": False,
            "switch": False,
        }
        if "ocr" in extras:
            ocr = extras["ocr"] or {}
            langs = [l for l in (ocr.get("languages") or {}) if l != "--"]
            caps["ocr"] = bool(ocr.get("enabled")) and bool(langs)
        if "switch" in extras and (extras["switch"] or {}).get("enabled"):
            caps["switch"] = True
        return caps

    # -- HID ---------------------------------------------------------------

    def key_down(self, key: str) -> None:
        self._send_key_event(key, "down")

    def key_up(self, key: str) -> None:
        self._send_key_event(key, "up")

    def press_key(self, key: str) -> None:
        self.key_down(key)
        self.key_up(key)

    def _send_key_event(self, key: str, state: str) -> None:
        self._request(
            "POST",
            "/api/hid/events/send_key",
            params={"key": key, "state": state},
        )

    def type_text(self, text: str) -> None:
        from .keys import char_to_key  # lazy import keeps module surface small

        for ch in text:
            name, shift = char_to_key(ch)
            if shift:
                self.key_down("ShiftLeft")
            try:
                self.press_key(name)
            finally:
                if shift:
                    self.key_up("ShiftLeft")

    def hid_reset(self) -> None:
        self._request("POST", "/api/hid/reset")

    # -- stream / snapshot / OCR -------------------------------------------

    def snapshot_jpeg(self, preview_max_width: int = 1280, preview_quality: int = 70) -> bytes:
        with self._http() as http:
            resp = http.get(
                "/api/streamer/snapshot",
                params={
                    "preview": "true",
                    "preview_max_width": preview_max_width,
                    "preview_quality": preview_quality,
                },
            )
        if resp.status_code >= 400:
            raise self.ApiError(resp.status_code, "snapshot")
        return resp.content

    def ocr_available(self) -> bool:
        return self.capabilities()["ocr"]

    def ocr(self, image_bytes: bytes) -> str:
        """OCR response handling where available.

        KVMD's OCR is frequently disabled without language packs (verified on
        GLKVM), so callers may pass raw JPEG/PNG bytes to a local OCR engine.
        """
        if self.ocr_available():
            self.require_capabilities("ocr")
            result = self._request(
                "POST", "/api/ocr", files={"file": ("frame.jpg", image_bytes, "image/jpeg")}
            )
            return (result.get("result") or {}).get("text", "")
        return self._local_ocr(image_bytes)

    @staticmethod
    def _local_ocr(image_bytes: bytes) -> str:
        try:
            import pytesseract
            from PIL import Image
            import io
        except ImportError as exc:
            raise RuntimeError(
                "OCR unavailable on device and no local OCR engine installed"
            ) from exc
        return pytesseract.image_to_string(Image.open(io.BytesIO(image_bytes)))
