import os
import subprocess
import sys
import time

from kvmctl.machines import device_lock


def test_device_lock_serializes_across_processes(tmp_path, monkeypatch):
    monkeypatch.setenv("KVMCTL_LOCK_DIR", str(tmp_path / "locks"))
    lock = device_lock("endpoint/device")
    assert lock.acquire(blocking=False)
    try:
        code = (
            "from kvmctl.machines import device_lock; "
            "print(device_lock('endpoint/device').acquire(blocking=False))"
        )
        env = dict(os.environ)
        env["KVMCTL_LOCK_DIR"] = str(tmp_path / "locks")
        out = subprocess.check_output([sys.executable, "-c", code], env=env, text=True)
        assert out.strip() == "False"
    finally:
        lock.release()


def test_device_lock_fail_closed_when_lock_directory_is_unusable(tmp_path, monkeypatch):
    bad = tmp_path / "not-a-directory"
    bad.write_text("x")
    monkeypatch.setenv("KVMCTL_LOCK_DIR", str(bad))
    # Construction itself must fail closed rather than silently using a
    # process-local fallback.
    try:
        lock = device_lock("unusable")
    except OSError:
        return
    assert lock.acquire(blocking=False) is False
