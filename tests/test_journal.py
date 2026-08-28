import json
import threading

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
