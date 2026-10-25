"""Tests for the exp_007 primary metric."""

from src.experiments.metrics import first_eligible_checkpoint_mastery


def test_first_eligible_checkpoint_mastery_uses_first_attempt_and_fourteen_day_window():
    assignments = {
        f"L{learner}": "2026-01-01T00:00:00+00:00" for learner in range(1, 11)
    }
    attempts = [
        {
            "learner_id": "L1",
            "attempt_id": "A1",
            "assessment_id": "checkpoint",
            "assessment_version": 1,
            "occurred_at": "2026-01-02T00:00:00+00:00",
        },
        {
            "learner_id": "L2",
            "attempt_id": "A2",
            "assessment_id": "checkpoint",
            "assessment_version": 1,
            "occurred_at": "2026-01-03T00:00:00+00:00",
        },
        {
            "learner_id": "L3",
            "attempt_id": "A3",
            "assessment_id": "checkpoint",
            "assessment_version": 1,
            "occurred_at": "2026-01-04T00:00:00+00:00",
        },
        {
            "learner_id": "L3",
            "attempt_id": "A3-later",
            "assessment_id": "checkpoint",
            "assessment_version": 1,
            "occurred_at": "2026-01-05T00:00:00+00:00",
        },
        {
            "learner_id": "L4",
            "attempt_id": "A4",
            "assessment_id": "checkpoint",
            "assessment_version": 1,
            "occurred_at": "2026-01-04T00:00:00+00:00",
        },
        {
            "learner_id": "L5",
            "attempt_id": "A5",
            "assessment_id": "checkpoint",
            "assessment_version": 1,
            "occurred_at": "2026-01-05T00:00:00+00:00",
        },
        {
            "learner_id": "L6",
            "attempt_id": "A6",
            "assessment_id": "checkpoint",
            "assessment_version": 1,
            "occurred_at": "2026-01-06T00:00:00+00:00",
        },
        {
            "learner_id": "L7",
            "attempt_id": "A7",
            "assessment_id": "checkpoint",
            "assessment_version": 1,
            "occurred_at": "2026-01-07T00:00:00+00:00",
        },
        {
            "learner_id": "L8",
            "attempt_id": "A8",
            "assessment_id": "checkpoint",
            "assessment_version": 1,
            "occurred_at": "2026-01-08T00:00:00+00:00",
        },
        {
            "learner_id": "L9",
            "attempt_id": "A9",
            "assessment_id": "checkpoint",
            "assessment_version": 1,
            "occurred_at": "2026-01-09T00:00:00+00:00",
        },
        {
            "learner_id": "L10",
            "attempt_id": "A10",
            "assessment_id": "checkpoint",
            "assessment_version": 1,
            "occurred_at": "2026-01-16T00:00:00+00:00",
        },
    ]
    completions = [
        {
            "attempt_id": "A1",
            "assessment_id": "checkpoint",
            "assessment_version": 1,
            "passed": True,
            "occurred_at": "2026-01-02T01:00:00+00:00",
        },
        {
            "attempt_id": "A2",
            "assessment_id": "checkpoint",
            "assessment_version": 1,
            "passed": False,
            "occurred_at": "2026-01-03T01:00:00+00:00",
        },
        {
            "attempt_id": "A3",
            "assessment_id": "checkpoint",
            "assessment_version": 1,
            "passed": False,
            "occurred_at": "2026-01-04T01:00:00+00:00",
        },
        {
            "attempt_id": "A3-later",
            "assessment_id": "checkpoint",
            "assessment_version": 1,
            "passed": True,
            "occurred_at": "2026-01-05T01:00:00+00:00",
        },
        {
            "attempt_id": "A4",
            "assessment_id": "checkpoint",
            "assessment_version": 1,
            "passed": True,
            "occurred_at": "2026-01-20T00:00:00+00:00",
        },
        {
            "attempt_id": "A5",
            "assessment_id": "other-checkpoint",
            "assessment_version": 1,
            "passed": True,
            "occurred_at": "2026-01-05T01:00:00+00:00",
        },
        {
            "attempt_id": "A6",
            "assessment_id": "checkpoint",
            "assessment_version": 2,
            "passed": True,
            "occurred_at": "2026-01-06T01:00:00+00:00",
        },
        {
            "attempt_id": "A7",
            "assessment_id": "checkpoint",
            "assessment_version": 1,
            "passed": True,
            "occurred_at": "2026-01-07T01:00:00+00:00",
        },
    ]

    # L1 and L7 passed: 2 mastered / 10 eligible assigned = 20%.
    assert first_eligible_checkpoint_mastery(assignments, attempts, completions, as_of="2100-01-01T00:00:00Z") == 0.2
