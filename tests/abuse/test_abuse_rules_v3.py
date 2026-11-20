"""Abuse rule set v3: v2 with the scripted-farming repeat threshold lowered from 10 to 4.

Everything else in v3 matches v2, v2 itself is unchanged (pinned digest), and v3 is the default.
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.abuse.detectors import CompletionFact, detect_scripted_farming
from src.abuse.rules import DEFAULT_ABUSE_RULES_VERSION, get_abuse_rules, load_abuse_rules

T0 = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)
V3, V2 = get_abuse_rules("v3"), get_abuse_rules("v2")


def repeats(n, minutes_apart=30):
    # one first completion, then n repeats of the same lesson
    rows = [CompletionFact("L1", "c000", "e000", "lesson-1", "lesson", T0, 0, 100)]
    for i in range(1, n + 1):
        rows.append(CompletionFact("L1", f"c{i:03d}", f"e{i:03d}", "lesson-1", "lesson",
                                   T0 + timedelta(minutes=minutes_apart * i), i, 100))
    return rows


def test_v3_is_the_default_and_only_changes_the_repeat_threshold():
    assert DEFAULT_ABUSE_RULES_VERSION == "v3"
    assert V3.scripted_farming.repeat.min_repeat_completions == 4
    assert V2.scripted_farming.repeat.min_repeat_completions == 10
    assert V3.scripted_farming.repeat.window_hours == V2.scripted_farming.repeat.window_hours
    for section in ("replay", "duplicate_exploit", "impossible_timing", "review_misuse", "receive_burst", "future_dated"):
        assert getattr(V3, section) == getattr(V2, section), section
    assert V3.scripted_farming.metronome == V2.scripted_farming.metronome
    assert V3.cohort_gaming_threshold == V2.cohort_gaming_threshold
    assert V3.digest != V2.digest
    assert set(load_abuse_rules()) == {"v1", "v2", "v3"}


@pytest.mark.parametrize("n, flagged", [(3, False), (4, True), (6, True)])
def test_four_repeats_flag_under_v3(n, flagged):
    rules = [s.rule_id for s in detect_scripted_farming(V3, repeats(n))]
    assert ("scripted_farming.repeat" in rules) is flagged


def test_v2_still_needs_ten(n=9):
    assert detect_scripted_farming(V2, repeats(n)) == []
    assert [s.rule_id for s in detect_scripted_farming(V2, repeats(10))] == ["scripted_farming.repeat"]


def test_four_repeats_spread_past_24_hours_do_not_flag():
    assert detect_scripted_farming(V3, repeats(4, minutes_apart=8 * 60 + 1)) == []
