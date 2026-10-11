"""SQLite staging and atomic projection replacement tests."""

import pytest

from src.state_machine.learner import Learner, LearnerApplication
from src.state_machine.projection import SQLiteLearnerProjection


def test_atomic_rebuild_is_repeatable_and_preserves_live_state_on_failure(tmp_path):
    db = str(tmp_path / "events.db")
    app = LearnerApplication(env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db})
    projection = SQLiteLearnerProjection(db)
    try:
        learner = Learner()
        learner.complete_lesson("c1", "l1", activity_date="2026-09-01")
        learner.complete_lesson("c3", "l1", activity_date="2026-09-03")
        app.save(learner)
        events = list(app.events.get(learner.id))
        first = projection.rebuild(str(learner.id), events)
        state_before_failure = projection.state(str(learner.id))
        assert state_before_failure is not None
        with pytest.raises(RuntimeError, match="injected staging failure"):
            projection.rebuild(str(learner.id), events, fail_after=2)
        assert projection.state(str(learner.id)) == state_before_failure
        second = projection.rebuild(str(learner.id), events)
        assert second == first
        assert projection.state(str(learner.id)) == state_before_failure
        assert len(list(app.events.get(learner.id))) == len(events)
    finally:
        projection.close()
        app.close()


def test_rebuilding_a_twice_keeps_b_projection(tmp_path):
    db = str(tmp_path / "events.db")
    app = LearnerApplication(env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db})
    projection = SQLiteLearnerProjection(db)
    try:
        learner_a = Learner()
        learner_a.complete_lesson("a1", "l1", activity_date="2026-09-01")
        app.save(learner_a)
        events_a_first = list(app.events.get(learner_a.id))

        learner_b = Learner()
        learner_b.complete_lesson("b1", "l1", activity_date="2026-09-02")
        app.save(learner_b)
        events_b = list(app.events.get(learner_b.id))

        projection.rebuild(str(learner_a.id), events_a_first)
        projection.rebuild(str(learner_b.id), events_b)
        state_b = projection.state(str(learner_b.id))

        learner_a.complete_lesson("a2", "l2", activity_date="2026-09-03")
        app.save(learner_a)
        events_a_second = list(app.events.get(learner_a.id))
        projection.rebuild(str(learner_a.id), events_a_second)

        assert projection.state(str(learner_b.id)) == state_b
    finally:
        projection.close()
        app.close()
