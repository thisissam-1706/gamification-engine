"""M6: abuse_rules.json is versioned, sha-256 pinned, strict and immutable."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.abuse.rules import (
    ABUSE_RULES,
    ABUSE_RULES_FILE,
    AbuseRuleConfigError,
    get_abuse_rules,
    load_abuse_rules,
)

ENTRY = json.loads(ABUSE_RULES_FILE.read_text(encoding="utf-8"))["v1"]


def _write(tmp_path: Path, entry) -> Path:
    path = tmp_path / "abuse_rules.json"
    path.write_text(json.dumps({"v1": entry}), encoding="utf-8")
    return path


def _with_digest(entry: dict) -> dict:
    from src.abuse.rules import content_digest

    body = {k: v for k, v in entry.items() if k != "sha256"}
    return {**body, "sha256": content_digest(body)}


def test_packaged_v1_has_the_specified_defaults():
    rules = get_abuse_rules("v1")
    assert "v1" in ABUSE_RULES
    assert rules.version == "v1"
    assert (rules.replay.window_minutes, rules.replay.min_distinct_replayed_events) == (60, 5)
    assert rules.replay.flag_conflicting_event_id_reuse is True
    assert (
        rules.duplicate_exploit.window_minutes,
        rules.duplicate_exploit.min_distinct_resubmitted_completions,
        rules.duplicate_exploit.flag_reward_field_in_source_event,
    ) == (60, 3, True)
    timing = rules.impossible_timing
    assert timing.default_min_gap_seconds == 20
    assert dict(timing.min_gap_seconds) == {"lesson": 20, "review": 10, "remediation": 20}
    assert (timing.short_gaps_required, timing.short_gap_window_minutes) == (3, 10)
    assert (timing.burst.max_completions, timing.burst.window_minutes) == (20, 10)
    farming = rules.scripted_farming
    assert (farming.repeat.window_hours, farming.repeat.min_repeat_completions) == (24, 10)
    assert (
        farming.metronome.min_events,
        farming.metronome.max_cv,
        farming.metronome.min_mean_gap_seconds,
    ) == (12, 0.05, 1)
    assert rules.cohort_gaming_threshold == 0.15


def test_digest_matches_file_and_is_stable():
    assert get_abuse_rules("v1").digest == ENTRY["sha256"]
    assert len(get_abuse_rules("v1").digest) == 64


def test_loaded_rules_are_immutable():
    rules = get_abuse_rules("v1")
    with pytest.raises(AttributeError):
        rules.replay.window_minutes = 1  # type: ignore[misc]
    with pytest.raises(TypeError):
        rules.impossible_timing.min_gap_seconds["lesson"] = 1  # type: ignore[index]
    with pytest.raises(TypeError):
        ABUSE_RULES["v2"] = rules  # type: ignore[index]


def test_replace_changes_the_digest():
    rules = get_abuse_rules("v1")
    changed = replace(rules, replay=replace(rules.replay, min_distinct_replayed_events=6))
    assert changed.digest != rules.digest


def test_tampered_value_with_old_digest_is_rejected(tmp_path):
    entry = json.loads(json.dumps(ENTRY))
    entry["replay"]["min_distinct_replayed_events"] = 9
    with pytest.raises(AbuseRuleConfigError, match="does not match its sha256"):
        load_abuse_rules(_write(tmp_path, entry))


def test_valid_new_digest_loads(tmp_path):
    entry = json.loads(json.dumps(ENTRY))
    entry["replay"]["min_distinct_replayed_events"] = 9
    loaded = load_abuse_rules(_write(tmp_path, _with_digest(entry)))
    assert loaded["v1"].replay.min_distinct_replayed_events == 9


@pytest.mark.parametrize("section", ["replay", "duplicate_exploit", "impossible_timing", "scripted_farming", "cohort_gaming_threshold", "sha256"])
def test_missing_key_is_rejected(tmp_path, section):
    entry = {k: v for k, v in ENTRY.items() if k != section}
    with pytest.raises(AbuseRuleConfigError, match="missing"):
        load_abuse_rules(_write(tmp_path, entry))


def test_unknown_keys_are_rejected_at_every_level(tmp_path):
    top = _with_digest({**ENTRY, "surprise": 1})
    with pytest.raises(AbuseRuleConfigError, match="unknown"):
        load_abuse_rules(_write(tmp_path, top))
    nested = json.loads(json.dumps(ENTRY))
    nested["impossible_timing"]["burst"]["extra"] = 1
    with pytest.raises(AbuseRuleConfigError, match="unknown"):
        load_abuse_rules(_write(tmp_path, _with_digest(nested)))


@pytest.mark.parametrize(
    "path, value",
    [
        (("replay", "window_minutes"), 0),
        (("replay", "window_minutes"), True),
        (("replay", "window_minutes"), 1.5),
        (("replay", "flag_conflicting_event_id_reuse"), "yes"),
        (("impossible_timing", "min_gap_seconds", "lesson"), -1),
        (("scripted_farming", "metronome", "max_cv"), 0),
        (("scripted_farming", "metronome", "max_cv"), "0.05"),
        (("cohort_gaming_threshold",), 1.0),
        (("cohort_gaming_threshold",), 0),
    ],
)
def test_invalid_values_are_rejected(tmp_path, path, value):
    entry = json.loads(json.dumps(ENTRY))
    target = entry
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(AbuseRuleConfigError):
        load_abuse_rules(_write(tmp_path, _with_digest(entry)))


def test_min_gap_seconds_needs_exactly_the_three_kinds(tmp_path):
    entry = json.loads(json.dumps(ENTRY))
    del entry["impossible_timing"]["min_gap_seconds"]["review"]
    with pytest.raises(AbuseRuleConfigError):
        load_abuse_rules(_write(tmp_path, _with_digest(entry)))


def test_unreadable_and_empty_files_are_rejected(tmp_path):
    with pytest.raises(AbuseRuleConfigError):
        load_abuse_rules(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(AbuseRuleConfigError):
        load_abuse_rules(bad)
    bad.write_text("not json", encoding="utf-8")
    with pytest.raises(AbuseRuleConfigError):
        load_abuse_rules(bad)


def test_unknown_version_raises():
    with pytest.raises(KeyError, match="Unknown abuse rule version: v9"):
        get_abuse_rules("v9")
