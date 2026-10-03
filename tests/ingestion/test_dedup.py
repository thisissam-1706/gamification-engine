"""AT-1: Lesson-completion idempotency (M1).

Given learner L1 completes lesson l_492 with completion_id cmp_492_003, when the
same completion is delivered again during the 7-day ingestion deduplication
window with a new event_id and dedup_key lesson_completed:cmp_492_003, then it
is silently discarded and the lesson count and XP total remain unchanged.

Given the same completion is resent after the 7-day window, when the aggregate
processes it, then it recognises the existing completion/reward identity and
does not increment the count or create a second XP award.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from src.ingestion.evidence import EvidenceStore
from src.ingestion.service import ingest_lesson_completed
from src.state_machine.learner import Learner, LearnerApplication
from tests.ingestion.test_ingestion_service import _envelope


def test_duplicate_lesson_completion_is_dropped_within_ingestion_window(tmp_path):
    """A changed delivery UUID must not make a completion a new business action."""
    db = str(tmp_path / "events.db")
    application = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
    )
    evidence = EvidenceStore(db)
    try:
        learner = Learner()
        application.save(learner)
        envelope = _envelope(str(learner.id), "cmp_492_003", str(uuid4()))
        assert ingest_lesson_completed(application, evidence, envelope).status == "accepted"
        retry = dict(envelope)
        retry["event_id"] = str(uuid4())
        assert ingest_lesson_completed(application, evidence, retry).status == "duplicate"
        restored = application.repository.get(learner.id)
        assert restored.xp_total == 100
        assert len(evidence.list(str(learner.id))) == 1
    finally:
        evidence.close()
        application.close()


def test_resend_after_ingestion_window_cannot_issue_a_second_xp_reward(tmp_path):
    """Durable business identity wins after the short-window cache has expired."""
    environment = {
        "PERSISTENCE_MODULE": "eventsourcing.sqlite",
        "SQLITE_DBNAME": str(tmp_path / "events.db"),
    }

    application = LearnerApplication(env=environment)
    first_event_id = "delivery-0001"
    first_received_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    try:
        learner = Learner()
        assert learner.complete_lesson(
            "cmp_492_003", "l_492", xp_amount=40, rule_version="xp-v1"
        ) is True
        application.save(learner)
        learner_id = learner.id
    finally:
        application.close()

    restarted_application = LearnerApplication(env=environment)
    try:
        replayed_learner = restarted_application.repository.get(learner_id)
        events_before_resend = list(restarted_application.events.get(learner_id))
        resent_event_id = "delivery-0002"
        resent_received_at = first_received_at + timedelta(days=8)

        assert resent_event_id != first_event_id
        assert resent_received_at - first_received_at > timedelta(days=7)
        # New envelope identity and delayed delivery do not alter business identity.
        assert replayed_learner.complete_lesson("cmp_492_003", "l_492") is False
        assert restarted_application.save(replayed_learner) == []
        assert replayed_learner.completed_lesson_count == 1
        assert replayed_learner.lesson_completion_counts == {"l_492": 1}
        assert replayed_learner.xp_total == 40
        assert replayed_learner.awarded_reward_ids == {"xp:cmp_492_003"}
        assert replayed_learner.applied_rule_versions == {"xp:cmp_492_003": "xp-v1"}

        events_after_resend = list(restarted_application.events.get(learner_id))
        assert len(events_after_resend) == len(events_before_resend)
        assert sum(
            isinstance(domain_event, Learner.XPAwarded)
            for domain_event in events_after_resend
        ) == 1
    finally:
        restarted_application.close()


def test_retry_after_persistence_before_acknowledgement_has_one_reward(tmp_path):
    """A lost acknowledgement retries the action, not the persisted reward."""
    environment = {
        "PERSISTENCE_MODULE": "eventsourcing.sqlite",
        "SQLITE_DBNAME": str(tmp_path / "events.db"),
    }
    application = LearnerApplication(env=environment)
    try:
        learner = Learner()
        learner.complete_lesson("cmp_crash_001", "l_crash", xp_amount=70)
        application.save(learner)  # Persistence succeeded; acknowledgement is lost.
        learner_id = learner.id
    finally:
        application.close()

    retry_application = LearnerApplication(env=environment)
    try:
        retried = retry_application.repository.get(learner_id)
        assert retried.complete_lesson("cmp_crash_001", "l_crash", xp_amount=70) is False
        assert retry_application.save(retried) == []
        stored_events = list(retry_application.events.get(learner_id))
        assert sum(isinstance(event, Learner.LessonCompleted) for event in stored_events) == 1
        assert sum(isinstance(event, Learner.XPAwarded) for event in stored_events) == 1
        assert retried.xp_total == 70
    finally:
        retry_application.close()
