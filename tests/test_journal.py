import json
import os
import stat
import subprocess
import sys
import threading
import time

from kvmctl.journal import Journal


def test_journal_appends_jsonl_and_excludes_secrets(tmp_path):
    path = tmp_path / "checkpoints.jsonl"
    journal = Journal(path)

    journal.append({
        "operation": "host.reboot",
        "target": "edge-01",
        "token": "do-not-write",
        "nested": {"password": "also-do-not-write", "state": "ready"},
    })

    lines = path.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["target"] == "edge-01"
    assert "token" not in record
    assert "password" not in record["nested"]
    assert "do-not-write" not in lines[0]


def test_journal_serializes_safe_json_values_and_rejects_unbounded_records(tmp_path):
    path = tmp_path / "checkpoints.jsonl"
    journal = Journal(path, max_record_bytes=256)

    journal.append({"when": object(), "values": {1, 2}})
    record = json.loads(path.read_text())
    assert isinstance(record["when"], str)
    assert sorted(record["values"]) == [1, 2]

    try:
        journal.append({"output": "x" * 1000})
    except ValueError as exc:
        assert "bound" in str(exc)
    else:
        raise AssertionError("oversized journal record was accepted")
    assert len(path.read_text().splitlines()) == 1


def test_journal_concurrent_appends_remain_complete_lines(tmp_path):
    path = tmp_path / "checkpoints.jsonl"
    journal = Journal(path)

    threads = [threading.Thread(target=lambda i=i: journal.append({"i": i})) for i in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    lines = path.read_text().splitlines()
    assert len(lines) == 20
    assert {json.loads(line)["i"] for line in lines} == set(range(20))


def test_journal_failed_short_write_rolls_back_partial_record(tmp_path, monkeypatch):
    path = tmp_path / "checkpoints.jsonl"
    journal = Journal(path)
    journal.append({"existing": True})
    before = path.read_bytes()
    real_write = os.write
    calls = 0

    def short_then_fail(fd, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(fd, data[:3])
        raise OSError("injected write failure")

    monkeypatch.setattr(os, "write", short_then_fail)
    try:
        journal.append({"new": True})
    except OSError as exc:
        assert "injected" in str(exc)
    else:
        raise AssertionError("failed journal append was accepted")
    assert path.read_bytes() == before


def test_journal_fsync_failure_fsyncs_rollback(tmp_path, monkeypatch):
    path = tmp_path / "checkpoints.jsonl"
    journal = Journal(path)
    journal.append({"existing": True})
    before = path.read_bytes()
    calls = 0

    def fail_append_fsync(fd):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected fsync failure")

    monkeypatch.setattr(os, "fsync", fail_append_fsync)
    try:
        journal.append({"new": True})
    except OSError as exc:
        assert "injected fsync failure" in str(exc)
    else:
        raise AssertionError("fsync failure was swallowed")
    assert calls == 2
    assert path.read_bytes() == before


def test_journal_waits_for_interprocess_lock(tmp_path):
    path = tmp_path / "checkpoints.jsonl"
    lock_path = path.with_name(path.name + ".lock")
    marker = tmp_path / "writer-started"
    holder = subprocess.Popen(
        [sys.executable, "-c", (
            "import fcntl, os, sys; "
            "fd=os.open(sys.argv[1], os.O_RDWR|os.O_CREAT, 0o600); "
            "fcntl.flock(fd, fcntl.LOCK_EX); print('locked', flush=True); "
            "sys.stdin.read(); fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)"
        ), str(lock_path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    writer = None
    try:
        assert holder.stdout.readline().strip() == "locked"
        writer = subprocess.Popen(
            [sys.executable, "-c", (
                "import pathlib, sys; "
                "pathlib.Path(sys.argv[2]).touch(); "
                "from kvmctl.journal import Journal; Journal(sys.argv[1]).append({'ok': True})"
            ), str(path), str(marker)],
        )
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()
        assert writer.poll() is None
        holder.stdin.close()
        assert writer.wait(timeout=5) == 0
    finally:
        if writer is not None and writer.poll() is None:
            writer.kill()
            writer.wait()
        if holder.poll() is None:
            holder.kill()
            holder.wait()
    assert path.read_text().splitlines() == ['{"ok":true}']


def test_journal_uses_secure_interprocess_lock_file(tmp_path):
    path = tmp_path / "checkpoints.jsonl"
    Journal(path).append({"ok": True})
    lock_path = path.with_name(path.name + ".lock")
    info = lock_path.stat()
    assert stat.S_ISREG(info.st_mode)
    assert info.st_uid == os.getuid()
    assert stat.S_IMODE(info.st_mode) == 0o600
    journal_info = path.stat()
    assert stat.S_ISREG(journal_info.st_mode)
    assert journal_info.st_uid == os.getuid()
    assert stat.S_IMODE(journal_info.st_mode) == 0o600
