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
