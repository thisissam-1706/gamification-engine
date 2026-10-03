"""Hand-calculated streak and replay contract tests."""

from src.state_machine import rules
from src.state_machine.learner import Learner, LearnerApplication
from src.state_machine.projection import rebuild_learner_state


def _stored(app: LearnerApplication, learner: Learner):
    return list(app.events.get(learner.id))


def test_t1_first_activity_has_no_remediation_quest():
    learner = Learner()
    assert learner.complete_lesson("c1", "l1", activity_date="2026-09-01")
    # First activity has previous 0, new 1, and no remediation assignment.
    update = learner.__class__.StreakUpdated
    event = next(event for event in learner.pending_events if isinstance(event, update))
    assert (event.previous_streak, event.new_streak) == (0, 1)
    assert learner.assigned_quest_ids == set()


def test_t2_consecutive_days_reach_three(tmp_path):
    app = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": str(tmp_path / "e.db")}
    )
    try:
        learner = Learner()
        for index in range(3):
            learner.complete_lesson(f"c{index}", "l1", activity_date=f"2026-09-0{index + 1}")
        app.save(learner)
        updates = [event for event in _stored(app, learner) if isinstance(event, Learner.StreakUpdated)]
        assert updates[-1].new_streak == 3
        assert sum(isinstance(event, Learner.XPAwarded) for event in _stored(app, learner)) == 3
    finally:
        app.close()


def test_t3_freeze_covers_one_day_gap(tmp_path):
    app = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": str(tmp_path / "e.db")}
    )
    try:
        learner = Learner()
        for day in ("01", "02", "03"):
            learner.complete_lesson(f"c{day}", "l1", activity_date=f"2026-09-{day}")
        learner.acquire_freeze("f1", activity_date="2026-09-03")
        learner.complete_lesson("c5", "l1", activity_date="2026-09-05")
        app.save(learner)
        event = [event for event in _stored(app, learner) if isinstance(event, Learner.StreakUpdated)][-1]
        assert (event.new_streak, event.freezes_consumed, event.freezes_remaining) == (4, 1, 0)
        assert sum(isinstance(item, Learner.XPAwarded) for item in _stored(app, learner)) == 4
    finally:
        app.close()


def test_t4_break_records_previous_streak(tmp_path):
    app = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": str(tmp_path / "e.db")}
    )
    try:
        learner = Learner()
        for day in ("01", "02", "03"):
            learner.complete_lesson(f"c{day}", "l1", activity_date=f"2026-09-{day}")
        learner.complete_lesson("c6", "l1", activity_date="2026-09-06")
        app.save(learner)
        assert learner.streak_before_break == 3
        assert app.repository.get(learner.id).streak_before_break == 3
        event = [event for event in _stored(app, learner) if isinstance(event, Learner.StreakUpdated)][-1]
        assert (event.previous_streak, event.new_streak) == (3, 1)
    finally:
        app.close()


def test_t5_late_event_corrects_later_date_without_reward(tmp_path):
    app = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": str(tmp_path / "e.db")}
    )
    try:
        learner = Learner()
        learner.acquire_freeze("f1", activity_date="2026-09-01")
        learner.complete_lesson("c1", "l1", activity_date="2026-09-01")
        learner.complete_lesson("c3", "l1", activity_date="2026-09-03")
        app.save(learner)
        reloaded = app.repository.get(learner.id)
        reloaded.complete_lesson("c2", "l1", activity_date="2026-09-02")
        app.save(reloaded)
        events = _stored(app, learner)
        updates = [event for event in events if isinstance(event, Learner.StreakUpdated)]
        d2 = next(event for event in updates if event.activity_date == "2026-09-02")
        d3 = [event for event in updates if event.activity_date == "2026-09-03"][-1]
        # D2 revision 1, D3 correction revision 2 supersedes its first fact.
        assert d2.revision == 1
        assert (d3.revision, d3.supersedes) == (2, f"streak:{learner.id}:2026-09-03:1")
        assert rebuild_learner_state(events).new_streak == 3
        assert rebuild_learner_state(events).freezes_remaining == 1
        assert sum(isinstance(event, Learner.XPAwarded) for event in events) == 3
        assert sum(event.xp_amount for event in events if isinstance(event, Learner.XPAwarded)) == reloaded.xp_total
    finally:
        app.close()


def test_t6_freeze_acquired_after_gap_cannot_cover_it():
    learner = Learner()
    learner.acquire_freeze("f1", activity_date="2026-09-04")
    learner.complete_lesson("c1", "l1", activity_date="2026-09-01")
    learner.complete_lesson("c3", "l1", activity_date="2026-09-03")
    assert learner.new_streak == 1
    assert learner.freezes_remaining == 1


def test_t7_rebuild_does_not_decide_or_append_events(tmp_path, monkeypatch):
    app = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": str(tmp_path / "e.db")}
    )
    try:
        learner = Learner()
        learner.acquire_freeze("f1", activity_date="2026-09-01")
        learner.complete_lesson("c1", "l1", activity_date="2026-09-01")
        learner.complete_lesson("c3", "l1", activity_date="2026-09-03")
        app.save(learner)
        reloaded = app.repository.get(learner.id)
        reloaded.complete_lesson("c2", "l1", activity_date="2026-09-02")
        app.save(reloaded)
        events = _stored(app, learner)
        count = len(events)

        def boom(*args, **kwargs):
            raise AssertionError("decision function called during rebuild")

        monkeypatch.setattr(rules, "calculate_streak", boom)
        monkeypatch.setattr(rules, "xp_amount_for", boom)
        monkeypatch.setattr(rules, "quest_target_for", boom)
        monkeypatch.setattr(rules, "badge_qualifies", boom)
        rebuilt = rebuild_learner_state(events)
        assert (rebuilt.new_streak, rebuilt.freezes_remaining, rebuilt.xp_total) == (3, 1, 300)
        assert len(_stored(app, learner)) == count
    finally:
        app.close()
