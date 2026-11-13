"""Events derived while handling one lesson_completed delivery carry that delivery's ids.

Rule: correlation_id = the envelope's correlation_id;
      causation_id   = the envelope's event_id (the delivery that started the chain).
LessonCompleted (the source fact) keeps the envelope's own causation_id.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from src.ingestion.evidence import EvidenceStore
from src.ingestion.validation import DEFAULT_INGEST_RULE_VERSION, expected_dedup_key
from src.state_machine.learner import Learner, LearnerApplication
from tests.ingestion.support import ingest_lesson_completed  # late_window=None

CORRELATION_ID = "corr-1"
EVENT_ID = "00000000-0000-4000-8000-00000000a001"


def _envelope(
    learner_id: str,
    completion_id: str,
    *,
    occurred_at: datetime,
    event_id: str = EVENT_ID,
    causation_id: str | None = None,
    assignment_id: str | None = None,
):
    payload = {
        "completion_id": completion_id,
        "lesson_id": f"lesson-{completion_id}",
        "timezone": "Asia/Kolkata",
    }
    if assignment_id is not None:
        payload["assignment_id"] = assignment_id
    return {
        "event_id": event_id,
        "event_type": "learning.lesson_completed",
        "schema_version": 1,
        "occurred_at": occurred_at.isoformat(),
        "ingested_at": (occurred_at + timedelta(minutes=1)).isoformat(),
        "subject_id": learner_id,
        "producer": "learning-svc",
        "correlation_id": CORRELATION_ID,
        "causation_id": causation_id,
        "dedup_key": expected_dedup_key(event_id, DEFAULT_INGEST_RULE_VERSION),
        "payload": payload,
        "provenance": {"source": "client"},
    }


def _open(tmp_path):
    db = str(tmp_path / "events.db")
    environment = {"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
    application = LearnerApplication(env=environment)
    evidence = EvidenceStore(db)
    learner = Learner()
    application.save(learner)
    return application, evidence, learner.id


def _stored(application, learner_id, event_class):
    return [
        e for e in application.events.get(UUID(str(learner_id))) if isinstance(e, event_class)
    ]


def _assert_ids(events, *, correlation_id=CORRELATION_ID, causation_id=EVENT_ID):
    assert events, "expected at least one event of this type"
    for e in events:
        assert getattr(e, "correlation_id", None) == correlation_id, type(e).__name__
        assert getattr(e, "causation_id", None) == causation_id, type(e).__name__


def test_a_xp_and_streak_events_carry_delivery_ids(tmp_path):
    application, evidence, learner_id = _open(tmp_path)
    try:
        envelope = _envelope(
            str(learner_id), "c-1", occurred_at=datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
        )
        result = ingest_lesson_completed(
            application, evidence, envelope, authenticated_producer="learning-svc"
        )
        assert result.status == "accepted"

        _assert_ids(_stored(application, learner_id, Learner.XPAwarded))
        _assert_ids(_stored(application, learner_id, Learner.StreakUpdated))
    finally:
        evidence.close()
        application.close()


def test_b_quest_and_badge_events_carry_delivery_ids(tmp_path):
    """Delivery 7 progresses+completes lesson_starter_v1 (target 3) and earns the badge (7-day streak)."""
    application, evidence, learner_id = _open(tmp_path)
    try:
        def day(n):
            return datetime(2026, 9, n, 12, tzinfo=timezone.utc)

        def seed(n):
            learner = application.repository.get(learner_id)
            learner.complete_lesson(
                f"seed-{n}",
                f"lesson-seed-{n}",
                occurred_at=day(n).isoformat(),
                timezone="Asia/Kolkata",
            )
            application.save(learner)

        for n in range(1, 5):  # days 1-4: streak builds, no quest yet
            seed(n)
        learner = application.repository.get(learner_id)
        learner.assign_quest(
            "lesson_starter_v1",
            assignment_id="assignment-1",
            assigned_at="2026-09-05T00:00:00Z",
        )
        application.save(learner)
        for n in (5, 6):  # quest progress 1 and 2 (target is 3)
            seed(n)

        envelope = _envelope(
            str(learner_id), "c-7", occurred_at=day(7), assignment_id="assignment-1"
        )
        result = ingest_lesson_completed(
            application, evidence, envelope, authenticated_producer="learning-svc"
        )
        assert result.status == "accepted"

        # Setup sanity: this delivery really did all three things.
        final = application.repository.get(learner_id)
        assert final.quest_instances["assignment-1"]["status"] == "completed"
        assert final.badges_issued, "badge should have been awarded"

        def from_delivery(event_class):
            # Seeded events were created without an envelope, so only the final
            # delivery's events are checked: take the events after LessonCompleted c-7.
            events = list(application.events.get(UUID(str(learner_id))))
            start = next(
                i
                for i, e in enumerate(events)
                if isinstance(e, Learner.LessonCompleted) and e.completion_id == "c-7"
            )
            return [e for e in events[start:] if isinstance(e, event_class)]

        for event_class in (Learner.QuestProgressed, Learner.QuestCompleted, Learner.BadgeAwarded):
            _assert_ids(from_delivery(event_class))
    finally:
        evidence.close()
        application.close()


def test_c_source_event_keeps_envelope_causation_derived_event_uses_event_id(tmp_path):
    application, evidence, learner_id = _open(tmp_path)
    try:
        envelope = _envelope(
            str(learner_id),
            "c-1",
            occurred_at=datetime(2026, 9, 1, 12, tzinfo=timezone.utc),
            causation_id="root-cause-1",
        )
        result = ingest_lesson_completed(
            application, evidence, envelope, authenticated_producer="learning-svc"
        )
        assert result.status == "accepted"

        completed = _stored(application, learner_id, Learner.LessonCompleted)
        assert len(completed) == 1
        assert completed[0].causation_id == "root-cause-1"  # unchanged behaviour
        assert completed[0].correlation_id == CORRELATION_ID

        _assert_ids(_stored(application, learner_id, Learner.XPAwarded))
    finally:
        evidence.close()
        application.close()
