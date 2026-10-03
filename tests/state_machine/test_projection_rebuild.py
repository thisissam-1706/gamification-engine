"""Projection rebuild test for the SQLite event-sourcing foundation."""

from src.state_machine.learner import Learner, LearnerApplication
from src.state_machine.projection import LearnerCompletionProjection


def test_completion_projection_rebuilds_after_a_wipe(tmp_path):
    """The persisted event stream reconstructs the same derived learner count."""
    application = LearnerApplication(
        env={
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(tmp_path / "events.db"),
        }
    )
    try:
        learner = Learner()
        learner.complete_lesson("cmp_492_001", "l_492")
        learner.complete_lesson("cmp_492_002", "l_492")
        learner.complete_lesson("cmp_493_001", "l_493")
        application.save(learner)

        projection = LearnerCompletionProjection()
        stored_event_count = len(list(application.events.get(learner.id)))
        projection.rebuild(application.events.get(learner.id))
        state_before_wipe = dict(projection.completed_lesson_counts)

        projection.clear()
        assert projection.completed_lesson_counts == {}

        projection.rebuild(application.events.get(learner.id))
        assert projection.completed_lesson_counts == state_before_wipe
        assert projection.completed_lesson_counts[learner.id] == 3
        assert len(list(application.events.get(learner.id))) == stored_event_count
    finally:
        application.close()
