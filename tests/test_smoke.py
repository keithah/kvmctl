"""Smoke test: import surface and redaction guarantees."""
from kvmctl import client
from kvmctl.client import KvmClient
from kvmctl.keys import char_to_key


def test_public_surface():
    for name in ("login", "set_token", "refresh", "check_auth", "get_info",
                 "capabilities", "require_capabilities", "key_down", "key_up",
                 "press_key", "type_text", "hid_reset", "snapshot_jpeg",
                 "ocr", "ocr_available", "redact"):
        assert callable(getattr(KvmClient, name)), name


def test_no_credentials_in_module_source():
    # The machine password from the live probe must never appear in source.
    import pathlib
    src = pathlib.Path(client.__file__).read_text()
    assert "[REDACTED-DEVICE-CREDENTIAL]" not in src
