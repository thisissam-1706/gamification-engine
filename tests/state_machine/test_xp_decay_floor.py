"""v3: the v2 decay schedule with a 5 XP floor (100, 50, 30, 10, 5, 5, ...)."""

import json

import pytest

from src.state_machine.rules import (
    DECAY_RULESETS_FILE,
    _REGISTRY,
    RuleConfigError,
    RuleSet,
    load_decay_rulesets,
    xp_for_completion,
)


@pytest.fixture
def decay(monkeypatch):
    """Registers the packaged decay rule sets for one test only (no leak into RULESETS)."""
    for version, ruleset in load_decay_rulesets().items():
        monkeypatch.setitem(_REGISTRY, version, ruleset)


@pytest.mark.parametrize("n, expected", [(0, 100), (1, 50), (2, 30), (3, 10), (4, 5), (5, 5), (20, 5)])
def test_v3_decays_to_a_floor_of_5(decay, n, expected):
    assert xp_for_completion("v3", n) == expected


def test_v2_still_decays_to_0(decay):
    assert [xp_for_completion("v2", n) for n in range(6)] == [100, 50, 30, 10, 0, 0]


def test_v3_is_v2_plus_floor_with_its_own_pinned_digest():
    loaded = load_decay_rulesets()
    v2, v3 = loaded["v2"], loaded["v3"]
    assert dict(v3.decay) == {**dict(v2.decay), "floor_xp": 5}
    assert (v3.xp_amount, dict(v3.quest_targets), v3.badge_threshold, v3.badge_id) == (
        v2.xp_amount, dict(v2.quest_targets), v2.badge_threshold, v2.badge_id,
    )
    assert v3.digest != v2.digest
    document = json.loads(DECAY_RULESETS_FILE.read_text(encoding="utf-8"))
    assert document["v3"]["sha256"] == v3.digest
    assert "floor_xp" not in document["v2"]["decay"]  # v2 content (and digest) unchanged


@pytest.mark.parametrize("floor", [-1, 1.5, True, "5"])
def test_invalid_floor_is_rejected(floor):
    with pytest.raises(RuleConfigError, match="floor_xp"):
        RuleSet(100, {"q": 1}, 7, "b", decay={"first_repeat_percent": 50, "step_xp": 20, "floor_xp": floor})


def test_unknown_decay_key_is_still_rejected():
    with pytest.raises(RuleConfigError, match="decay must have"):
        RuleSet(100, {"q": 1}, 7, "b", decay={"first_repeat_percent": 50, "step_xp": 20, "cap": 5})
