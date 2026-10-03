"""Durable streak-update facts are deterministic and safe to replay."""

from src.state_machine.learner import Learner, LearnerApplication
from src.state_machine.projection import (
    LearnerStreakProjection,
    rebuild_learner_state,
)
from src.state_machine.rules import local_activity_date


def test_consecutive_days_increment_streak(tmp_path):
    """Activity across consecutive calendar days increments streak +1 each day."""
    app = LearnerApplication(
        env={
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(tmp_path / "events.db"),
        }
    )
    try:
        learner = Learner()
        assert learner.complete_lesson("c1", "l1", activity_date="2026-09-01") is True
        assert learner.complete_lesson("c2", "l2", activity_date="2026-09-02") is True
        assert learner.complete_lesson("c3", "l3", activity_date="2026-09-03") is True
        app.save(learner)

        restored = app.repository.get(learner.id)
        assert restored.current_streak == 3
        assert restored.freezes_remaining == 0
    finally:
        app.close()


def test_dated_completions_emit_streak_updates_and_ignore_same_day_activity(tmp_path):
    """Multiple completions on the same calendar day do not inflate streak."""
    app = LearnerApplication(
        env={
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(tmp_path / "events.db"),
        }
    )
    try:
        learner = Learner()
        learner.complete_lesson("c1", "l1", activity_date="2026-09-20")
        learner.complete_lesson("c2", "l2", activity_date="2026-09-20")
        learner.complete_lesson("c3", "l3", activity_date="2026-09-21")
        app.save(learner)

        updates = [
            event
            for event in app.events.get(learner.id)
            if isinstance(event, Learner.StreakUpdated)
        ]
        assert [(event.completion_id, event.current_streak, event.freezes_remaining) for event in updates] == [
            ("c1", 1, 0),
            ("c2", 1, 0),
            ("c3", 2, 0),
        ]

        restored = app.repository.get(learner.id)
        assert restored.current_streak == 2
        assert restored.freezes_remaining == 0
    finally:
        app.close()


def test_broken_streak_without_freezes_resets_to_one(tmp_path):
    """An uncovered gap in activity days resets the streak back to 1."""
    app = LearnerApplication(
        env={
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(tmp_path / "events.db"),
        }
    )
    try:
        learner = Learner()
        learner.complete_lesson("c1", "l1", activity_date="2026-09-01")
        learner.complete_lesson("c2", "l2", activity_date="2026-09-02")
        # Gap: no activity on 2026-09-03, next activity on 2026-09-04 without freezes
        learner.complete_lesson("c3", "l3", activity_date="2026-09-04")
        app.save(learner)

        restored = app.repository.get(learner.id)
        assert restored.current_streak == 1
        assert restored.freezes_remaining == 0
    finally:
        app.close()


def test_freezes_preserve_streak_across_missed_days(tmp_path):
    """Freezes bridge missed days, decrementing freeze balance and continuing streak."""
    app = LearnerApplication(
        env={
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(tmp_path / "events.db"),
        }
    )
    try:
        learner = Learner()
        learner.complete_lesson("c1", "l1", activity_date="2026-09-10")
        learner.acquire_freeze("f1")
        learner.acquire_freeze("f2")
        # 2 missed days: 2026-09-11 and 2026-09-12 (delta = 3 days, missing = 2)
        learner.complete_lesson("c2", "l2", activity_date="2026-09-13")
        app.save(learner)

        restored = app.repository.get(learner.id)
        assert restored.current_streak == 2
        assert restored.freezes_remaining == 0
    finally:
        app.close()


def test_timezone_calculation_derives_correct_local_activity_date():
    """Local activity date correctly converts UTC ISO timestamp to learner IANA timezone."""
    # 23:30 UTC on 2026-10-03 is 05:00 on 2026-10-04 in Asia/Kolkata (UTC+5:30)
    kolkata_date = local_activity_date("2026-10-03T23:30:00Z", "Asia/Kolkata")
    assert kolkata_date == "2026-10-04"

    # 02:00 UTC on 2026-10-03 is 22:00 on 2026-10-02 in America/New_York (UTC-4:00 EDT)
    ny_date = local_activity_date("2026-10-03T02:00:00Z", "America/New_York")
    assert ny_date == "2026-10-02"


def test_complete_lesson_with_occurred_at_and_timezone(tmp_path):
    """Learner aggregate accepts occurred_at + timezone to derive activity date."""
    app = LearnerApplication(
        env={
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(tmp_path / "events.db"),
        }
    )
    try:
        learner = Learner()
        learner.complete_lesson(
            "c1",
            "l1",
            occurred_at="2026-10-03T23:30:00Z",
            timezone="Asia/Kolkata",
        )
        app.save(learner)

        restored = app.repository.get(learner.id)
        assert restored.activity_dates == {"2026-10-04"}
        assert restored.current_streak == 1
    finally:
        app.close()


def test_streak_projection_rebuild(tmp_path):
    """LearnerStreakProjection reconstructs exact streak and freeze summary after wipe."""
    app = LearnerApplication(
        env={
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(tmp_path / "events.db"),
        }
    )
    try:
        learner = Learner()
        learner.complete_lesson("c1", "l1", activity_date="2026-09-01")
        learner.acquire_freeze("f1")
        learner.complete_lesson("c2", "l2", activity_date="2026-09-03")
        app.save(learner)

        events = list(app.events.get(learner.id))
        projection = LearnerStreakProjection()
        projection.rebuild(events)

        summary = projection.streak_summaries[learner.id]
        assert summary.current_streak == 2
        assert summary.freezes_remaining == 0
    finally:
        app.close()


def test_late_completion_emits_recalculated_streak_without_duplicate_rewards(tmp_path):
    """Replaying or applying late events recalculates streak and restores unused freeze."""
    app = LearnerApplication(
        env={
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(tmp_path / "events.db"),
        }
    )
    try:
        learner = Learner()
        learner.complete_lesson("c20", "l1", activity_date="2026-09-20")
        learner.acquire_freeze("freeze-1")
        learner.complete_lesson("c22", "l2", activity_date="2026-09-22")
        app.save(learner)

        learner = app.repository.get(learner.id)
        learner.complete_lesson("c21", "l3", activity_date="2026-09-21")
        app.save(learner)
        events = list(app.events.get(learner.id))

        updates = [event for event in events if isinstance(event, Learner.StreakUpdated)]
        assert (updates[-1].completion_id, updates[-1].current_streak, updates[-1].freezes_remaining) == (
            "c21", 3, 1
        )
        assert rebuild_learner_state(events).current_streak == 3
        assert len([event for event in events if isinstance(event, Learner.XPAwarded)]) == 3
    finally:
        app.close()

