import copy

import pytest

from kvmctl.sequences import plan_hash
from kvmctl.workflows import (
    WorkflowDefinition,
    WorkflowError,
    WorkflowRepository,
    inspect_workflow,
    list_workflows,
    resolve_workflow,
)


def mapping(name="open-terminal-and-identify", target="pve2"):
    result = {
        "name": name,
        "target": target,
        "max_duration_ms": 5000,
        "unexpected_screen_policy": "abort",
        "steps": [
            {"type": "key", "value": "ControlAltT"},
            {"type": "text", "value": "hostname"},
            {"type": "key", "value": "Enter"},
        ],
    }
    return result


def test_compiles_steps_to_canonical_plan_and_resolves_by_revision():
    repo = WorkflowRepository.from_mappings([mapping()])
    definition = repo.list()[0]
    assert definition.name == "open-terminal-and-identify"
    assert definition.plan.target == "pve2"
    assert tuple(a.kind for a in definition.plan.actions) == ("key", "text", "key")
    assert definition.revision.startswith("sha256:")
    resolved = repo.resolve(definition.name, definition.revision, "pve2")
    assert resolved.name == definition.name
    assert resolved.resolved_target == "pve2"


def test_steps_have_inline_plan_parity():
    repo = WorkflowRepository.from_mappings([mapping()])
    definition = repo.list()[0]
    inline = {"target": "pve2", "actions": mapping()["steps"], "max_duration_ms": 5000,
              "unexpected_screen_policy": "abort"}
    assert definition.plan.actions == WorkflowDefinition.from_mapping(mapping()).plan.actions
    assert plan_hash(definition.plan) == plan_hash(inline)


def test_revision_is_deterministic_and_scope_bound():
    one = mapping()
    two = {"steps": list(reversed(list(reversed(one["steps"])))), "unexpected_screen_policy": "abort",
           "max_duration_ms": 5000, "target": "pve2", "name": one["name"]}
    assert WorkflowRepository.from_mappings([one]).list()[0].revision == WorkflowRepository.from_mappings([two]).list()[0].revision
    assert WorkflowRepository.from_mappings([mapping(name="other")]).list()[0].revision != WorkflowRepository.from_mappings([one]).list()[0].revision
    assert WorkflowRepository.from_mappings([mapping(target="pve3")]).list()[0].revision != WorkflowRepository.from_mappings([one]).list()[0].revision


def test_listing_is_name_sorted_and_wrappers_delegate():
    repo = WorkflowRepository.from_mappings([mapping("zeta"), mapping("alpha")])
    assert [d.name for d in repo.list()] == ["alpha", "zeta"]
    assert [d["name"] for d in list_workflows(repo)] == ["alpha", "zeta"]
    d = repo.list()[0]
    assert inspect_workflow(repo, d.name, d.revision, "pve2")["revision"] == d.revision
    assert resolve_workflow(repo, d.name, d.revision, "pve2").resolved_target == "pve2"

@pytest.mark.parametrize("bad", [
    {"name": "", "target": "pve2", "steps": [{"type": "text", "value": "x"}]},
    {"name": "bad name", "target": "pve2", "steps": [{"type": "text", "value": "x"}]},
    {"name": "bad/name", "target": "pve2", "steps": [{"type": "text", "value": "x"}]},
    {"name": 3, "target": "pve2", "steps": [{"type": "text", "value": "x"}]},
    {"name": "x", "target": "pve2", "steps": []},
    {"name": "x", "target": "pve2", "actions": [{"type": "text", "value": "x"}]},
    {"name": "x", "target": "pve2", "steps": [{"type": "bogus", "value": "x"}]},
    {"name": "x", "target": "pve2", "steps": [{"type": "text", "value": "x"}], "extra": "no"},
])
def test_rejects_malformed_definitions(bad):
    with pytest.raises(WorkflowError):
        WorkflowRepository.from_mappings([bad])


def test_rejects_duplicates_and_revision_spoofing():
    with pytest.raises(WorkflowError):
        WorkflowRepository.from_mappings([mapping(), mapping()])
    bad = mapping()
    bad["revision"] = "sha256:" + "0" * 64
    with pytest.raises(WorkflowError):
        WorkflowRepository.from_mappings([bad])


def test_target_resolution_is_strict():
    repo = WorkflowRepository.from_mappings([mapping()])
    d = repo.list()[0]
    for target in (None, "pve3"):
        with pytest.raises(WorkflowError):
            repo.resolve(d.name, d.revision, target)
    with pytest.raises(WorkflowError):
        repo.resolve("missing", d.revision, "pve2")
    with pytest.raises(WorkflowError):
        repo.resolve(d.name, "sha256:" + "0" * 64, "pve2")


def test_inspection_is_redacted_canonical_and_defensive():
    raw = mapping()
    repo = WorkflowRepository.from_mappings([raw])
    d = repo.list()[0]
    inspected = repo.inspect(d.name)
    assert set(inspected) == {"name", "revision", "target", "target_independent", "max_duration_ms", "unexpected_screen_policy", "actions", "steps"}
    assert inspected["actions"] == inspected["steps"]
    inspected["actions"].append({"type": "text", "value": "changed"})
    raw["steps"].append({"type": "text", "value": "changed"})
    assert len(repo.list()[0].plan.actions) == 3
    assert all(secret not in repr(inspected) for secret in ("token", "password", "secret", "authorization", "cookie"))


def test_definitions_are_frozen_and_target_independent_requires_opt_in():
    repo = WorkflowRepository.from_mappings([{**mapping(target=None), "target_independent": True}])
    d = repo.list()[0]
    assert d.target_independent is True
    assert repo.resolve(d.name, d.revision, "any-target").resolved_target == "any-target"
    with pytest.raises(WorkflowError):
        WorkflowRepository.from_mappings([{**mapping(target=None)}])
    with pytest.raises((AttributeError, TypeError)):
        d.name = "changed"
