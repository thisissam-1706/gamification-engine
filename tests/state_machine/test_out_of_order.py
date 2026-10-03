from src.state_machine.learner import Learner, LearnerApplication
from src.state_machine.projection import rebuild_learner_state


def test_out_of_order_within_72h_triggers_correct_state_recalculation(tmp_path):
    """When an earlier event arrives out of order within 72h, folding the full event log produces

    the correct chronological streak and XP totals.
    """
    app = LearnerApplication(
        env={
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(tmp_path / "events.db"),
        }
    )
    try:
        learner = Learner()
        # Process Day 1 (2026-09-20) and Day 3 (2026-09-22)
        learner.complete_lesson("c20", "l1", activity_date="2026-09-20")
        learner.complete_lesson("c22", "l2", activity_date="2026-09-22")
        app.save(learner)

        # Before out-of-order event: streak was reset to 1 on 09-22 because 09-21 was missing
        interim_state = rebuild_learner_state(list(app.events.get(learner.id)))
        assert interim_state.current_streak == 1
        assert interim_state.xp_total == 200

        # Out-of-order event for 2026-09-21 arrives later
        reloaded = app.repository.get(learner.id)
        reloaded.complete_lesson("c21", "l3", activity_date="2026-09-21")
        app.save(reloaded)

        final_events = list(app.events.get(learner.id))
        final_state = rebuild_learner_state(final_events)
        # With 09-21 integrated, streak is 3 consecutive days
        assert final_state.current_streak == 3
        assert final_state.xp_total == 300
        assert final_state.activity_dates == ("2026-09-20", "2026-09-21", "2026-09-22")
    finally:
        app.close()

