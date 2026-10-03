"""SQLite event-sourcing spike: save and replay one learner aggregate."""

from pathlib import Path

from src.state_machine.learner import Learner, LearnerApplication


def sqlite_environment(database_path: Path) -> dict[str, str]:
    """Returns the eventsourcing configuration for an isolated SQLite database."""
    return {
        "PERSISTENCE_MODULE": "eventsourcing.sqlite",
        "SQLITE_DBNAME": str(database_path),
    }


def assert_recorded_completion(learner: Learner) -> None:
    """Checks state folded from one completion and its XP reward."""
    assert learner.completed_lesson_count == 1
    assert learner.completed_lesson_ids == {"cmp_492_003"}
    assert learner.lesson_completion_counts == {"l_492": 1}
    assert learner.xp_total == 100
    assert learner.awarded_reward_ids == {"xp:cmp_492_003"}
    assert learner.applied_rule_versions == {"xp:cmp_492_003": "v1"}


def test_learner_state_survives_a_sqlite_application_restart(tmp_path: Path):
    """A fresh application reconstructs learner state from the durable event log."""
    environment = sqlite_environment(tmp_path / "events.db")

    application = LearnerApplication(env=environment)
    try:
        learner = Learner()
        assert learner.complete_lesson("cmp_492_003", "l_492") is True
        application.save(learner)
        learner_id = learner.id

        assert_recorded_completion(application.repository.get(learner_id))
    finally:
        application.close()

    restarted_application = LearnerApplication(env=environment)
    try:
        assert_recorded_completion(restarted_application.repository.get(learner_id))
    finally:
        restarted_application.close()


def test_completion_and_xp_reward_are_persisted_as_two_facts(tmp_path: Path):
    """One fixed completion yields exactly source and reward domain events."""
    environment = sqlite_environment(tmp_path / "events.db")
    application = LearnerApplication(env=environment)
    try:
        learner = Learner()
        assert learner.complete_lesson(
            "cmp_fixed_001", "l_fixed", xp_amount=25, rule_version="xp-v1"
        ) is True
        application.save(learner)

        stored_events = list(application.events.get(learner.id))
        business_events = [
            domain_event
            for domain_event in stored_events
            if isinstance(domain_event, (Learner.LessonCompleted, Learner.XPAwarded))
        ]
        # ``Created`` is framework lifecycle metadata. The accepted action itself
        # emits exactly these two business facts in the same save operation.
        assert len(business_events) == 2
        assert isinstance(business_events[0], Learner.LessonCompleted)
        assert isinstance(business_events[1], Learner.XPAwarded)

        persisted = application.repository.get(learner.id)
        assert persisted.completed_lesson_count == 1
        assert persisted.lesson_completion_counts == {"l_fixed": 1}
        assert persisted.xp_total == 25
        assert persisted.awarded_reward_ids == {"xp:cmp_fixed_001"}
        assert persisted.applied_rule_versions == {"xp:cmp_fixed_001": "xp-v1"}
    finally:
        application.close()
