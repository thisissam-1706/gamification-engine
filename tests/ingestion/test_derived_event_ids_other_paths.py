"""M1 step 3: correlation/causation ids on engine-produced events outside the lesson chain.

D  remediation (RemediationDecided + remediation QuestAssigned) inherit the attempt delivery's ids
E  the expiry sweep stamps its own correlation id and no causation id
F  quest expiry inside a lesson delivery carries that delivery's ids (already true after step 2)
G  cohort assignment: default correlation id "cohort_assignment:<epoch>", or explicit ids
H  freeze acquisition accepts and stores optional ids
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from src.interventions.service import run_quest_expiry, run_remediation
from src.state_machine.learner import Learner
from tests.ingestion.support import ingest_lesson_completed  # late_window=None
from tests.ingestion.test_derived_event_ids import _envelope as _lesson_envelope
from tests.interventions.test_checkpoint_attempt_ingestion import (
    _checkpoint_envelope,
    _ingest_checkpoint,
    _new_learner,
    _open,
)

ASSIGNED_AT = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _events(application, learner_id, name):
    return [
        e
        for e in application.events.get(UUID(str(learner_id)))
        if type(e).__name__ == name
    ]


def _ids(event):
    return getattr(event, "correlation_id", None), getattr(event, "causation_id", None)


# --- D: remediation ------------------------------------------------------------


def test_d_remediation_events_carry_the_attempt_delivery_ids(tmp_path):
    attempt_event_id = "00000000-0000-4000-8000-00000000d001"
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        envelope = _checkpoint_envelope(learner_id, "att-1", attempt_event_id)
        envelope["payload"]["passed"] = False
        envelope["correlation_id"] = "att-corr"
        result = _ingest_checkpoint(application, evidence, envelope)
        assert result.status == "accepted"

        decision = run_remediation(
            application,
            learner_id,
            "att-1",
            clock=lambda: datetime(2026, 9, 10, 12, tzinfo=timezone.utc),
        )
        assert decision.action == "assign"  # setup sanity: a quest was assigned

        decided = _events(application, learner_id, "RemediationDecided")
        assert len(decided) == 1
        quests = [
            e
            for e in _events(application, learner_id, "QuestAssigned")
            if e.assignment_id == "remediation_quest:att-1"
        ]
        assert len(quests) == 1
        for e in decided + quests:
            assert _ids(e) == ("att-corr", attempt_event_id), type(e).__name__
    finally:
        evidence.close()
        application.close()


# --- E: sweep -------------------------------------------------------------------


def test_e_sweep_expiry_has_sweep_correlation_id_and_no_causation(tmp_path):
    as_of = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        learner = application.repository.get(UUID(learner_id))
        learner.assign_quest(
            "lesson_starter_v1",
            assignment_id="assignment-1",
            assigned_at=ASSIGNED_AT,
            deadline_at=ASSIGNED_AT + timedelta(days=7),
        )
        application.save(learner)

        assert run_quest_expiry(application, learner_id, clock=lambda: as_of) == [
            "assignment-1"
        ]

        expired = _events(application, learner_id, "QuestExpired")
        assert len(expired) == 1
        assert _ids(expired[0]) == (f"quest_expiry_sweep:{as_of.isoformat()}", None)
    finally:
        evidence.close()
        application.close()


# --- F: expiry during a lesson delivery (should already pass after step 2) ------


def test_f_expiry_inside_a_lesson_delivery_carries_the_delivery_ids(tmp_path):
    event_id = "00000000-0000-4000-8000-00000000f001"
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        learner = application.repository.get(UUID(learner_id))
        learner.assign_quest(
            "lesson_starter_v1",
            assignment_id="assignment-1",
            assigned_at="2026-08-01T00:00:00Z",
            deadline_at="2026-08-15T00:00:00Z",
        )
        application.save(learner)

        envelope = _lesson_envelope(
            learner_id,
            "late-1",
            occurred_at=datetime(2026, 9, 1, 12, tzinfo=timezone.utc),
            event_id=event_id,
            assignment_id="assignment-1",
        )
        result = ingest_lesson_completed(
            application, evidence, envelope, authenticated_producer="learning-svc"
        )
        assert result.status == "accepted"

        expired = _events(application, learner_id, "QuestExpired")
        assert len(expired) == 1
        assert _ids(expired[0]) == ("corr-1", event_id)
    finally:
        evidence.close()
        application.close()


# --- G: cohort ------------------------------------------------------------------


def _fresh_learner() -> Learner:
    learner = Learner()
    learner.collect_events()  # drop the creation event
    return learner


def test_g_cohort_assignment_without_ids_gets_the_default_correlation_id():
    learner = _fresh_learner()
    learner.assign_cohort("c1", "2026-W42", assigned_at=ASSIGNED_AT)

    (event,) = learner.collect_events()
    assert type(event).__name__ == "CohortAssigned"
    assert _ids(event) == ("cohort_assignment:2026-W42", None)


def test_g_cohort_assignment_with_explicit_ids_stores_them():
    learner = _fresh_learner()
    learner.assign_cohort(
        "c1",
        "2026-W42",
        assigned_at=ASSIGNED_AT,
        correlation_id="my-corr",
        causation_id="my-cause",
    )

    (event,) = learner.collect_events()
    assert _ids(event) == ("my-corr", "my-cause")


# --- H: freeze ------------------------------------------------------------------


def test_h_freeze_without_ids_stores_none():
    learner = _fresh_learner()
    learner.acquire_freeze("freeze-1", activity_date="2026-09-01")

    (event,) = learner.collect_events()
    assert type(event).__name__ == "FreezeAcquired"
    # The attributes must exist and be None (not merely be absent).
    assert hasattr(event, "correlation_id") and hasattr(event, "causation_id")
    assert _ids(event) == (None, None)


def test_h_freeze_with_explicit_ids_stores_them():
    learner = _fresh_learner()
    learner.acquire_freeze(
        "freeze-1",
        activity_date="2026-09-01",
        correlation_id="my-corr",
        causation_id="my-cause",
    )

    (event,) = learner.collect_events()
    assert _ids(event) == ("my-corr", "my-cause")
