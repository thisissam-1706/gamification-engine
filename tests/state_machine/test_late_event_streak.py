"""AT-4: Late Event Streak Recalc (M2)

Given StreakCount = 5, Freezes = 1, last active 2026-09-20, when a valid event with occurred_at = 2026-09-22 (∆d = 2) arrives within 72h, then Freezes ≥ (∆d − 1) = 1 holds, so StreakCount = 6 and Freezes = 0.
"""
from src.state_machine.learner import Learner, LearnerApplication
from src.state_machine.projection import rebuild_learner_state


def test_older_completion_rebuilds_streak_after_xp_and_freeze_exist(tmp_path):
    """A late fact is ordered by activity day; replay issues no new rewards."""
    app = LearnerApplication(env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": str(tmp_path / "events.db")})
    try:
        learner = Learner()
        learner.complete_lesson("c20", "l1", xp_amount=10, activity_date="2026-09-20")
        learner.acquire_freeze("freeze-1")
        learner.complete_lesson("c22", "l2", xp_amount=10, activity_date="2026-09-22")
        app.save(learner)
        before_late = list(app.events.get(learner.id))
        before_state = rebuild_learner_state(before_late)
        assert before_state.xp_total == 20
        assert before_state.freezes_remaining == 0

        reloaded = app.repository.get(learner.id)
        reloaded.complete_lesson("c21", "l3", xp_amount=10, activity_date="2026-09-21")
        app.save(reloaded)
        after_late = list(app.events.get(learner.id))

        state = rebuild_learner_state(after_late)
        assert state.xp_total == 30
        assert state.activity_dates == ("2026-09-20", "2026-09-21", "2026-09-22")
        assert state.current_streak == 3
        assert state.freezes_remaining == 1
        assert {event.reward_id for event in after_late if isinstance(event, Learner.XPAwarded)} == {
            "xp:c20", "xp:c21", "xp:c22"
        }
        assert sum(isinstance(event, Learner.FreezeAcquired) for event in after_late) == 1
        assert sum(isinstance(event, Learner.XPAwarded) for event in after_late) == 3
        # The late completion emits its source, XP award, and a correction
        # for the already-processed later activity date.
        assert len(after_late) == len(before_late) + 4
    finally:
        app.close()


def test_at4_late_event_streak_recalc(tmp_path):
    """Given StreakCount = 5, Freezes = 1, last active 2026-09-20, when event with

    occurred_at = 2026-09-22 arrives, Freezes >= 1 holds, so StreakCount = 6 and Freezes = 0.
    """
    app = LearnerApplication(
        env={
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(tmp_path / "events.db"),
        }
    )
    try:
        learner = Learner()
        # 5 consecutive days: Sep 16 to Sep 20
        for i, day in enumerate(["2026-09-16", "2026-09-17", "2026-09-18", "2026-09-19", "2026-09-20"]):
            learner.complete_lesson(f"cmp_{i}", f"l_{i}", activity_date=day)
        learner.acquire_freeze("freeze_1")
        app.save(learner)

        restored = app.repository.get(learner.id)
        assert restored.current_streak == 5
        assert restored.freezes_remaining == 1

        # Arrives on 2026-09-22 (∆d = 2)
        restored.complete_lesson("cmp_22", "l_22", activity_date="2026-09-22")
        app.save(restored)

        updated = app.repository.get(learner.id)
        assert updated.current_streak == 6
        assert updated.freezes_remaining == 0

        # State rebuild yields the exact same state
        events = list(app.events.get(learner.id))
        rebuilt = rebuild_learner_state(events)
        assert rebuilt.current_streak == 6
        assert rebuilt.freezes_remaining == 0
    finally:
        app.close()
