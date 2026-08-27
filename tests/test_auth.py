"""RED: authentication, session handling, TLS config, credential redaction."""
import json

import httpx
import pytest

from kvmctl.client import KvmClient


def build(fake):
    client = KvmClient("https://kvm.test", verify=False)
    client._transport = httpx.MockTransport(fake.handle)
    return client


def test_login_posts_form_and_stores_token(fake):
    fake.token = None
    captured = {}

    def login(req):
        body = req["content"].decode()
        assert "user=admin" in body
        assert "passwd=secret" in body
        captured["ct"] = req["headers"].get("content-type", "")
        return 200, {"ok": True, "result": {"token": "tok123"}}

    fake.add("POST", "/api/auth/login", login)
    c = build(fake)
    c.login("admin", "secret")
    assert c.token == "tok123"
    assert "application/x-www-form-urlencoded" in captured["ct"]


@pytest.mark.parametrize("password", ["pa&ss", "p+w", "50%off", "hash#tag"])
def test_login_form_encodes_special_password_characters(fake, password):
    from urllib.parse import quote_plus
    fake.add("POST", "/api/auth/login",
             lambda req: (200, {"ok": True, "result": {"token": "tok"}}))
    c = build(fake)
    c.login("admin", password)
    assert fake.requests[-1]["content"].decode() == (
        "user=admin&passwd=" + quote_plus(password)
    )


def test_authenticated_requests_use_token_header(fake):
    fake.token = "tok123"
    fake.add("GET", "/api/auth/check", lambda r: (200, {"ok": True, "result": {"active": True}}))
    c = build(fake)
    c.set_token("tok123")
    result = c.check_auth()
    assert result["result"]["active"] is True
    assert fake.requests[-1]["headers"]["token"] == "tok123"


def test_missing_or_wrong_token_raises_auth_error(fake):
    fake.token = "expected"
    fake.add("GET", "/api/info", lambda r: (200, {"ok": True}))
    c = build(fake)
    with pytest.raises(KvmClient.AuthError):
        c.get_info()


def test_refresh_relogin_with_stored_credentials(fake):
    # First login issues token A; refresh must re-login and pick up token B.
    tokens = iter(["tokA", "tokB"])
    fake.token = None

    def login(req):
        return 200, {"ok": True, "result": {"token": next(tokens)}}

    def check(req):
        if req["headers"].get("token") != current[0]:
            return 403, {"ok": False}
        return 200, {"ok": True}

    current = ["tokA"]
    fake.add("POST", "/api/auth/login", login)
    fake.add("GET", "/api/auth/check", check)
    c = build(fake)
    c.login("admin", "pw")
    assert c.token == "tokA"
    current[0] = "tokB"
    c.refresh()
    assert c.token == "tokB"
    assert c.check_auth() == {"ok": True}
    assert fake.requests[-1]["headers"]["token"] == "tokB"


def test_tls_verification_can_be_enabled(fake):
    c = KvmClient("https://kvm.test", verify=True)
    assert c.verify is True
    c2 = KvmClient("https://kvm.test", verify="/path/to/ca.pem")
    assert c2.verify == "/path/to/ca.pem"


def test_repr_redacts_credentials():
    c = KvmClient("https://kvm.test", verify=False)
    c.set_token("super-secret-token")
    text = repr(c)
    assert "super-secret-token" not in text
    assert c.redact("my-password") == "***"
