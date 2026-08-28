import pytest

from kvmctl.sequences import (
    Action,
    SequenceLimits,
    SequencePlan,
    UnexpectedScreenPolicy,
    canonicalize_plan,
    plan_hash,
    validate_plan,
)


def test_constructs_and_hashes_mapping_plan():
    plan = SequencePlan.from_mapping({
        "target": "pve2",
        "actions": [
            {"type": "text", "value": "hostname"},
            {"type": "key", "value": "Enter"},
        ],
        "max_duration_ms": 5000,
        "unexpected_screen_policy": "abort",
    })
    assert plan.target == "pve2"
    assert plan.actions[0].kind == "text"
    assert plan.unexpected_screen_policy is UnexpectedScreenPolicy.ABORT
    assert plan_hash(plan) == plan_hash(plan.to_mapping())


def test_canonicalization_is_stable_and_normalizes_key_aliases():
    one = {"actions": [{"value": "ctrl+enter", "type": "key"}], "target": "pve2"}
    two = {"target": "pve2", "actions": [{"type": "key", "value": "ControlLeft+Enter"}], "unexpected_screen_policy": "abort"}
    assert canonicalize_plan(one) == canonicalize_plan(two)
    assert plan_hash(one) == plan_hash(two)


@pytest.mark.parametrize("mapping", [
    {"target": "pve2", "actions": [{"type": "bogus", "value": "x"}]},
    {"target": "pve2", "actions": []},
    {"target": "pve2", "actions": [{"type": "text", "value": "x"}] * 11},
    {"target": "pve2", "actions": [{"type": "text", "value": "x"}], "max_duration_ms": 0},
    {"target": "pve2", "actions": [{"type": "text", "value": "x"}], "max_duration_ms": 30001},
    {"target": "pve2", "actions": [{"type": "mouse_move_pct", "x_pct": 101, "y_pct": 0.5}]},
    {"target": "pve2", "actions": [{"type": "hold_key", "key": "A", "duration_ms": 0}]},
    {"target": "pve2", "actions": [{"type": "text", "value": "x"}], "unexpected_screen_policy": "retry"},
    {"target": "pve2", "actions": [{"type": "text", "value": "x", "extra": 1}]},
    {"target": "pve2", "actions": [{"type": "text", "value": "x"}], "extra": 1},
])
def test_rejects_invalid_plans(mapping):
    with pytest.raises((TypeError, ValueError)):
        SequencePlan.from_mapping(mapping)


def test_requires_one_nonempty_target():
    with pytest.raises((TypeError, ValueError)):
        SequencePlan.from_mapping({"target": "", "actions": [{"type": "text", "value": "x"}]})
    with pytest.raises((TypeError, ValueError)):
        SequencePlan.from_mapping({"target": ["a", "b"], "actions": [{"type": "text", "value": "x"}]})


def test_action_values_are_normalized_to_immutable_typed_actions():
    plan = SequencePlan.from_mapping({
        "target": "pve2", "actions": [
            {"type": "hold_key", "key": "ctrl", "duration_ms": 25.9},
            {"type": "mouse_move", "x": 10.0, "y": 20},
            {"type": "mouse_click", "button": "left", "count": 2.0},
            {"type": "mouse_scroll", "dx": -1.0, "dy": 3.0},
        ]
    })
    assert plan.actions == (
        Action("hold_key", key="ControlLeft", duration_ms=25),
        Action("mouse_move", x=10, y=20),
        Action("mouse_click", button="left", count=2),
        Action("mouse_scroll", dx=-1, dy=3),
    )
    with pytest.raises(Exception):
        plan.actions[0].kind = "text"


def test_validate_plan_accepts_plan_or_mapping_and_defaults_limits():
    plan = validate_plan({"target": "pve2", "actions": [{"type": "release_all"}]})
    assert isinstance(plan, SequencePlan)
    assert plan.max_duration_ms == SequenceLimits().max_duration_ms == 30000
    assert plan.to_mapping() == {
        "actions": [{"type": "release_all"}],
        "max_duration_ms": 30000,
        "target": "pve2",
        "unexpected_screen_policy": "abort",
    }
