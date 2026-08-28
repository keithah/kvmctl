import json

from kvmctl.results import operation_result


def test_operation_result_builds_stable_success_shape():
    result = operation_result(
        operation="snapshot",
        target="pve2",
        transport="kvm",
        read_only=True,
        changed=False,
        state="observed",
        evidence={"bytes": 12},
    )

    assert result == {
        "operation": "snapshot",
        "target": "pve2",
        "transport": "kvm",
        "read_only": True,
        "ok": True,
        "changed": False,
        "state": "observed",
        "evidence": {"bytes": 12},
        "warnings": [],
        "error": None,
        "next_actions": [],
    }
    json.dumps(result)


def test_operation_result_builds_structured_failure_shape():
    result = operation_result(
        operation="reboot",
        target="pve2",
        transport="ssh",
        read_only=False,
        changed=False,
        state="blocked",
        ok=False,
        error={"code": "write_disabled", "retryable": False, "requires_human": False},
        warnings=["authorization required"],
        next_actions=["enable the write policy"],
    )

    assert result["ok"] is False
    assert result["error"] == {
        "code": "write_disabled",
        "retryable": False,
        "requires_human": False,
    }
    assert result["warnings"] == ["authorization required"]
    assert result["next_actions"] == ["enable the write policy"]
    json.dumps(result)


def test_operation_result_wraps_legacy_evidence_without_losing_fields():
    legacy = {
        "operation": "verify",
        "transport": "kvm",
        "read_only": True,
        "ok": True,
        "evidence": {"verified": True, "machine": "pve2"},
    }

    result = operation_result.from_legacy(legacy, target="pve2", state="verified")

    assert result["operation"] == "verify"
    assert result["target"] == "pve2"
    assert result["transport"] == "kvm"
    assert result["read_only"] is True
    assert result["ok"] is True
    assert result["changed"] is False
    assert result["state"] == "verified"
    assert result["evidence"] == legacy["evidence"]
