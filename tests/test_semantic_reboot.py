import pytest

from kvmctl.host import reboot_confirmation
from kvmctl.semantics import SemanticSurface


class SemanticRunner:
    def __init__(self):
        self.hostnames = ["edge-01\n", OSError, "edge-01\n"]
        self.calls = []

    def __call__(self, argv):
        argv = tuple(argv)
        self.calls.append(argv)
        if argv == ("hostname",):
            value = self.hostnames.pop(0)
            if value is OSError:
                raise OSError("gone")
            return value
        if argv == ("cat", "/etc/os-release"):
            return 'NAME=Ubuntu\nVERSION_ID="24.04"\nPRETTY_NAME="Ubuntu 24.04 LTS"\n'
        if argv == ("systemctl", "reboot"):
            return (0, "")
        raise AssertionError(argv)


def test_semantic_host_reboot_uses_host_boundary_and_write_gate():
    runner = SemanticRunner()
    surface = SemanticSurface(object(), host_runner=runner)
    with pytest.raises(Exception, match="write"):
        surface.host_reboot("edge-01", reboot_confirmation("edge-01"))
    surface.write_enabled = True
    result = surface.host_reboot("edge-01", reboot_confirmation("edge-01"),
                                 attempts=3, sleep=lambda _: None)
    assert result["ok"] is True
    assert result["operation"] == "host.reboot"
    assert all(call != ("exec_command",) for call in runner.calls)
