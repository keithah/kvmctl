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


@pytest.fixture(autouse=True)
def isolated_kvmctl_paths(tmp_path_factory, monkeypatch):
    """Bind every kvmctl persistence path to a private per-test directory.

    Defaults live under the invoking user's cache; leaving them unset would
    make the suite depend on the host's HOME, umask, and leftover state.
    Tests that set these variables themselves still win, because their
    ``monkeypatch.setenv`` runs after this fixture.
    """
    root = tmp_path_factory.mktemp("kvmctl-env")
    monkeypatch.setenv("KVMCTL_JOURNAL_FILE", str(root / "journal.jsonl"))
    monkeypatch.setenv("KVMCTL_AUTH_FILE", str(root / "authorization.json"))
    monkeypatch.setenv("KVMCTL_SESSION_FILE", str(root / "session.json"))
    monkeypatch.setenv("KVMCTL_LOCK_DIR", str(root / "locks"))


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
