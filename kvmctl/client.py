"""KVMD-compatible client core.

Capability-driven client for PiKVM / GLKVM style APIs. Verified against a live
GLKVM (KVMD 4.82): URL-encoded form login, lowercase ``token`` header auth,
``/api/info`` capability discovery, HID events, and streamer snapshots.
"""
from __future__ import annotations

import asyncio
import ssl
import threading
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
        host: Optional[str] = None,
    ):
        # TLS verification is explicit: True, False, or a CA bundle path.
        self.verify = verify
        self.base_url = base_url.rstrip("/")
        self.token: Optional[str] = None
        self._credentials: Optional[tuple] = None
        self._transport: Optional[httpx.BaseTransport] = None  # test injection point
        self._timeout = timeout
        self.host = host
        self._stream = None
        self._held_keys: set[str] = set()

    # -- plumbing ---------------------------------------------------------

    def _http(self) -> httpx.Client:
        headers = {}
        if self.token:
            headers["token"] = self.token
        if self.host:
            headers["host"] = self.host
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
            data={"user": user, "passwd": password},
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
        system = info.get("system") or {}
        system_streamer = system.get("streamer") or {}
        hid_info = info.get("hid") or {}
        caps = {
            "hid": bool(hid_info.get("enabled")),
            "stream": bool(info.get("streamer", {})) or bool(system_streamer),
            "ocr": False,
            "switch": False,
        }
        # Older/embedded KVMD builds omit HID from /api/info. Probe the
        # canonical read-only endpoint when the summary field is absent.
        if "hid" not in info:
            try:
                hid = self._request("GET", "/api/hid").get("result", {})
                caps["hid"] = bool(hid.get("enabled")) and bool(hid.get("connected", True))
            except self.ApiError:
                pass
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
        try:
            self._send_key_event(key, "up")
        finally:
            self._held_keys.discard(key)

    def press_key(self, key: str) -> None:
        self.key_down(key)
        self.key_up(key)

    def send_keys(self, keys: str | list[str]) -> None:
        """Send a KVMD shortcut using canonical browser-style key names."""
        if isinstance(keys, list):
            keys = ",".join(keys)
        if not keys or any(not part.strip() for part in keys.split(",")):
            raise ValueError("shortcut must contain one or more key names")
        self._request("POST", "/api/hid/events/send_shortcut",
                      params={"keys": keys})

    def release_all(self) -> list[str]:
        """Release keys held through this client and return their names."""
        released = []
        for key in list(self._held_keys):
            self.key_up(key)
            released.append(key)
        return released

    def mouse_move(self, x: int, y: int) -> None:
        """Move to normalized absolute coordinates in the KVMD int16 range."""
        if not (-32768 <= x <= 32767 and -32768 <= y <= 32767):
            raise ValueError("mouse coordinates must be in -32768..32767")
        self._request("POST", "/api/hid/events/send_mouse_move",
                      params={"to_x": x, "to_y": y})

    def mouse_move_pct(self, x_pct: float, y_pct: float) -> tuple[int, int]:
        """Move to a screen percentage and return normalized coordinates."""
        if not (0 <= x_pct <= 100 and 0 <= y_pct <= 100):
            raise ValueError("mouse percentages must be in 0..100")
        x = round(x_pct / 100 * 65535 - 32768)
        y = round(y_pct / 100 * 65535 - 32768)
        self.mouse_move(x, y)
        return x, y

    def mouse_button(self, button: str, state: bool) -> None:
        if button not in {"left", "middle", "right", "up", "down"}:
            raise ValueError(f"unsupported mouse button: {button}")
        self._request("POST", "/api/hid/events/send_mouse_button",
                      params={"button": button, "state": "true" if state else "false"})

    def mouse_scroll(self, dx: int = 0, dy: int = 0) -> None:
        if not (-127 <= dx <= 127 and -127 <= dy <= 127):
            raise ValueError("mouse wheel deltas must be in -127..127")
        self._request("POST", "/api/hid/events/send_mouse_wheel",
                      params={"delta_x": dx, "delta_y": dy})

    def _send_key_event(self, key: str, state: str) -> None:
        self._request(
            "POST",
            "/api/hid/events/send_key",
            params={"key": key, "state": "true" if state == "down" else "false"},
        )
        if state == "down":
            self._held_keys.add(key)

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

    # -- stream lifecycle -------------------------------------------------

    def open_stream(self):
        """Open and retain the authenticated stream WebSocket.

        The socket must remain open while snapshots are used.  This is a
        concrete implementation of the injected opener used by recovery.py.
        """
        try:
            from websockets.asyncio.client import connect
        except ImportError as exc:
            raise RuntimeError("stream support requires the 'websockets' package") from exc
        self.close_stream()
        scheme = "wss" if self.base_url.startswith("https://") else "ws"
        url = self.base_url.split("://", 1)[1] + "/api/ws?stream=1"
        ws_url = f"{scheme}://{url}"
        kwargs: dict[str, Any] = {"additional_headers": {}}
        if self.host:
            kwargs["additional_headers"]["Origin"] = f"https://{self.host}"
        if self.token:
            kwargs["additional_headers"]["token"] = self.token
        if scheme == "wss" and self.verify is False:
            tls = ssl.create_default_context()
            tls.check_hostname = False
            tls.verify_mode = ssl.CERT_NONE
            kwargs["ssl"] = tls
        elif scheme == "wss" and isinstance(self.verify, str):
            kwargs["ssl"] = ssl.create_default_context(cafile=self.verify)
        ready = threading.Event()
        state: dict[str, Any] = {}

        def run() -> None:
            async def serve() -> None:
                try:
                    state["ws"] = await connect(ws_url, **kwargs)
                except BaseException as exc:  # propagate handshake failures
                    state["error"] = exc
                finally:
                    ready.set()
                if "ws" in state:
                    await state["ws"].wait_closed()

            loop = asyncio.new_event_loop()
            state["loop"] = loop
            asyncio.set_event_loop(loop)
            loop.run_until_complete(serve())
            loop.close()

        thread = threading.Thread(target=run, name="kvmctl-stream", daemon=True)
        thread.start()
        if not ready.wait(self._timeout):
            raise TimeoutError("stream WebSocket handshake timed out")
        if "error" in state:
            raise state["error"]
        self._stream = (state, thread)
        return self._stream

    def close_stream(self) -> None:
        """Close the retained stream, if any."""
        if self._stream is not None:
            state, thread = self._stream
            ws = state.get("ws")
            loop = state.get("loop")
            if ws is not None and loop is not None and not loop.is_closed():
                future = asyncio.run_coroutine_threadsafe(ws.close(), loop)
                future.result(timeout=self._timeout)
            thread.join(timeout=self._timeout)
            self._stream = None
