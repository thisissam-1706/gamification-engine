"""End-to-end ingestion, producer binding, and durable evidence tests."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from src.ingestion.evidence import EvidenceStore
from src.ingestion.service import ingest_lesson_completed
from src.state_machine.learner import Learner, LearnerApplication
from src.state_machine.projection import LearnerCompletionProjection


def _envelope(learner_id: str, completion_id: str, event_id: str | None = None):
    occurred = datetime(2026, 9, 1, tzinfo=timezone.utc)
    return {
        "event_id": event_id or str(uuid4()),
        "event_type": "learning.lesson_completed",
        "schema_version": 1,
        "occurred_at": occurred.isoformat(),
        "ingested_at": (occurred + timedelta(minutes=1)).isoformat(),
        "subject_id": learner_id,
        "producer": "learning-svc",
        "correlation_id": str(uuid4()),
        "causation_id": None,
        "dedup_key": f"lesson_completed:{completion_id}",
        "payload": {
            "completion_id": completion_id,
            "lesson_id": "lesson-1",
            "timezone": "Asia/Kolkata",
        },
        "provenance": {"source": "client"},
    }


def test_ingestion_deduplicates_late_resends_and_preserves_original_ref(tmp_path):
    db = str(tmp_path / "events.db")
    app = LearnerApplication(env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db})
    evidence = EvidenceStore(db)
    try:
        learner = Learner()
        app.save(learner)
        learner_id = str(learner.id)
        first = _envelope(learner_id, "completion-1", "00000000-0000-4000-8000-000000000001")
        assert ingest_lesson_completed(app, evidence, first).status == "accepted"
        before = list(app.events.get(learner.id))
        for index in range(3):
            resent = _envelope(
                learner_id,
                "completion-1",
                f"00000000-0000-4000-8000-00000000000{index + 2}",
            )
            resent["occurred_at"] = (datetime(2026, 9, 10, tzinfo=timezone.utc)).isoformat()
            result = ingest_lesson_completed(app, evidence, resent)
            assert (result.status, result.reason) == ("duplicate", "duplicate_completion_id")
        after = list(app.events.get(learner.id))
        rows = evidence.list(learner_id)
        assert len(after) == len(before)
        assert sum(isinstance(event, Learner.XPAwarded) for event in after) == 1
        assert len(rows) == 3
        assert {row.original_ref for row in rows} == {first["event_id"]}
    finally:
        evidence.close()
        app.close()


def test_ingestion_rejection_and_producer_binding_are_durable(tmp_path):
    db = str(tmp_path / "events.db")
    app = LearnerApplication(env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db})
    evidence = EvidenceStore(db)
    try:
        learner = Learner()
        app.save(learner)
        learner_id = str(learner.id)
        invalid = _envelope(learner_id, "bad", str(uuid4()))
        invalid["payload"]["xp_amount"] = 999
        assert ingest_lesson_completed(app, evidence, invalid).reason == "reward_field_in_source_event"
        disallowed = _envelope(learner_id, "bad-2", str(uuid4()))
        disallowed["producer"] = "assessment-svc"
        assert ingest_lesson_completed(app, evidence, disallowed).reason == "untrusted_producer"
        client_claim = _envelope(learner_id, "bad-3", str(uuid4()))
        client_claim["provenance"] = {"source": "server_authoritative"}
        assert ingest_lesson_completed(app, evidence, client_claim).reason == "untrusted_producer"
        assert learner_id
        assert len(list(app.events.get(learner.id))) == 1
        assert [row.kind for row in evidence.list(learner_id)] == ["rejected", "rejected", "rejected"]
        projection = LearnerCompletionProjection()
        projection.rebuild(app.events.get(learner.id))
        assert projection.completed_lesson_counts == {}
    finally:
        evidence.close()
        app.close()

    restarted = EvidenceStore(db)
    try:
        assert len(restarted.list(learner_id)) == 3
    finally:
        restarted.close()
