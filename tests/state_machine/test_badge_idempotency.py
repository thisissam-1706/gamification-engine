"""Badge issuance is durable, idempotent, and replay-safe."""

from src.state_machine import rules
from src.state_machine.learner import Learner, LearnerApplication
from src.state_machine.projection import rebuild_learner_state


def test_week_warrior_issues_once_across_break_and_rebuild(tmp_path, monkeypatch):
    app = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": str(tmp_path / "e.db")}
    )
    try:
        learner = Learner()
        for day in range(1, 8):
            learner.complete_lesson(f"c{day}", "l1", activity_date=f"2026-09-{day:02d}")
        # D10 breaks the streak; D11-D17 reaches seven again.
        learner.complete_lesson("c10", "l1", activity_date="2026-09-10")
        for day in range(11, 18):
            learner.complete_lesson(f"c{day}", "l1", activity_date=f"2026-09-{day:02d}")
        app.save(learner)
        events = list(app.events.get(learner.id))
        awards = [event for event in events if isinstance(event, Learner.BadgeAwarded)]
        # Streaks 1..7 qualify twice, but badge identity permits exactly one award.
        assert len(awards) == 1
        assert awards[0].badge_id == "week_warrior_v1"
        assert learner.badges_issued == {"week_warrior_v1": "badge:week_warrior_v1"}
        assert sum(isinstance(event, Learner.XPAwarded) for event in events) == 15

        expected = rebuild_learner_state(events)
        assert expected.badges_issued == learner.badges_issued

        def boom(*args, **kwargs):
            raise AssertionError("badge predicate called during replay")

        monkeypatch.setattr(rules, "badge_qualifies", boom)
        assert rebuild_learner_state(events).badges_issued == expected.badges_issued
        assert len(list(app.events.get(learner.id))) == len(events)
    finally:
        app.close()
