"""AT-17: Learning-oriented remediation (M4)

Go through the real paths only: ``ingest_checkpoint_attempted`` and ``ingest_lesson_completed``
(ingestion with a fixed trusted clock), then ``run_remediation`` and ``run_quest_expiry`` with fixed
clocks.

  a. A learner's first checkpoint attempt on an assessment fails and there has been no remediation
     "assign" in the last 14 days: an "assign" decision is stored and a ``remediation_v1`` quest is
     assigned with deadline = decision time + 7 days.
  b. The learner had an "assign" decision less than 14 days earlier and a new failed first attempt on
     another assessment arrives: an explicit "no_action" decision (reason ``remediated_within_window``)
     is stored, and no new quest is created.
  c. The assigned quest is not completed: ``run_quest_expiry`` after the deadline marks it "expired"
     with ``expired_at`` equal to the deadline, and a remediation completion after that does not
     complete it.

Note: the wording of AT-17 is not in docs/traceability-matrix.md (the row says "to be written");
the three scenarios above follow the M4 brief.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from src.ingestion.config import IngestConfig
from src.ingestion.service import ingest_checkpoint_attempted, ingest_lesson_completed
from src.ingestion.validation import DEFAULT_INGEST_RULE_VERSION, expected_dedup_key
from src.interventions.service import run_quest_expiry, run_remediation
from tests.interventions.test_checkpoint_attempt_ingestion import (
    _checkpoint_envelope,
    _learner,
    _new_learner,
    _open,
)

T0 = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)
QUEST_ID = "remediation_v1"


def _config(now: datetime) -> IngestConfig:
    """Default 72 h late window, with the trusted receive clock fixed at ``now``."""
    return IngestConfig(DEFAULT_INGEST_RULE_VERSION, clock=lambda: now)


def _attempt_failed(application, evidence, learner_id, attempt_id, assessment_id, now):
    envelope = _checkpoint_envelope(
        learner_id,
        attempt_id,
        occurred_at=now - timedelta(hours=1),
        assessment_id=assessment_id,
    )
    assert envelope["payload"]["passed"] is False
    result = ingest_checkpoint_attempted(
        application,
        evidence,
        envelope,
        authenticated_producer="learning-svc",
        config=_config(now),
    )
    assert (result.status, result.reason) == ("accepted", None)


def _remediation_completion(application, evidence, learner_id, completion_id, occurred_at, now):
    event_id = str(uuid4())
    envelope = {
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
        "payload": {
            "completion_id": completion_id,
            "lesson_id": "remediation-lesson-1",
            "timezone": "Asia/Kolkata",
            "completion_kind": "remediation",
        },
        "provenance": {"source": "client"},
    }
    result = ingest_lesson_completed(
        application,
        evidence,
        envelope,
        authenticated_producer="learning-svc",
        config=_config(now),
    )
    assert result.status == "accepted"


def _stream(application, learner_id, name):
    return [
        event
        for event in application.events.get(UUID(learner_id))
        if type(event).__name__ == name
    ]


def test_at17_failed_first_attempt_with_no_recent_assign_stores_assign_and_quest(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        _attempt_failed(application, evidence, learner_id, "att-1", "math", T0)

        decision = run_remediation(application, learner_id, "att-1", clock=lambda: T0)

        assert decision.action == "assign"
        assert decision.reason is None
        stored = _learner(application, learner_id).remediation_decisions["remediation:att-1"]
        assert stored["action"] == "assign"
        assert stored["decided_at"] == T0.isoformat()
        quest = _learner(application, learner_id).quest_instances["remediation_quest:att-1"]
        assert quest["quest_id"] == QUEST_ID
        assert quest["status"] == "assigned"
        assert quest["assigned_at"] == T0
        assert quest["deadline_at"] == T0 + timedelta(days=7)
        assert len(_stream(application, learner_id, "RemediationDecided")) == 1
        assert len(_stream(application, learner_id, "QuestAssigned")) == 1
    finally:
        evidence.close()
        application.close()


def test_at17_failed_attempt_within_14_days_of_an_assign_stores_an_explicit_no_action(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        _attempt_failed(application, evidence, learner_id, "att-1", "math", T0)
        first = run_remediation(application, learner_id, "att-1", clock=lambda: T0)
        assert first.action == "assign"

        later = T0 + timedelta(days=5)
        _attempt_failed(application, evidence, learner_id, "att-2", "physics", later)
        second = run_remediation(application, learner_id, "att-2", clock=lambda: later)

        assert (second.action, second.reason) == ("no_action", "remediated_within_window")
        learner = _learner(application, learner_id)
        stored = learner.remediation_decisions["remediation:att-2"]
        assert (stored["action"], stored["reason"]) == ("no_action", "remediated_within_window")
        assert stored["decided_at"] == later.isoformat()
        assert stored["quest_id"] is None
        assert stored["deadline_at"] is None
        # No new quest: the only one is still the first decision's.
        assert set(learner.quest_instances) == {"remediation_quest:att-1"}
        assert len(_stream(application, learner_id, "RemediationDecided")) == 2
        assert len(_stream(application, learner_id, "QuestAssigned")) == 1
    finally:
        evidence.close()
        application.close()


def test_at17_uncompleted_quest_expires_after_its_deadline_and_cannot_be_completed(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        _attempt_failed(application, evidence, learner_id, "att-1", "math", T0)
        decision = run_remediation(application, learner_id, "att-1", clock=lambda: T0)
        deadline = decision.deadline_at
        assert deadline == T0 + timedelta(days=7)

        # At exactly the deadline the quest is not yet expired.
        assert run_quest_expiry(application, learner_id, clock=lambda: deadline) == []
        quest_id = "remediation_quest:att-1"
        assert _learner(application, learner_id).quest_instances[quest_id]["status"] == "assigned"

        # After the deadline, with no activity, the sweep expires it.
        after = deadline + timedelta(seconds=1)
        assert run_quest_expiry(application, learner_id, clock=lambda: after) == [quest_id]
        assert _learner(application, learner_id).quest_instances[quest_id]["status"] == "expired"
        expiry = _stream(application, learner_id, "QuestExpired")
        assert len(expiry) == 1
        assert expiry[0].expired_at == deadline.isoformat()

        # A remediation completion after that does not complete it.
        completed_at = deadline + timedelta(hours=2)
        _remediation_completion(
            application, evidence, learner_id, "rem-1", completed_at, completed_at
        )
        learner = _learner(application, learner_id)
        assert learner.quest_instances[quest_id]["status"] == "expired"
        assert learner.quest_instances[quest_id]["progress"] == 0
        assert _stream(application, learner_id, "QuestCompleted") == []
        assert set(learner.quest_instances) == {quest_id}
    finally:
        evidence.close()
        application.close()
