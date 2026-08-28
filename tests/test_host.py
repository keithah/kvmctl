import pytest

from kvmctl.host import HostProbeProfile, ProbeError, RunnerResult, run_probe


class ScriptedRunner:
    def __init__(self, outputs):
        self.outputs = outputs
        self.calls = []

    def __call__(self, argv):
        argv = tuple(argv)
        self.calls.append(argv)
        return self.outputs[argv]


def test_identity_probe_returns_sanitized_hostname_and_os():
    runner = ScriptedRunner({
        ("hostname",): "edge-01\n",
        ("cat", "/etc/os-release"): "NAME=Ubuntu\nVERSION_ID=\"24.04\"\nPRETTY_NAME=\"Ubuntu 24.04 LTS\"\n",
    })
    result = run_probe("host.identity.inspect", runner)
    assert result == {"probe": "host.identity.inspect", "hostname": "edge-01", "os": {"name": "Ubuntu", "version_id": "24.04", "pretty_name": "Ubuntu 24.04 LTS"}}
    assert all(isinstance(value, (str, dict, list, type(None), bool, int, float)) for value in result.values())


def test_graphics_probe_parses_pci_drivers_and_drm_nodes():
    runner = ScriptedRunner({
        ("lspci", "-nnk"): "00:02.0 VGA compatible controller [0300]: Intel Corporation UHD [8086:9a49]\n\tSubsystem: Example [1234:5678]\n\tKernel driver in use: i915\n\tKernel modules: i915\n",
        ("find", "/dev/dri", "-maxdepth", "1", "-type", "c", "-printf", "%f\\n"): "card0\nrenderD128\n",
    })
    assert run_probe("host.graphics.inspect", runner) == {"probe": "host.graphics.inspect", "devices": [{"address": "00:02.0", "class": "VGA compatible controller", "description": "Intel Corporation UHD", "driver": "i915"}], "drm_nodes": ["card0", "renderD128"]}


def test_render_access_probe_uses_status_codes_for_quiet_commands():
    runner = ScriptedRunner({
        ("systemctl", "is-active", "--quiet", "kvm-render"): (0, ""),
        ("test", "-r", "/dev/dri/renderD128"): (0, ""),
        ("test", "-w", "/dev/dri/renderD128"): (1, ""),
    })
    assert run_probe("service.render_access.inspect", runner) == {"probe": "service.render_access.inspect", "service": "kvm-render", "active": True, "node": "/dev/dri/renderD128", "readable": True, "writable": False}


def test_render_access_probe_rejects_unexpected_status_codes():
    runner = ScriptedRunner({
        ("systemctl", "is-active", "--quiet", "kvm-render"): (2, ""),
        ("test", "-r", "/dev/dri/renderD128"): (0, ""),
        ("test", "-w", "/dev/dri/renderD128"): (1, ""),
    })
    with pytest.raises(ProbeError, match="malformed"):
        run_probe("service.render_access.inspect", runner)


def test_unknown_probe_and_malformed_output_fail_closed():
    with pytest.raises(ProbeError, match="unknown probe"):
        run_probe("host.reboot", lambda argv: "")
    runner = ScriptedRunner({("hostname",): "bad host name with spaces\n", ("cat", "/etc/os-release"): "NAME=Ubuntu\n"})
    with pytest.raises(ProbeError, match="malformed"):
        run_probe("host.identity.inspect", runner)


def test_probe_rejects_unbounded_output_and_sensitive_values():
    runner = ScriptedRunner({("hostname",): "edge-01\npassword=secret\n", ("cat", "/etc/os-release"): "NAME=Ubuntu\nVERSION_ID=24.04\nPRETTY_NAME=Ubuntu\n"})
    with pytest.raises(ProbeError, match="malformed"):
        run_probe("host.identity.inspect", runner, max_output_bytes=32)


def test_graphics_probe_rejects_malformed_device_and_sensitive_text():
    runner = ScriptedRunner({
        ("lspci", "-nnk"): "not a pci record\n\tKernel driver in use: password=secret\n",
        ("find", "/dev/dri", "-maxdepth", "1", "-type", "c", "-printf", "%f\\n"): "card0\n",
    })
    with pytest.raises(ProbeError, match="malformed"):
        run_probe("host.graphics.inspect", runner)


def test_graphics_probe_rejects_malformed_pci_looking_line():
    runner = ScriptedRunner({
        ("lspci", "-nnk"): "00:02.0 VGA compatible controller [0300]: Intel Corporation UHD [8086]\n",
        ("find", "/dev/dri", "-maxdepth", "1", "-type", "c", "-printf", "%f\\n"): "card0\n",
    })
    with pytest.raises(ProbeError, match="malformed"):
        run_probe("host.graphics.inspect", runner)


def test_graphics_probe_rejects_unrecognized_nonempty_records():
    runner = ScriptedRunner({
        ("lspci", "-nnk"): "00:02.0 VGA compatible controller [0300]: Intel UHD [8086:9a49]\n\tUnexpected record\n",
        ("find", "/dev/dri", "-maxdepth", "1", "-type", "c", "-printf", "%f\\n"): "card0\n",
    })
    with pytest.raises(ProbeError, match="malformed"):
        run_probe("host.graphics.inspect", runner)


def test_graphics_probe_accepts_non_graphics_pci_records():
    runner = ScriptedRunner({
        ("lspci", "-nnk"): (
            "00:01.0 Audio device [0403]: Example Audio [1234:5678]\n"
            "00:02.0 VGA compatible controller [0300]: Intel UHD [8086:9a49]\n"
            "\tKernel driver in use: i915\n"
        ),
        ("find", "/dev/dri", "-maxdepth", "1", "-type", "c", "-printf", "%f\\n"): "card0\n",
    })
    result = run_probe("host.graphics.inspect", runner)
    assert len(result["devices"]) == 1
    assert result["devices"][0]["driver"] == "i915"


def test_render_access_probe_uses_profiled_service_and_node():
    runner = ScriptedRunner({
        ("systemctl", "is-active", "--quiet", "custom-render"): RunnerResult(0, ""),
        ("test", "-r", "/dev/dri/renderD99"): RunnerResult(0, ""),
        ("test", "-w", "/dev/dri/renderD99"): RunnerResult(1, ""),
    })
    profile = HostProbeProfile(service_name="custom-render", drm_node="/dev/dri/renderD99")
    result = run_probe("service.render_access.inspect", runner, profile=profile)
    assert result["service"] == "custom-render"
    assert result["node"] == "/dev/dri/renderD99"


def test_probe_passes_timeout_to_typed_runner():
    calls = []

    def runner(argv, *, timeout):
        calls.append(timeout)
        if tuple(argv) == ("hostname",):
            return RunnerResult(0, "edge-01\n")
        return RunnerResult(0, 'NAME=Ubuntu\nVERSION_ID="24.04"\nPRETTY_NAME="Ubuntu"\n')

    run_probe("host.identity.inspect", runner, profile=HostProbeProfile(timeout_seconds=1.25))
    assert calls == [1.25, 1.25]


def test_probe_times_out_a_stuck_legacy_runner():
    def runner(argv):
        import time
        time.sleep(1)
        return "edge-01\n"

    with pytest.raises(ProbeError, match="timed out"):
        run_probe("host.identity.inspect", runner, profile=HostProbeProfile(timeout_seconds=0.01))
