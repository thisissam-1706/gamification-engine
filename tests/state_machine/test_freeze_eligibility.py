"""Freeze eligibility rules and acquisition validation tests."""

import pytest

from src.state_machine.learner import Learner
from src.state_machine.rules import calculate_streak


def test_freeze_acquired_before_previous_activity_covers_gap():
    result = calculate_streak(
        ["2026-09-28", "2026-09-30"],
        [("f", "2026-09-25")],
    )

    assert result["2026-09-30"] == (2, 0, 1)


def test_freeze_acquired_after_resume_does_not_cover_gap():
    result = calculate_streak(
        ["2026-09-28", "2026-09-30"],
        [("f", "2026-10-01")],
    )

    assert result["2026-09-30"] == (1, 1, 0)


def test_freeze_acquired_on_missing_day_is_eligible():
    result = calculate_streak(
        ["2026-09-28", "2026-09-30"],
        [("f", "2026-09-29")],
    )

    assert result["2026-09-30"] == (2, 0, 1)


@pytest.mark.parametrize(
    ("freezes", "expected"),
    [
        (
            [("f1", "2026-09-25"), ("f2", "2026-09-26")],
            (2, 0, 2),
        ),
        (
            [("f1", "2026-09-25")],
            (1, 1, 0),
        ),
    ],
)
def test_two_missing_days_need_two_eligible_freezes(freezes, expected):
    result = calculate_streak(
        ["2026-09-28", "2026-10-01"],
        freezes,
    )

    assert result["2026-10-01"] == expected


def test_acquire_freeze_requires_date_before_first_activity():
    learner = Learner()

    with pytest.raises(ValueError, match="activity_date is required"):
        learner.acquire_freeze("f1")

    learner.complete_lesson("c1", "l1", activity_date="2026-09-28")
    assert learner.acquire_freeze("f2")
    assert learner.freeze_acquisitions["f2"] == "2026-09-28"
