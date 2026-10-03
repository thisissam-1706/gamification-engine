from datetime import date, timedelta
from src.state_machine.learner import Learner, LearnerApplication
from src.state_machine.projection import rebuild_learner_state


def test_deterministic_replay_checksum(tmp_path):
    """Given a sequence of events for learner L1, replaying twice yields bit-identical state vectors."""
    app = LearnerApplication(
        env={
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(tmp_path / "events.db"),
        }
    )
    try:
        learner = Learner()
        start_date = date(2026, 1, 1)
        # Generate 100 domain events (completions + XP + streak updates + freezes)
        for i in range(30):
            current_day = (start_date + timedelta(days=i)).isoformat()
            learner.complete_lesson(f"cmp_{i}", f"lesson_{i % 5}", activity_date=current_day)
            if i % 10 == 0:
                learner.acquire_freeze(f"freeze_{i}")
        app.save(learner)

        events = list(app.events.get(learner.id))
        assert len(events) >= 60

        # Replay 1
        state_pass_1 = rebuild_learner_state(events)
        # Replay 2
        state_pass_2 = rebuild_learner_state(events)

        assert state_pass_1 == state_pass_2
        assert state_pass_1.xp_total == state_pass_2.xp_total
        assert state_pass_1.current_streak == state_pass_2.current_streak
        assert state_pass_1.freezes_remaining == state_pass_2.freezes_remaining
        assert state_pass_1.activity_dates == state_pass_2.activity_dates
        assert state_pass_1.current_streak == 30
    finally:
        app.close()

