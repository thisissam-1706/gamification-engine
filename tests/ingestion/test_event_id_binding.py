"""Week 4 Fix 6a: a transport event_id is bound to the one action it delivered.

Week 3 review row 26: the same event_id sent with two different completion_ids
was accepted twice (200 XP). The binding lives in ``delivery_ids`` beside the
evidence table, so it must also hold after a restart on the same files.
"""

import sqlite3
from uuid import UUID, uuid4

import pytest

from src.ingestion.evidence import EvidenceStore
from src.ingestion.service import canonical_digest
from tests.ingestion.support import ingest_lesson_completed  # late_window=None
from src.state_machine.learner import Learner, LearnerApplication
from tests.ingestion.test_ingestion_service import _envelope

EVENT_ID = "00000000-0000-4000-8000-0000000006a1"


def _open(db: str) -> tuple[LearnerApplication, EvidenceStore]:
    application = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
    )
    return application, EvidenceStore(db)


def _ingest(application, evidence, envelope):
    result = ingest_lesson_completed(
        application, evidence, envelope, authenticated_producer="learning-svc"
    )
    return result.status, result.reason


def _new_learner(application: LearnerApplication) -> str:
    learner = Learner()
    application.save(learner)
    return str(learner.id)


def _xp_total(application: LearnerApplication, learner_id: str) -> int:
    return application.repository.get(UUID(learner_id)).xp_total


def test_same_event_id_with_different_completion_id_is_rejected(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        first = _envelope(learner_id, "cmp-A", EVENT_ID)
        reused = _envelope(learner_id, "cmp-B", EVENT_ID)

        assert _ingest(application, evidence, first) == ("accepted", None)
        events_before = list(application.events.get(UUID(learner_id)))
        assert _ingest(application, evidence, reused) == (
            "rejected",
            "conflicting_event_id_reuse",
        )

        learner = application.repository.get(UUID(learner_id))
        assert learner.xp_total == 100
        assert learner.completed_lesson_ids == {"cmp-A"}
        assert len(list(application.events.get(learner.id))) == len(events_before)
        rows = evidence.list(learner_id)
        assert len(rows) == 1
        assert (rows[0].kind, rows[0].reason) == ("rejected", "conflicting_event_id_reuse")
        assert (rows[0].action_id, rows[0].event_id, rows[0].original_ref) == (
            "cmp-B",
            EVENT_ID,
            "cmp-A",
        )
        binding = evidence.delivery_binding(EVENT_ID)
        assert binding is not None
        assert (binding.learner_id, binding.action_id, binding.digest) == (
            learner_id,
            "cmp-A",
            canonical_digest(first),
        )
    finally:
        evidence.close()
        application.close()


def test_same_event_id_with_identical_payload_is_a_duplicate(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        first = _envelope(learner_id, "cmp-A", EVENT_ID)
        assert _ingest(application, evidence, first) == ("accepted", None)

        # Transport metadata is outside the digest, so a redelivery may change it.
        resend = _envelope(learner_id, "cmp-A", EVENT_ID)
        resend["ingested_at"] = "2026-09-01T00:05:00+00:00"
        assert resend["correlation_id"] != first["correlation_id"]
        assert canonical_digest(resend) == canonical_digest(first)
        for _ in range(2):
            assert _ingest(application, evidence, resend) == (
                "duplicate",
                "duplicate_event_id",
            )

        assert _xp_total(application, learner_id) == 100
        rows = evidence.list(learner_id)
        assert len(rows) == 1
        assert (rows[0].kind, rows[0].reason, rows[0].original_ref) == (
            "duplicate",
            "duplicate_event_id",
            EVENT_ID,
        )
    finally:
        evidence.close()
        application.close()


def test_same_event_id_and_action_with_changed_payload_is_rejected(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        assert _ingest(application, evidence, _envelope(learner_id, "cmp-A", EVENT_ID)) == (
            "accepted",
            None,
        )
        altered = _envelope(learner_id, "cmp-A", EVENT_ID)
        altered["payload"]["lesson_id"] = "lesson-2"
        assert _ingest(application, evidence, altered) == (
            "rejected",
            "conflicting_event_id_reuse",
        )
        assert _xp_total(application, learner_id) == 100
        assert [row.original_ref for row in evidence.list(learner_id)] == ["cmp-A"]
    finally:
        evidence.close()
        application.close()


def test_event_id_binding_survives_restart_on_the_same_files(tmp_path):
    db = str(tmp_path / "events.db")
    application, evidence = _open(db)
    try:
        learner_id = _new_learner(application)
        first = _envelope(learner_id, "cmp-A", EVENT_ID)
        assert _ingest(application, evidence, first) == ("accepted", None)
    finally:
        evidence.close()
        application.close()
    del application, evidence

    restarted, restarted_evidence = _open(db)
    try:
        assert _ingest(
            restarted, restarted_evidence, _envelope(learner_id, "cmp-B", EVENT_ID)
        ) == ("rejected", "conflicting_event_id_reuse")
        assert _ingest(
            restarted, restarted_evidence, _envelope(learner_id, "cmp-A", EVENT_ID)
        ) == ("duplicate", "duplicate_event_id")

        assert _xp_total(restarted, learner_id) == 100
        rows = restarted_evidence.list(learner_id)
        assert [(row.kind, row.reason, row.original_ref) for row in rows] == [
            ("rejected", "conflicting_event_id_reuse", "cmp-A"),
            ("duplicate", "duplicate_event_id", EVENT_ID),
        ]
    finally:
        restarted_evidence.close()
        restarted.close()


def test_new_event_id_with_same_completion_id_is_unchanged(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        assert _ingest(application, evidence, _envelope(learner_id, "cmp-A", EVENT_ID)) == (
            "accepted",
            None,
        )
        retry_event_id = str(uuid4())
        assert _ingest(
            application, evidence, _envelope(learner_id, "cmp-A", retry_event_id)
        ) == ("duplicate", "duplicate_completion_id")

        assert _xp_total(application, learner_id) == 100
        rows = evidence.list(learner_id)
        assert [(row.reason, row.event_id, row.original_ref) for row in rows] == [
            ("duplicate_completion_id", retry_event_id, EVENT_ID)
        ]
        # A duplicate delivery does not bind its own event_id.
        assert evidence.delivery_binding(retry_event_id) is None
    finally:
        evidence.close()
        application.close()


def test_crash_before_binding_is_backfilled_from_the_aggregate(tmp_path, monkeypatch):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        first = _envelope(learner_id, "cmp-A", EVENT_ID)

        def crash(*args, **kwargs):
            raise RuntimeError("simulated crash")

        # The completion is saved; the process dies before the binding row is written.
        monkeypatch.setattr(evidence, "bind_delivery", crash)
        with pytest.raises(RuntimeError, match="simulated crash"):
            _ingest(application, evidence, first)
        monkeypatch.undo()
        assert _xp_total(application, learner_id) == 100
        assert evidence.delivery_binding(EVENT_ID) is None

        assert _ingest(application, evidence, _envelope(learner_id, "cmp-B", EVENT_ID)) == (
            "rejected",
            "conflicting_event_id_reuse",
        )
        binding = evidence.delivery_binding(EVENT_ID)
        assert binding is not None
        assert (binding.action_id, binding.digest) == ("cmp-A", None)

        assert _ingest(application, evidence, first) == ("duplicate", "duplicate_event_id")
        binding = evidence.delivery_binding(EVENT_ID)
        assert binding is not None
        assert binding.digest == canonical_digest(first)

        assert _xp_total(application, learner_id) == 100
        assert [(row.kind, row.original_ref) for row in evidence.list(learner_id)] == [
            ("rejected", "cmp-A"),
            ("duplicate", EVENT_ID),
        ]
    finally:
        evidence.close()
        application.close()


def test_database_without_delivery_ids_opens_and_is_migrated(tmp_path):
    db = str(tmp_path / "events.db")
    application, evidence = _open(db)
    try:
        learner_id = _new_learner(application)
        assert _ingest(application, evidence, _envelope(learner_id, "cmp-A", EVENT_ID)) == (
            "accepted",
            None,
        )
    finally:
        evidence.close()
        application.close()

    # Turn the file back into a pre-fix database.
    with sqlite3.connect(db) as connection:
        connection.execute("DROP TABLE delivery_ids")
    connection.close()

    application, evidence = _open(db)
    try:
        binding = evidence.delivery_binding(EVENT_ID)
        assert binding is not None
        assert (binding.learner_id, binding.action_id, binding.digest) == (
            learner_id,
            "cmp-A",
            None,
        )
        assert _ingest(application, evidence, _envelope(learner_id, "cmp-B", EVENT_ID)) == (
            "rejected",
            "conflicting_event_id_reuse",
        )
        assert _xp_total(application, learner_id) == 100
    finally:
        evidence.close()
        application.close()

    # Reopening an already migrated file is a no-op.
    EvidenceStore(db).close()
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM delivery_ids").fetchone() == (1,)
    connection.close()


def test_canonical_digest_covers_exactly_the_content_fields():
    envelope = _envelope("00000000-0000-4000-8000-000000000001", "cmp-A", EVENT_ID)
    digest = canonical_digest(envelope)
    assert len(digest) == 64

    for field, value in {
        "event_id": str(uuid4()),
        "ingested_at": "2026-09-02T00:00:00+00:00",
        "correlation_id": str(uuid4()),
        "causation_id": str(uuid4()),
        "dedup_key": "other",
        "provenance": {"source": "other"},
    }.items():
        assert canonical_digest({**envelope, field: value}) == digest, field

    reordered = dict(reversed(list(envelope.items())))
    reordered["payload"] = dict(reversed(list(envelope["payload"].items())))
    assert canonical_digest(reordered) == digest

    for field, value in {
        "event_type": "learning.other",
        "schema_version": 2,
        "subject_id": "00000000-0000-4000-8000-000000000002",
        "producer": "other-svc",
        "occurred_at": "2026-09-02T00:00:00+00:00",
        "payload": {**envelope["payload"], "lesson_id": "lesson-2"},
    }.items():
        assert canonical_digest({**envelope, field: value}) != digest, field
