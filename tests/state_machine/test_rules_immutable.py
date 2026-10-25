"""Week 4 Fix 9a: rule sets are immutable and come from a verified artifact.

Week 3 review row 26: a frozen RuleSet still held a mutable quest_targets
dict, and RULESETS could be changed with no versioned configuration artifact.
"""

import json
from pathlib import Path

import pytest

from src.state_machine.rules import (
    QUEST_DEFINITIONS,
    RULESETS,
    RULESETS_FILE,
    XP_AMOUNTS,
    RuleConfigError,
    RuleSet,
    get_ruleset,
    load_rulesets,
)

# Golden digest of v1's content. Changing any v1 value requires a NEW version
# (v2, ...) with its own digest; never edit v1 and update this literal.
V1_SHA256 = "ba6d0a3821ebdb1358840ec7a0dd2f67136b4eeb3062d79f3369b53cda6b4430"

V1_ENTRY = {
    "xp_amount": 100,
    "quest_targets": {"lesson_starter_v1": 3},
    "badge_threshold": 7,
    "badge_id": "week_warrior_v1",
    "sha256": V1_SHA256,
}


def _write(tmp_path: Path, document) -> Path:
    path = tmp_path / "rulesets.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _ruleset(**overrides) -> RuleSet:
    fields = {
        "xp_amount": 100,
        "quest_targets": {"lesson_starter_v1": 3},
        "badge_threshold": 7,
        "badge_id": "week_warrior_v1",
    }
    fields.update(overrides)
    return RuleSet(**fields)


# -- immutability -------------------------------------------------------------


def test_quest_targets_cannot_be_mutated():
    ruleset = get_ruleset("v1")
    with pytest.raises(TypeError):
        ruleset.quest_targets["lesson_starter_v1"] = 99  # type: ignore[index]
    with pytest.raises(TypeError):
        del ruleset.quest_targets["lesson_starter_v1"]  # type: ignore[attr-defined]
    assert ruleset.quest_targets["lesson_starter_v1"] == 3


def test_constructor_dict_is_copied():
    targets = {"lesson_starter_v1": 3}
    ruleset = _ruleset(quest_targets=targets)
    targets["lesson_starter_v1"] = 99
    targets["new_quest"] = 1
    assert dict(ruleset.quest_targets) == {"lesson_starter_v1": 3}


def test_registry_and_compatibility_views_are_read_only():
    with pytest.raises(TypeError):
        RULESETS["x"] = _ruleset()  # type: ignore[index]
    with pytest.raises(TypeError):
        del RULESETS["v1"]  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        XP_AMOUNTS["v1"] = 150  # type: ignore[index]
    with pytest.raises(TypeError):
        QUEST_DEFINITIONS["lesson_starter_v1"]["target"] = 99  # type: ignore[index]
    assert dict(XP_AMOUNTS) == {"v1": 100}
    assert QUEST_DEFINITIONS["lesson_starter_v1"]["target"] == 3


def test_ruleset_fields_stay_frozen():
    with pytest.raises(AttributeError):
        get_ruleset("v1").xp_amount = 150  # type: ignore[misc]


# -- packaged artifact --------------------------------------------------------


def test_packaged_file_loads_exact_v1():
    assert RULESETS_FILE == Path(__file__).resolve().parents[2] / "src" / "state_machine" / "rulesets.json"
    loaded = load_rulesets()
    assert list(loaded) == ["v1"]
    v1 = loaded["v1"]
    assert v1.xp_amount == 100
    assert dict(v1.quest_targets) == {"lesson_starter_v1": 3}
    assert v1.badge_threshold == 7
    assert v1.badge_id == "week_warrior_v1"
    assert loaded == dict(RULESETS)


def test_v1_digest_is_golden():
    # See V1_SHA256: a v1 content change must become a new version instead.
    assert get_ruleset("v1").digest == V1_SHA256
    assert json.loads(RULESETS_FILE.read_text(encoding="utf-8"))["v1"]["sha256"] == V1_SHA256


def test_get_ruleset_behaviour_is_unchanged():
    assert get_ruleset() is RULESETS["v1"]
    explicit = _ruleset(xp_amount=5)
    assert get_ruleset(explicit) is explicit
    with pytest.raises(KeyError, match="Unknown rule version: v9"):
        get_ruleset("v9")


# -- loader rejections ----------------------------------------------------------


def test_tampered_value_with_old_digest_is_rejected(tmp_path):
    path = _write(tmp_path, {"v1": {**V1_ENTRY, "xp_amount": 150}})
    with pytest.raises(RuleConfigError, match="does not match its sha256"):
        load_rulesets(path)


def test_tampered_quest_target_with_old_digest_is_rejected(tmp_path):
    path = _write(tmp_path, {"v1": {**V1_ENTRY, "quest_targets": {"lesson_starter_v1": 2}}})
    with pytest.raises(RuleConfigError, match="does not match its sha256"):
        load_rulesets(path)


@pytest.mark.parametrize("missing", ["xp_amount", "quest_targets", "badge_threshold", "badge_id", "sha256"])
def test_missing_key_is_rejected(tmp_path, missing):
    entry = {key: value for key, value in V1_ENTRY.items() if key != missing}
    with pytest.raises(RuleConfigError, match="missing"):
        load_rulesets(_write(tmp_path, {"v1": entry}))


@pytest.mark.parametrize(
    "field, value",
    [
        ("xp_amount", 0),
        ("xp_amount", -100),
        ("xp_amount", "100"),
        ("xp_amount", True),
        ("badge_threshold", 0),
        ("badge_threshold", 7.0),
        ("badge_id", ""),
        ("badge_id", "   "),
        ("quest_targets", {"lesson_starter_v1": 0}),
        ("quest_targets", {"lesson_starter_v1": -3}),
        ("quest_targets", {"": 3}),
        ("quest_targets", ["lesson_starter_v1"]),
    ],
)
def test_invalid_value_is_rejected_by_loader_and_constructor(tmp_path, field, value):
    with pytest.raises(RuleConfigError):
        load_rulesets(_write(tmp_path, {"v1": {**V1_ENTRY, field: value}}))
    with pytest.raises(RuleConfigError):
        _ruleset(**{field: value})


def test_unknown_key_and_malformed_files_are_rejected(tmp_path):
    with pytest.raises(RuleConfigError, match="unknown keys"):
        load_rulesets(_write(tmp_path, {"v1": {**V1_ENTRY, "xp_amonut": 100}}))
    with pytest.raises(RuleConfigError):
        load_rulesets(_write(tmp_path, {}))
    with pytest.raises(RuleConfigError):
        load_rulesets(_write(tmp_path, ["v1"]))
    bad_json = tmp_path / "broken.json"
    bad_json.write_text("{not json", encoding="utf-8")
    with pytest.raises(RuleConfigError):
        load_rulesets(bad_json)
    with pytest.raises(RuleConfigError):
        load_rulesets(tmp_path / "absent.json")


def test_a_correctly_digested_new_version_loads(tmp_path):
    v2 = _ruleset(xp_amount=150)
    path = _write(
        tmp_path,
        {
            "v1": V1_ENTRY,
            "v2": {
                "xp_amount": 150,
                "quest_targets": {"lesson_starter_v1": 3},
                "badge_threshold": 7,
                "badge_id": "week_warrior_v1",
                "sha256": v2.digest,
            },
        },
    )
    loaded = load_rulesets(path)
    assert loaded["v2"].xp_amount == 150
    assert loaded["v1"].digest == V1_SHA256
    assert v2.digest != V1_SHA256
