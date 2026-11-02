"""M4 step 3: an "assign" remediation decision creates a real quest.

Written before the implementation. Assumed new behaviour (see the Week 6 M4 step 3 audit):
``run_remediation`` also calls ``learner.assign_quest(config.quest_id,
assignment_id=f"remediation_quest:{attempt_id}", assigned_at=decision.decided_at,
deadline_at=decision.deadline_at, definition_version=config.quest_definition_version)``
in the same save, and ``RemediationConfig.quest_definition_version`` defaults to
"remediation_quests_v1".
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from src.ingestion.validation import DEFAULT_INGEST_RULE_VERSION, expected_dedup_key
from src.interventions.config import RemediationConfig
from tests.ingestion.support import ingest_lesson_completed
from tests.interventions.test_remediation_service import (
    NOW,
    _ingest,
    _remediation_events,
    _run,
)
from tests.interventions.test_checkpoint_attempt_ingestion import (
    _learner,
    _new_learner,
    _open,
)

QUEST = "remediation_quest:att-1"


def _events_named(application, learner_id, name) -> list:
    return [
        event
        for event in application.events.get(UUID(learner_id))
        if type(event).__name__ == name
    ]


def _lesson_envelope(learner_id, completion_id, kind, occurred_at):
    event_id = str(uuid4())
    payload = {
        "completion_id": completion_id,
        "lesson_id": "lesson-1",
        "timezone": "Asia/Kolkata",
    }
    if kind is not None:
        payload["completion_kind"] = kind
    return {
        "event_id": event_id,
        "event_type": "learning.lesson_completed",
        "schema_version": 1,
        "occurred_at": occurred_at.isoformat(),
        "ingested_at": (occurred_at + timedelta(minutes=1)).isoformat(),
        "subject_id": learner_id,
        "producer": "learning-svc",
        "correlation_id": str(uuid4()),
        "causation_id": None,
        "dedup_key": expected_dedup_key(event_id, DEFAULT_INGEST_RULE_VERSION),
        "payload": payload,
        "provenance": {"source": "client"},
    }


def _complete_lesson(application, evidence, learner_id, kind, occurred_at):
    result = ingest_lesson_completed(
        application,
        evidence,
        _lesson_envelope(learner_id, f"c-{kind}", kind, occurred_at),
        authenticated_producer="learning-svc",
    )
    assert result.status == "accepted"


def test_config_has_quest_definition_version():
    assert RemediationConfig().quest_definition_version == "remediation_quests_v1"


def test_assign_decision_creates_the_quest_in_one_save(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        _ingest(application, evidence, learner_id, "att-1")

        saves = []
        original_save = application.save
        application.save = lambda *aggregates, **kw: (
            saves.append(aggregates),
            original_save(*aggregates, **kw),
        )[1]
        decision = _run(application, learner_id, "att-1", NOW)
        application.save = original_save

        assert decision.action == "assign"
        instance = _learner(application, learner_id).quest_instances[QUEST]
        assert instance["quest_id"] == "remediation_v1"
        assert instance["definition_version"] == "remediation_quests_v1"
        assert instance["status"] == "assigned"
        assert instance["assigned_at"] == decision.decided_at
        assert instance["deadline_at"] == decision.decided_at + timedelta(days=7)

        decided = _events_named(application, learner_id, "RemediationDecided")
        assigned = _events_named(application, learner_id, "QuestAssigned")
        assert len(decided) == 1
        assert len(assigned) == 1
        # One save, and the two events are consecutive versions of the same aggregate.
        assert len(saves) == 1
        assert assigned[0].originator_version == decided[0].originator_version + 1
    finally:
        evidence.close()
        application.close()


def test_passed_attempt_creates_no_quest(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        _ingest(application, evidence, learner_id, "att-1", passed=True)

        decision = _run(application, learner_id, "att-1", NOW)

        assert decision.action == "no_action"
        learner = _learner(application, learner_id)
        assert learner.quest_instances == {}
        assert learner.assigned_quest_ids == set()
        assert _events_named(application, learner_id, "QuestAssigned") == []
        assert len(_remediation_events(application, learner_id)) == 1
    finally:
        evidence.close()
        application.close()


def test_window_blocked_decision_creates_no_second_quest(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        _ingest(application, evidence, learner_id, "att-1", assessment_id="math")
        _ingest(application, evidence, learner_id, "att-2", assessment_id="physics")

        _run(application, learner_id, "att-1", NOW)
        second = _run(application, learner_id, "att-2", NOW + timedelta(days=3))

        assert second.action == "no_action"
        assert set(_learner(application, learner_id).quest_instances) == {QUEST}
        assert len(_events_named(application, learner_id, "QuestAssigned")) == 1
    finally:
        evidence.close()
        application.close()


def test_rerun_30_days_later_assigns_nothing_new(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        _ingest(application, evidence, learner_id, "att-1")
        _run(application, learner_id, "att-1", NOW)

        _run(application, learner_id, "att-1", NOW + timedelta(days=30))

        assert len(_events_named(application, learner_id, "QuestAssigned")) == 1
        assert set(_learner(application, learner_id).quest_instances) == {QUEST}
    finally:
        evidence.close()
        application.close()


def test_full_path_remediation_completion_completes_the_quest(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        _ingest(application, evidence, learner_id, "att-1")
        _run(application, learner_id, "att-1", NOW)

        _complete_lesson(
            application, evidence, learner_id, "remediation", NOW + timedelta(days=2)
        )

        instance = _learner(application, learner_id).quest_instances[QUEST]
        assert instance["status"] == "completed"
        assert len(_events_named(application, learner_id, "QuestCompleted")) == 1
    finally:
        evidence.close()
        application.close()


def test_full_path_lesson_completion_leaves_the_quest_assigned(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        _ingest(application, evidence, learner_id, "att-1")
        _run(application, learner_id, "att-1", NOW)

        _complete_lesson(
            application, evidence, learner_id, "lesson", NOW + timedelta(days=2)
        )

        instance = _learner(application, learner_id).quest_instances[QUEST]
        assert instance["status"] == "assigned"
        assert instance["progress"] == 0
        assert _events_named(application, learner_id, "QuestCompleted") == []
    finally:
        evidence.close()
        application.close()


def test_replay_in_a_fresh_application_keeps_quest_and_decisions(tmp_path):
    db = str(tmp_path / "events.db")
    application, evidence = _open(db)
    try:
        learner_id = _new_learner(application)
        _ingest(application, evidence, learner_id, "att-1", assessment_id="math")
        _ingest(application, evidence, learner_id, "att-2", assessment_id="physics")
        _ingest(application, evidence, learner_id, "att-3", assessment_id="chem", passed=True)
        _run(application, learner_id, "att-1", NOW)
        _run(application, learner_id, "att-2", NOW + timedelta(days=1))
        _run(application, learner_id, "att-3", NOW + timedelta(days=2))
        _complete_lesson(
            application, evidence, learner_id, "remediation", NOW + timedelta(days=3)
        )
        learner = _learner(application, learner_id)
        quests = dict(learner.quest_instances)
        decisions = dict(learner.remediation_decisions)
        assert set(quests) == {QUEST}
        assert quests[QUEST]["status"] == "completed"
    finally:
        evidence.close()
        application.close()

    fresh, fresh_evidence = _open(db)
    try:
        replayed = _learner(fresh, learner_id)
        assert replayed.quest_instances == quests
        assert replayed.remediation_decisions == decisions
    finally:
        fresh_evidence.close()
        fresh.close()


def test_failing_quest_assignment_saves_nothing(tmp_path):
    import pytest

    from src.interventions.service import run_remediation

    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        _ingest(application, evidence, learner_id, "att-1")
        events_before = len(list(application.events.get(UUID(learner_id))))

        with pytest.raises(KeyError):
            run_remediation(
                application,
                learner_id,
                "att-1",
                clock=lambda: NOW,
                config=RemediationConfig(quest_id="no_such_quest"),
            )

        learner = _learner(application, learner_id)
        assert learner.remediation_decisions == {}
        assert learner.quest_instances == {}
        assert len(list(application.events.get(UUID(learner_id)))) == events_before
        assert _events_named(application, learner_id, "RemediationDecided") == []
    finally:
        evidence.close()
        application.close()


def test_other_learners_remediation_completion_does_not_touch_this_learners_quest(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        first_id = _new_learner(application)
        second_id = _new_learner(application)
        _ingest(application, evidence, first_id, "att-1")
        _run(application, first_id, "att-1", NOW)

        _complete_lesson(
            application, evidence, second_id, "remediation", NOW + timedelta(days=1)
        )

        first = _learner(application, first_id)
        assert first.quest_instances[QUEST]["status"] == "assigned"
        assert first.quest_instances[QUEST]["progress"] == 0
        second = _learner(application, second_id)
        assert second.quest_instances == {}
        assert second.xp_total > 0
        assert _events_named(application, first_id, "QuestCompleted") == []
    finally:
        evidence.close()
        application.close()
