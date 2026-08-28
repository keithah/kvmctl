import json

import pytest

from kvmctl.host import HostAdapter, reboot_confirmation
from kvmctl.journal import Journal
from kvmctl.policy import PolicyError


class RebootRunner:
    def __init__(self, identities, reboot_result=(0, "")):
        self.identities = list(identities)
        self.reboot_result = reboot_result
        self.calls = []

    def __call__(self, argv):
        argv = tuple(argv)
        self.calls.append(argv)
        if argv == ("hostname",):
            if not self.identities:
                raise OSError("host disappeared")
            return self.identities.pop(0)
        if argv == ("cat", "/etc/os-release"):
            return 'NAME=Ubuntu\nVERSION_ID="24.04"\nPRETTY_NAME="Ubuntu 24.04 LTS"\n'
        if argv == ("systemctl", "reboot"):
            return self.reboot_result
        raise AssertionError(argv)


def test_reboot_requires_write_and_confirmation_bound_to_target():
    runner = RebootRunner(["edge-01\n"])
    adapter = HostAdapter(runner)
    confirmation = reboot_confirmation("edge-01")
    with pytest.raises(PolicyError, match="write"):
        adapter.reboot("edge-01", confirmation, write_enabled=False)
    with pytest.raises(PolicyError, match="confirmation"):
        adapter.reboot("edge-01", "wrong", write_enabled=True)
    assert ("systemctl", "reboot") not in runner.calls


def test_reboot_lifecycle_polls_disappearance_readiness_and_verifies_identity():
    runner = RebootRunner(["edge-01\n", OSError, "edge-01\n"])
    # OSError is interpreted by the runner below as temporary disappearance.
    original = runner.__call__
    def call(argv):
        if tuple(argv) == ("hostname",) and runner.identities and runner.identities[0] is OSError:
            runner.identities.pop(0)
            raise OSError("gone")
        return original(argv)
    adapter = HostAdapter(call)
    result = adapter.reboot("edge-01", reboot_confirmation("edge-01"),
                            write_enabled=True, attempts=3, sleep=lambda _: None)
    assert result["ok"] is True
    assert result["operation"] == "host.reboot"
    assert result["evidence"]["preflight"]["hostname"] == "edge-01"
    assert result["evidence"]["post_return"]["hostname"] == "edge-01"
    assert ("systemctl", "reboot") in runner.calls


def test_reboot_returns_stable_timeout_code_when_host_never_returns():
    runner = RebootRunner(["edge-01\n"])
    adapter = HostAdapter(runner)
    result = adapter.reboot("edge-01", reboot_confirmation("edge-01"),
                            write_enabled=True, attempts=2, sleep=lambda _: None)
    assert result["ok"] is False
    assert result["error"] == {"code": "host_reboot_timeout", "retryable": True, "requires_human": False}


def test_reboot_journals_lifecycle_transitions(tmp_path):
    runner = RebootRunner(["edge-01\n", OSError, "edge-01\n"])
    original = runner.__call__

    def call(argv):
        if tuple(argv) == ("hostname",) and runner.identities and runner.identities[0] is OSError:
            runner.identities.pop(0)
            raise OSError("gone")
        return original(argv)

    adapter = HostAdapter(call, journal=Journal(tmp_path / "reboot.jsonl"))
    result = adapter.reboot("edge-01", reboot_confirmation("edge-01"),
                            write_enabled=True, attempts=3, sleep=lambda _: None)

    assert result["ok"] is True
    transitions = [json.loads(line)["transition"] for line in
                   (tmp_path / "reboot.jsonl").read_text().splitlines()]
    assert transitions == ["preflight", "reboot_requested", "disappeared", "ready"]


def test_reboot_result_survives_journal_write_failure():
    class BrokenJournal:
        def append(self, record):
            raise OSError("journal unavailable")

    runner = RebootRunner(["edge-01\n"])
    adapter = HostAdapter(runner, journal=BrokenJournal())
    result = adapter.reboot("edge-01", reboot_confirmation("edge-01"),
                            write_enabled=True, attempts=1, sleep=lambda _: None)
    assert result["ok"] is False
    assert result["error"]["code"] == "host_reboot_timeout"


def test_reboot_returns_stable_mismatch_code_and_never_uses_exec_command():
    runner = RebootRunner(["edge-01\n", OSError, "other-host\n"])
    original = runner.__call__
    def call(argv):
        if tuple(argv) == ("hostname",) and runner.identities and runner.identities[0] is OSError:
            runner.identities.pop(0)
            raise OSError("gone")
        return original(argv)
    adapter = HostAdapter(call)
    result = adapter.reboot("edge-01", reboot_confirmation("edge-01"),
                            write_enabled=True, attempts=3, sleep=lambda _: None)
    assert result["ok"] is False
    assert result["error"]["code"] == "host_identity_mismatch"
    assert all(call[0] != "exec_command" for call in runner.calls)
