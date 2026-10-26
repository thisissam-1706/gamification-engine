"""M6 follow-up: abuse rule set v2 adds review_misuse, receive_burst and future_dated sections."""

import json
from dataclasses import replace

import pytest

from src.abuse import rules as rules_module
from src.abuse.rules import (
    ABUSE_RULES,
    ABUSE_RULES_FILE,
    DEFAULT_ABUSE_RULES_VERSION,
    AbuseRuleConfigError,
    content_digest,
    get_abuse_rules,
    load_abuse_rules,
)

V1_SHA256 = "d9b25324f8e5578b644c579055e8414ddf658d5208cf7d2ee6b2ef10bd5adbff"
DOCUMENT = json.loads(ABUSE_RULES_FILE.read_text(encoding="utf-8"))
NEW = {
    "review_misuse": {"max_reviews_per_lesson": 3, "window_hours": 24, "orphan_min_lessons": 3},
    "receive_burst": {"max_completions": 40, "window_minutes": 10},
    "future_dated": {"tolerance_minutes": 5, "min_completions": 3},
}


def _write(tmp_path, entry, version="v2"):
    path = tmp_path / "abuse_rules.json"
    path.write_text(json.dumps({version: entry}), encoding="utf-8")
    return path


def _redigest(entry):
    body = {k: v for k, v in entry.items() if k != "sha256"}
    return {**body, "sha256": content_digest(body)}


def _v2():
    return json.loads(json.dumps(DOCUMENT["v2"]))


def test_v1_loads_unchanged_with_absent_new_sections():
    v1 = get_abuse_rules("v1")
    assert v1.digest == V1_SHA256 == DOCUMENT["v1"]["sha256"]
    assert (v1.review_misuse, v1.receive_burst, v1.future_dated) == (None, None, None)
    assert not {"review_misuse", "receive_burst", "future_dated"} & DOCUMENT["v1"].keys()


def test_v2_has_v1_values_plus_new_sections():
    v1, v2 = get_abuse_rules("v1"), get_abuse_rules("v2")
    assert v2.version == "v2" and v2.digest == DOCUMENT["v2"]["sha256"] != v1.digest
    for name in ("replay", "duplicate_exploit", "impossible_timing", "scripted_farming", "cohort_gaming_threshold"):
        assert getattr(v2, name) == getattr(v1, name), name
    assert (v2.review_misuse.max_reviews_per_lesson, v2.review_misuse.window_hours,
            v2.review_misuse.orphan_min_lessons) == (3, 24, 3)
    assert (v2.receive_burst.max_completions, v2.receive_burst.window_minutes) == (40, 10)
    assert (v2.future_dated.tolerance_minutes, v2.future_dated.min_completions) == (5, 3)


def test_both_versions_coexist_and_are_immutable():
    assert {"v1", "v2"} <= set(ABUSE_RULES)
    with pytest.raises(AttributeError):
        get_abuse_rules("v2").receive_burst.max_completions = 1  # type: ignore[misc]


def test_default_version_is_v2_and_defined_once():
    assert DEFAULT_ABUSE_RULES_VERSION == "v2"
    assert get_abuse_rules().version == "v2"
    from src.abuse import guardrail, service

    assert rules_module.get_abuse_rules.__defaults__ == (DEFAULT_ABUSE_RULES_VERSION,)
    assert service.AbuseDetectionService.__init__.__defaults__[0] == DEFAULT_ABUSE_RULES_VERSION
    assert guardrail.cohort_gaming_report.__defaults__ == (DEFAULT_ABUSE_RULES_VERSION,)


def test_replace_new_section_changes_digest():
    v2 = get_abuse_rules("v2")
    changed = replace(v2, receive_burst=replace(v2.receive_burst, max_completions=41))
    assert changed.digest != v2.digest


@pytest.mark.parametrize("section, key, value", [
    ("review_misuse", "max_reviews_per_lesson", 4),
    ("review_misuse", "window_hours", 25),
    ("review_misuse", "orphan_min_lessons", 4),
    ("receive_burst", "max_completions", 41),
    ("receive_burst", "window_minutes", 11),
    ("future_dated", "tolerance_minutes", 6),
    ("future_dated", "min_completions", 4),
])
def test_editing_a_v2_value_without_new_digest_is_rejected(tmp_path, section, key, value):
    entry = _v2()
    entry[section][key] = value
    with pytest.raises(AbuseRuleConfigError, match="does not match its sha256"):
        load_abuse_rules(_write(tmp_path, entry))


def test_editing_an_old_v2_value_without_new_digest_is_rejected(tmp_path):
    entry = _v2()
    entry["replay"]["window_minutes"] = 61
    with pytest.raises(AbuseRuleConfigError, match="does not match its sha256"):
        load_abuse_rules(_write(tmp_path, entry))


def test_removing_a_new_section_is_rejected_by_digest(tmp_path):
    entry = _v2()
    del entry["future_dated"]
    with pytest.raises(AbuseRuleConfigError, match="does not match its sha256"):
        load_abuse_rules(_write(tmp_path, entry))


def test_valid_new_digest_loads(tmp_path):
    entry = _v2()
    entry["receive_burst"]["max_completions"] = 50
    assert load_abuse_rules(_write(tmp_path, _redigest(entry)))["v2"].receive_burst.max_completions == 50


@pytest.mark.parametrize("section", list(NEW))
def test_unknown_and_missing_keys_in_new_sections_are_rejected(tmp_path, section):
    extra = _v2()
    extra[section]["surprise"] = 1
    with pytest.raises(AbuseRuleConfigError, match="unknown"):
        load_abuse_rules(_write(tmp_path, _redigest(extra)))
    missing = _v2()
    missing[section].pop(next(iter(NEW[section])))
    with pytest.raises(AbuseRuleConfigError, match="missing"):
        load_abuse_rules(_write(tmp_path, _redigest(missing)))


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "3", None])
@pytest.mark.parametrize("section, key", [(s, k) for s, body in NEW.items() for k in body])
def test_invalid_values_in_new_sections_are_rejected(tmp_path, section, key, bad):
    entry = _v2()
    entry[section][key] = bad
    with pytest.raises(AbuseRuleConfigError):
        load_abuse_rules(_write(tmp_path, _redigest(entry)))


def test_new_section_must_be_an_object(tmp_path):
    entry = _v2()
    entry["future_dated"] = 5
    with pytest.raises(AbuseRuleConfigError):
        load_abuse_rules(_write(tmp_path, _redigest(entry)))


def test_shadow_compare_between_v1_and_v2_still_works(tmp_path):
    from datetime import timedelta

    from src.abuse.service import AbuseDetectionService, compare_runs
    from src.abuse.store import AbuseSignalStore
    from tests.abuse.harness import EPOCH, Harness

    h = Harness(str(tmp_path / "e.db"))
    try:
        lid = h.new_learner()
        for i in range(5):
            h.complete(lid, EPOCH + timedelta(seconds=5 * i), lesson_id=f"l{i}")
        store = AbuseSignalStore(h.db)
        service = AbuseDetectionService(h.application, h.evidence, store)
        a = service.run(shadow=True, rules_version="v1")
        b = service.run(shadow=True, rules_version="v2")
        assert (a.rules_version, b.rules_version) == ("v1", "v2")
        assert compare_runs(a, b)["only_in_a"] == compare_runs(a, b)["only_in_b"] == []
        assert a.signals and {s.signal_id for s in a.signals}.isdisjoint({s.signal_id for s in b.signals})
        assert store.count() == 0
    finally:
        h.close()
