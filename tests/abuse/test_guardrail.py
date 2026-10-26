"""M6 cohort gaming guardrail (metrics-framework 5.3 / exp_007): repeat-XP share per cohort-week."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from src.abuse.detectors import CompletionFact
from src.abuse.guardrail import XPAwardFact, cohort_gaming_report
from src.abuse.rules import get_abuse_rules

RULES = get_abuse_rules("v1")
WEEK = "2026-W42"  # Mon 2026-10-12 .. Sun 2026-10-18 (UTC)
IN_WEEK = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


def award(learner, xp, repeat, when=IN_WEEK):
    return XPAwardFact(learner, when, xp, repeat)


def by_cohort(report):
    return {r.cohort_id: r for r in report}


def test_exactly_fifteen_percent_is_not_breached():
    awards = [award("a", 85, 0), award("a", 15, 1)]
    [report] = cohort_gaming_report(awards, {"a": "c1"}, WEEK, RULES)
    assert (report.total_xp, report.repeat_xp, report.share) == (100, 15, 0.15)
    assert report.breached is False
    assert report.threshold == 0.15 and report.week == WEEK


def test_just_over_fifteen_percent_is_breached():
    awards = [award("a", 84, 0), award("a", 16, 2)]
    [report] = cohort_gaming_report(awards, {"a": "c1"}, WEEK, RULES)
    assert report.share == 0.16 and report.breached is True


def test_other_thresholds_come_from_rules():
    rules = replace(RULES, cohort_gaming_threshold=0.5)
    awards = [award("a", 50, 0), award("a", 50, 1)]
    assert cohort_gaming_report(awards, {"a": "c"}, WEEK, rules)[0].breached is False
    awards = [award("a", 49, 0), award("a", 51, 1)]
    assert cohort_gaming_report(awards, {"a": "c"}, WEEK, rules)[0].breached is True


def test_zero_total_xp_is_share_zero_and_not_breached():
    awards = [award("a", 0, 5), award("a", 0, 0)]
    [report] = cohort_gaming_report(awards, {"a": "c1"}, WEEK, RULES)
    assert (report.total_xp, report.repeat_xp, report.share, report.breached) == (0, 0, 0.0, False)


def test_empty_cohort_and_no_awards():
    report = cohort_gaming_report([], {"a": "c1", "b": "c2"}, WEEK, RULES)
    assert [(r.cohort_id, r.total_xp, r.breached) for r in report] == [("c1", 0, False), ("c2", 0, False)]
    assert cohort_gaming_report([], {}, WEEK, RULES) == []


def test_repeat_index_none_counts_as_non_repeat():
    awards = [award("a", 100, None), award("a", 100, 0)]
    [report] = cohort_gaming_report(awards, {"a": "c1"}, WEEK, RULES)
    assert (report.total_xp, report.repeat_xp, report.breached) == (200, 0, False)


def test_cohorts_are_isolated_and_sorted():
    awards = [
        award("a", 50, 0), award("a", 50, 1),   # c2: 50% repeat
        award("b", 100, 0),                      # c1: 0%
        award("x", 1000, 3),                     # unmapped learner is ignored
    ]
    report = cohort_gaming_report(awards, {"a": "c2", "b": "c1"}, WEEK, RULES)
    assert [r.cohort_id for r in report] == ["c1", "c2"]
    got = by_cohort(report)
    assert (got["c1"].repeat_xp, got["c1"].breached) == (0, False)
    assert (got["c2"].share, got["c2"].breached) == (0.5, True)


def test_weeks_are_isolated():
    next_week = IN_WEEK + timedelta(days=7)
    awards = [award("a", 100, 0), award("a", 100, 1, next_week)]
    assert cohort_gaming_report(awards, {"a": "c"}, WEEK, RULES)[0].repeat_xp == 0
    assert cohort_gaming_report(awards, {"a": "c"}, "2026-W43", RULES)[0].repeat_xp == 100


def test_week_boundaries_are_computed_in_utc():
    sunday_end = datetime(2026, 10, 18, 23, 59, 59, tzinfo=timezone.utc)
    monday_start = datetime(2026, 10, 19, 0, 0, 0, tzinfo=timezone.utc)
    ist = timezone(timedelta(hours=5, minutes=30))
    # Monday 02:00 IST is Sunday 20:30 UTC: still week 42.
    monday_ist = datetime(2026, 10, 19, 2, 0, tzinfo=ist)
    awards = [award("a", 10, 0, sunday_end), award("a", 20, 0, monday_start), award("a", 30, 0, monday_ist)]
    assert cohort_gaming_report(awards, {"a": "c"}, "2026-W42", RULES)[0].total_xp == 40
    assert cohort_gaming_report(awards, {"a": "c"}, "2026-W43", RULES)[0].total_xp == 20


def test_naive_timestamps_are_utc_and_iso_year_edges():
    naive = datetime(2026, 12, 31, 12)  # ISO week 2026-W53
    assert cohort_gaming_report([award("a", 5, 0, naive)], {"a": "c"}, "2026-W53", RULES)[0].total_xp == 5
    jan = datetime(2027, 1, 1, tzinfo=timezone.utc)  # still 2026-W53
    assert cohort_gaming_report([award("a", 7, 0, jan)], {"a": "c"}, "2026-W53", RULES)[0].total_xp == 7


def test_accepts_completion_facts_directly():
    fact = CompletionFact("a", "c1", "e1", "l", "lesson", IN_WEEK, 1, 40)
    [report] = cohort_gaming_report([fact], {"a": "c"}, WEEK, RULES)
    assert (report.total_xp, report.repeat_xp) == (40, 40)


def test_bad_week_key_is_rejected():
    for bad in ("2026-42", "2026-W4", "W42", ""):
        with pytest.raises(ValueError):
            cohort_gaming_report([], {}, bad, RULES)


def test_result_is_order_independent():
    awards = [award("a", 30, 0), award("a", 30, 1), award("b", 10, 0), award("b", 90, 4)]
    forward = cohort_gaming_report(awards, {"a": "c1", "b": "c2"}, WEEK, RULES)
    assert cohort_gaming_report(awards[::-1], {"b": "c2", "a": "c1"}, WEEK, RULES) == forward
