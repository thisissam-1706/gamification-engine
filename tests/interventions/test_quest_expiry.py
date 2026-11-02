"""M4 step 4: a sweep that expires overdue quests without needing another completion.

Written before the implementation. Assumed new API (see the Week 6 M4 step 4 audit):
``Learner.expire_due_quests(as_of) -> list[str]`` and
``src.interventions.service.run_quest_expiry(app, learner_id, *, clock) -> list[str]``.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from src.state_machine.learner import Learner
from tests.interventions.test_checkpoint_attempt_ingestion import (
    _learner,
    _new_learner,
    _open,
)

ASSIGNED = datetime(2026, 9, 1, tzinfo=timezone.utc)
DEADLINE = ASSIGNED + timedelta(days=7)
AFTER = DEADLINE + timedelta(days=3)


def _assign(learner, quest_id="remediation_v1", assignment_id="remediation_quest:att-1",
            version="remediation_quests_v1", deadline=DEADLINE):
    assert learner.assign_quest(
        quest_id,
        assignment_id=assignment_id,
        assigned_at=ASSIGNED,
        deadline_at=deadline,
        definition_version=version,
    )


def _remediation_learner() -> Learner:
    learner = Learner()
    _assign(learner)
    learner.collect_events()
    return learner


def _expiry_events(events) -> list:
    return [e for e in events if type(e).__name__ == "QuestExpired"]


def _event_count(application, learner_id) -> int:
    return len(list(application.events.get(UUID(learner_id))))


# --- Learner.expire_due_quests ------------------------------------------------


def test_sweep_expires_an_overdue_quest_with_expired_at_equal_to_the_deadline():
    learner = _remediation_learner()

    expired = learner.expire_due_quests(AFTER)

    assert expired == ["remediation_quest:att-1"]
    assert learner.quest_instances["remediation_quest:att-1"]["status"] == "expired"
    events = _expiry_events(learner.collect_events())
    assert len(events) == 1
    assert events[0].assignment_id == "remediation_quest:att-1"
    assert events[0].quest_id == "remediation_v1"
    assert events[0].expired_at == DEADLINE.isoformat()  # the deadline, not as_of


def test_sweep_at_exactly_the_deadline_expires_nothing():
    learner = _remediation_learner()

    assert learner.expire_due_quests(DEADLINE) == []

    assert learner.collect_events() == []
    assert learner.quest_instances["remediation_quest:att-1"]["status"] == "assigned"


def test_sweep_one_microsecond_after_the_deadline_expires():
    learner = _remediation_learner()
    assert learner.expire_due_quests(DEADLINE + timedelta(microseconds=1)) == [
        "remediation_quest:att-1"
    ]


def test_second_sweep_returns_empty_and_emits_nothing():
    learner = _remediation_learner()
    learner.expire_due_quests(AFTER)
    learner.collect_events()

    assert learner.expire_due_quests(AFTER + timedelta(days=30)) == []

    assert learner.collect_events() == []


def test_completed_quest_is_untouched():
    learner = Learner()
    _assign(learner)
    learner.complete_lesson(
        "c-rem",
        "lesson-1",
        occurred_at=ASSIGNED + timedelta(days=1),
        timezone="UTC",
        completion_kind="remediation",
    )
    assert learner.quest_instances["remediation_quest:att-1"]["status"] == "completed"
    learner.collect_events()

    assert learner.expire_due_quests(AFTER) == []

    assert learner.collect_events() == []
    assert learner.quest_instances["remediation_quest:att-1"]["status"] == "completed"


def test_already_expired_quest_is_untouched():
    learner = Learner()
    _assign(learner)
    learner.complete_lesson(  # lazy expiry: a completion after the deadline expires it
        "c-late", "lesson-1", occurred_at=AFTER, timezone="UTC", completion_kind="remediation"
    )
    assert learner.quest_instances["remediation_quest:att-1"]["status"] == "expired"
    learner.collect_events()

    assert learner.expire_due_quests(AFTER + timedelta(days=30)) == []

    assert learner.collect_events() == []


def test_sweep_covers_rule_set_quests_too():
    learner = Learner()
    _assign(learner, "lesson_starter_v1", "starter-1", "v1")
    learner.collect_events()

    assert learner.expire_due_quests(AFTER) == ["starter-1"]

    assert learner.quest_instances["starter-1"]["status"] == "expired"
    assert _expiry_events(learner.collect_events())[0].quest_id == "lesson_starter_v1"


def test_only_the_due_quest_is_expired():
    learner = Learner()
    _assign(learner, assignment_id="due", deadline=DEADLINE)
    _assign(learner, "lesson_starter_v1", "not-due", "v1", deadline=DEADLINE + timedelta(days=30))
    learner.collect_events()

    assert learner.expire_due_quests(AFTER) == ["due"]

    assert learner.quest_instances["due"]["status"] == "expired"
    assert learner.quest_instances["not-due"]["status"] == "assigned"
    assert len(_expiry_events(learner.collect_events())) == 1


def test_expired_assignment_ids_are_returned_sorted_and_each_uses_its_own_deadline():
    learner = Learner()
    _assign(learner, assignment_id="b", deadline=DEADLINE + timedelta(days=1))
    _assign(learner, "lesson_starter_v1", "a", "v1", deadline=DEADLINE)
    learner.collect_events()

    assert learner.expire_due_quests(AFTER) == ["a", "b"]

    by_assignment = {e.assignment_id: e.expired_at for e in _expiry_events(learner.collect_events())}
    assert by_assignment == {
        "a": DEADLINE.isoformat(),
        "b": (DEADLINE + timedelta(days=1)).isoformat(),
    }


def test_naive_as_of_raises_value_error_and_emits_nothing():
    learner = _remediation_learner()

    with pytest.raises(ValueError):
        learner.expire_due_quests(datetime(2026, 9, 20))

    assert learner.collect_events() == []
    assert learner.quest_instances["remediation_quest:att-1"]["status"] == "assigned"


def test_swept_quest_is_not_progressed_or_completed_by_a_later_remediation_completion():
    learner = _remediation_learner()
    learner.expire_due_quests(AFTER)
    learner.collect_events()

    # Dated inside the original window, so only the expired status can stop it.
    learner.complete_lesson(
        "c-late-arrival",
        "lesson-1",
        occurred_at=ASSIGNED + timedelta(days=1),
        timezone="UTC",
        completion_kind="remediation",
    )
    names = [type(e).__name__ for e in learner.collect_events()]

    assert "QuestProgressed" not in names
    assert "QuestCompleted" not in names
    instance = learner.quest_instances["remediation_quest:att-1"]
    assert instance["status"] == "expired"
    assert instance["progress"] == 0


# --- run_quest_expiry ---------------------------------------------------------


def _run_expiry(application, learner_id, now):
    # Imported here so a missing feature fails each test with a clear ImportError.
    from src.interventions.service import run_quest_expiry

    return run_quest_expiry(application, learner_id, clock=lambda: now)


def _learner_with_saved_quest(application) -> str:
    learner_id = _new_learner(application)
    learner = _learner(application, learner_id)
    _assign(learner)
    application.save(learner)
    return learner_id


def test_run_quest_expiry_expires_saves_and_returns_the_ids(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _learner_with_saved_quest(application)
        before = _event_count(application, learner_id)

        expired = _run_expiry(application, learner_id, AFTER)

        assert expired == ["remediation_quest:att-1"]
        assert _event_count(application, learner_id) == before + 1
        stored = _learner(application, learner_id).quest_instances["remediation_quest:att-1"]
        assert stored["status"] == "expired"
    finally:
        evidence.close()
        application.close()


def test_run_quest_expiry_with_nothing_due_saves_nothing(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _learner_with_saved_quest(application)
        before = _event_count(application, learner_id)

        assert _run_expiry(application, learner_id, DEADLINE) == []

        assert _event_count(application, learner_id) == before
    finally:
        evidence.close()
        application.close()


def test_run_quest_expiry_twice_is_idempotent(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _learner_with_saved_quest(application)
        _run_expiry(application, learner_id, AFTER)
        after_first = _event_count(application, learner_id)

        assert _run_expiry(application, learner_id, AFTER + timedelta(days=5)) == []

        assert _event_count(application, learner_id) == after_first
    finally:
        evidence.close()
        application.close()


def test_run_quest_expiry_with_a_naive_clock_raises_and_saves_nothing(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _learner_with_saved_quest(application)
        before = _event_count(application, learner_id)

        with pytest.raises(ValueError):
            _run_expiry(application, learner_id, datetime(2026, 9, 20))

        assert _event_count(application, learner_id) == before
        stored = _learner(application, learner_id).quest_instances["remediation_quest:att-1"]
        assert stored["status"] == "assigned"
    finally:
        evidence.close()
        application.close()


def test_expiry_survives_replay_in_a_fresh_application(tmp_path):
    db = str(tmp_path / "events.db")
    application, evidence = _open(db)
    try:
        learner_id = _learner_with_saved_quest(application)
        _run_expiry(application, learner_id, AFTER)
        expected = dict(_learner(application, learner_id).quest_instances)
    finally:
        evidence.close()
        application.close()

    fresh, fresh_evidence = _open(db)
    try:
        assert _learner(fresh, learner_id).quest_instances == expected
        assert expected["remediation_quest:att-1"]["status"] == "expired"
    finally:
        fresh_evidence.close()
        fresh.close()


# --- lazy expiry records the same value as the sweep ---------------------------


def test_lazy_expiry_records_the_deadline_as_expired_at():
    learner = _remediation_learner()

    learner.complete_lesson(
        "c-late",
        "lesson-1",
        occurred_at=DEADLINE + timedelta(hours=1),
        timezone="UTC",
        completion_kind="remediation",
    )

    events = _expiry_events(learner.collect_events())
    assert len(events) == 1
    assert events[0].expired_at == DEADLINE.isoformat()  # not the completion's occurred_at
    assert learner.quest_instances["remediation_quest:att-1"]["status"] == "expired"


# --- edge cases: readers, replay, full M4 path -------------------------------


def _events(application, learner_id) -> list:
    return list(application.events.get(UUID(learner_id)))


def test_quest_readers_agree_with_the_aggregate_after_a_sweep(tmp_path):
    from src.state_machine.projection import (
        LearnerQuestProjection,
        SQLiteLearnerProjection,
        rebuild_learner_state,
    )

    db = str(tmp_path / "events.db")
    application, evidence = _open(db)
    sqlite_projection = SQLiteLearnerProjection(str(tmp_path / "projection.db"))
    try:
        learner_id = _learner_with_saved_quest(application)
        before_events = _events(application, learner_id)
        replay_before = rebuild_learner_state(before_events)
        sqlite_projection.rebuild(learner_id, before_events)
        sqlite_before = sqlite_projection.state(learner_id)

        _run_expiry(application, learner_id, AFTER)

        events = _events(application, learner_id)
        aggregate = _learner(application, learner_id).quest_instances["remediation_quest:att-1"]
        expiry = _expiry_events(events)[0]

        # The aggregate and the quest read model agree on status and the deadline.
        quests = LearnerQuestProjection()
        for event in events:
            quests.apply(event)
        record = quests.get_summary(UUID(learner_id)).assignments["remediation_quest:att-1"]
        assert aggregate["status"] == record.status == "expired"
        assert record.deadline_at == aggregate["deadline_at"] == DEADLINE
        # expired_at lives only on the event, and equals the deadline both readers hold.
        assert expiry.expired_at == record.deadline_at.isoformat()

        # Replay state ignores quest expiry, so both replay readers are unchanged by the sweep.
        assert rebuild_learner_state(events) == replay_before
        sqlite_projection.rebuild(learner_id, events)
        assert sqlite_projection.state(learner_id) == sqlite_before
        assert sqlite_before["completed_quests"] == []
    finally:
        sqlite_projection.close()
        evidence.close()
        application.close()


def test_quest_reader_reports_expired_with_or_without_as_of(tmp_path):
    from src.state_machine.projection import LearnerQuestProjection

    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _learner_with_saved_quest(application)
        quests = LearnerQuestProjection()
        for event in _events(application, learner_id):
            quests.apply(event)
        assert quests.get_summary(UUID(learner_id)).assignments[
            "remediation_quest:att-1"
        ].status == "assigned"
        # Before any sweep the read model already reports expiry when given as_of.
        assert quests.get_summary(UUID(learner_id), AFTER).assignments[
            "remediation_quest:att-1"
        ].status == "expired"

        _run_expiry(application, learner_id, AFTER)
        quests = LearnerQuestProjection()
        for event in _events(application, learner_id):
            quests.apply(event)
        assert quests.get_summary(UUID(learner_id)).assignments[
            "remediation_quest:att-1"
        ].status == "expired"
    finally:
        evidence.close()
        application.close()


def test_replay_after_a_sweep_has_identical_quest_instances_with_other_quests(tmp_path):
    db = str(tmp_path / "events.db")
    application, evidence = _open(db)
    try:
        learner_id = _new_learner(application)
        learner = _learner(application, learner_id)
        _assign(learner, assignment_id="due", deadline=DEADLINE)
        _assign(learner, "lesson_starter_v1", "later", "v1", deadline=DEADLINE + timedelta(days=30))
        application.save(learner)
        assert _run_expiry(application, learner_id, AFTER) == ["due"]
        expected = dict(_learner(application, learner_id).quest_instances)
    finally:
        evidence.close()
        application.close()

    fresh, fresh_evidence = _open(db)
    try:
        replayed = _learner(fresh, learner_id).quest_instances
        assert replayed == expected
        assert replayed["due"]["status"] == "expired"
        assert replayed["later"]["status"] == "assigned"
    finally:
        fresh_evidence.close()
        fresh.close()


def _m4_path(tmp_path, second_attempt_after: timedelta):
    from tests.interventions.test_remediation_service import NOW, _ingest, _run

    application, evidence = _open(str(tmp_path / "events.db"))
    learner_id = _new_learner(application)
    _ingest(application, evidence, learner_id, "att-1", assessment_id="math")
    _ingest(application, evidence, learner_id, "att-2", assessment_id="physics")
    first = _run(application, learner_id, "att-1", NOW)
    expired = _run_expiry(application, learner_id, first.deadline_at + timedelta(seconds=1))
    second = _run(application, learner_id, "att-2", NOW + second_attempt_after)
    return application, evidence, learner_id, first, expired, second


def test_full_m4_path_expiry_does_not_reset_the_14_day_window(tmp_path):
    application, evidence, learner_id, first, expired, second = _m4_path(
        tmp_path, timedelta(days=10)
    )
    try:
        assert first.action == "assign"
        assert expired == ["remediation_quest:att-1"]
        learner = _learner(application, learner_id)
        assert learner.quest_instances["remediation_quest:att-1"]["status"] == "expired"
        assert (second.action, second.reason) == ("no_action", "remediated_within_window")
        assert set(learner.quest_instances) == {"remediation_quest:att-1"}
    finally:
        evidence.close()
        application.close()


def test_full_m4_path_assigns_again_after_14_days(tmp_path):
    application, evidence, learner_id, first, expired, second = _m4_path(
        tmp_path, timedelta(days=14)
    )
    try:
        assert expired == ["remediation_quest:att-1"]
        assert second.action == "assign"
        learner = _learner(application, learner_id)
        assert learner.quest_instances["remediation_quest:att-1"]["status"] == "expired"
        new = learner.quest_instances["remediation_quest:att-2"]
        assert new["status"] == "assigned"
        assert new["assigned_at"] == second.decided_at
        assert new["deadline_at"] == second.decided_at + timedelta(days=7)
    finally:
        evidence.close()
        application.close()
