"""Shared test fixtures: a mock KVMD transport built on httpx MockTransport."""
import json

import pytest


class FakeKvmd:
    """Records requests and returns scripted responses keyed by (method, path)."""

    def __init__(self):
        self.requests = []  # list of dicts: method, path, headers, content, params
        self.routes = {}    # (method, path) -> callable(request_dict) -> (status, body)
        self.token = None   # when set, require this token header

    def add(self, method, path, fn=None, status=200, body=None, content=None):
        self.routes[(method.upper(), path)] = fn or (lambda req: (status, content if content is not None else body if body is not None else {"ok": True}))

    def handle(self, request: "httpx.Request"):
        req = {
            "method": request.method,
            "path": request.url.path,
            "headers": {k.lower(): v for k, v in request.headers.items()},
            "params": dict(request.url.params),
            "content": request.content,
        }
        self.requests.append(req)
        token = self.token
        if token is not None and req["headers"].get("token") != token:
            return httpx.Response(403, json={"ok": False, "error": "unauthorized"})
        key = (req["method"], req["path"])
        if key in self.routes:
            status, body = self.routes[key](req)
            if isinstance(body, (bytes, bytearray)):
                return httpx.Response(status, content=bytes(body), headers={"content-type": "image/jpeg"})
            return httpx.Response(status, json=body)
        return httpx.Response(404, json={"ok": False, "error": "not found"})


try:
    import httpx  # noqa
except ImportError:  # pragma: no cover
    raise


@pytest.fixture
def fake():
    return FakeKvmd()


@pytest.fixture
def transport(fake):
    import httpx
    return httpx.MockTransport(fake.handle)


def make_client(client_cls, base_url="https://kvm.test", **kwargs):
    """Helper to construct a client against the mock transport."""
    import httpx
    from kvmctl.client import KvmClient

    client = client_cls(base_url=base_url, verify=False, **kwargs)
    return client
