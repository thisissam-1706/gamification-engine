"""End-to-end ingestion, producer binding, and durable evidence tests."""

from datetime import datetime, timedelta, timezone
import multiprocessing
from uuid import uuid4

from src.ingestion.evidence import EvidenceStore
from src.ingestion.service import ingest_lesson_completed
from src.state_machine.learner import Learner, LearnerApplication
from src.state_machine.projection import LearnerCompletionProjection


def _ingest_in_worker(db: str, learner_id: str, envelope: dict, results) -> None:
    application = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
    )
    evidence = EvidenceStore(db)
    try:
        result = ingest_lesson_completed(application, evidence, envelope)
        results.put((result.status, result.reason))
    except Exception as exc:  # pragma: no cover - proves the worker never leaks errors
        results.put(("error", type(exc).__name__))
    finally:
        evidence.close()
        application.close()


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


def test_concurrent_duplicate_completion_is_acknowledged_as_one_duplicate(tmp_path):
    db = str(tmp_path / "events.db")
    environment = {"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
    application = LearnerApplication(env=environment)
    evidence = EvidenceStore(db)
    try:
        learner = Learner()
        application.save(learner)
        learner_id = str(learner.id)
    finally:
        evidence.close()
        application.close()

    first = _envelope(learner_id, "concurrent-1", "00000000-0000-4000-8000-000000000101")
    second = _envelope(learner_id, "concurrent-1", "00000000-0000-4000-8000-000000000102")
    context = multiprocessing.get_context("fork")
    results = context.Queue()
    workers = [
        context.Process(target=_ingest_in_worker, args=(db, learner_id, envelope, results))
        for envelope in (first, second)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert sorted(results.get() for _ in workers) == [
        ("accepted", None),
        ("duplicate", "duplicate_completion_id"),
    ]
    application = LearnerApplication(env=environment)
    evidence = EvidenceStore(db)
    try:
        events = list(application.events.get(learner.id))
        rows = evidence.list(learner_id)
        assert sum(isinstance(event, Learner.LessonCompleted) for event in events) == 1
        assert sum(isinstance(event, Learner.XPAwarded) for event in events) == 1
        assert application.repository.get(learner.id).xp_total == 100
        assert len(rows) == 1
        assert rows[0].reason == "duplicate_completion_id"
        assert rows[0].original_ref in {first["event_id"], second["event_id"]}
    finally:
        evidence.close()
        application.close()


def test_concurrent_different_completions_retry_without_lost_update(tmp_path):
    db = str(tmp_path / "events.db")
    environment = {"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
    application = LearnerApplication(env=environment)
    learner = Learner()
    application.save(learner)
    learner_id = str(learner.id)
    application.close()

    context = multiprocessing.get_context("fork")
    results = context.Queue()
    workers = [
        context.Process(
            target=_ingest_in_worker,
            args=(
                db,
                learner_id,
                _envelope(learner_id, completion_id, event_id),
                results,
            ),
        )
        for completion_id, event_id in (
            ("concurrent-a", "00000000-0000-4000-8000-000000000103"),
            ("concurrent-b", "00000000-0000-4000-8000-000000000104"),
        )
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert sorted(results.get() for _ in workers) == [
        ("accepted", None),
        ("accepted", None),
    ]
    application = LearnerApplication(env=environment)
    evidence = EvidenceStore(db)
    try:
        events = list(application.events.get(learner.id))
        assert sum(isinstance(event, Learner.LessonCompleted) for event in events) == 2
        assert sum(isinstance(event, Learner.XPAwarded) for event in events) == 2
        assert application.repository.get(learner.id).xp_total == 200
        assert evidence.list(learner_id) == []
    finally:
        evidence.close()
        application.close()


def test_duplicate_quest_completion_writes_durable_evidence(tmp_path):
    db = str(tmp_path / "events.db")
    environment = {"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
    application = LearnerApplication(env=environment)
    evidence = EvidenceStore(db)
    try:
        learner = Learner()
        application.save(learner)
        learner_id = str(learner.id)
        learner = application.repository.get(learner.id)
        learner.assign_quest("lesson_starter_v1", assignment_id="assignment-1")
        application.save(learner)
        for index in range(3):
            payload = _envelope(learner_id, f"quest-{index}", f"00000000-0000-4000-8000-00000000020{index}")
            payload["payload"]["assignment_id"] = "assignment-1"
            assert ingest_lesson_completed(application, evidence, payload).status == "accepted"
        events_before = list(application.events.get(learner.id))
        duplicate = _envelope(learner_id, "quest-repeat", "00000000-0000-4000-8000-000000000204")
        duplicate["payload"]["assignment_id"] = "assignment-1"
        result = ingest_lesson_completed(application, evidence, duplicate)
        assert (result.status, result.reason) == (
            "duplicate",
            "duplicate_quest_completion",
        )
        assert list(application.events.get(learner.id)) == events_before
        rows = evidence.list(learner_id)
        assert len(rows) == 1
        assert rows[0].action_id == "quest_completion:assignment-1"
        assert rows[0].original_ref == "quest_completion:assignment-1"
    finally:
        evidence.close()
        application.close()

    restarted = EvidenceStore(db)
    try:
        rows = restarted.list(learner_id)
        assert len(rows) == 1
        assert rows[0].reason == "duplicate_quest_completion"
    finally:
        restarted.close()


def test_external_derived_events_are_rejected_with_evidence(tmp_path):
    db = str(tmp_path / "events.db")
    environment = {"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
    application = LearnerApplication(env=environment)
    evidence = EvidenceStore(db)
    try:
        learner = Learner()
        application.save(learner)
        learner_id = str(learner.id)
        events_before = list(application.events.get(learner.id))
        xp_before = application.repository.get(learner.id).xp_total
        for index, event_type in enumerate(
            (
                "gamification.xp_awarded",
                "gamification.streak_updated",
                "gamification.badge_awarded",
            )
        ):
            envelope = _envelope(
                learner_id,
                f"derived-{index}",
                f"00000000-0000-4000-8000-00000000030{index}",
            )
            envelope["event_type"] = event_type
            envelope["producer"] = "learning-svc"
            result = ingest_lesson_completed(application, evidence, envelope)
            assert (result.status, result.reason) == (
                "rejected",
                "untrusted_producer",
            )
        assert list(application.events.get(learner.id)) == events_before
        assert application.repository.get(learner.id).xp_total == xp_before
        rows = evidence.list(learner_id)
        assert len(rows) == 3
        assert [row.reason for row in rows] == ["untrusted_producer"] * 3
    finally:
        evidence.close()
        application.close()
