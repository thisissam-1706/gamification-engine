"""Week 4 Fix 7a: source provenance is retained for every accepted delivery.

Week 3 review row 26: source correlation/causation and the canonical envelope
were not retained. They are now kept in two places: on the delivery_ids row
(evidence store) and on the stored LessonCompleted domain fact.
"""

import json
import sqlite3
from uuid import UUID

import pytest

from src.ingestion.evidence import PROVENANCE_COLUMNS, EvidenceStore
from src.ingestion.service import canonical_digest
from tests.ingestion.support import ingest_lesson_completed  # late_window=None
from src.state_machine.learner import Learner, LearnerApplication
from tests.ingestion.test_ingestion_service import _envelope

EVENT_ID = "00000000-0000-4000-8000-0000000007a1"
CORRELATION_ID = "00000000-0000-4000-8000-0000000007c1"
CAUSATION_ID = "00000000-0000-4000-8000-0000000007d1"


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


def _lesson_events(application: LearnerApplication, learner_id: str):
    return [
        event
        for event in application.events.get(UUID(learner_id))
        if isinstance(event, Learner.LessonCompleted)
    ]


def _provenance_row(evidence: EvidenceStore, event_id: str) -> dict:
    binding = evidence.delivery_binding(event_id)
    assert binding is not None
    return {
        "learner_id": binding.learner_id,
        "action_id": binding.action_id,
        "digest": binding.digest,
        **{column: getattr(binding, column) for column in PROVENANCE_COLUMNS},
    }


def _assert_full_provenance(application, evidence, learner_id, envelope):
    # Every field is spelled out literally rather than copied from the envelope.
    assert _provenance_row(evidence, EVENT_ID) == {
        "learner_id": learner_id,
        "action_id": "cmp-A",
        "digest": canonical_digest(envelope),
        "producer": "learning-svc",
        "event_type": "learning.lesson_completed",
        "schema_version": 1,
        "occurred_at": "2026-09-01T00:00:00+00:00",
        "ingested_at": "2026-09-01T00:01:00+00:00",
        "correlation_id": "00000000-0000-4000-8000-0000000007c1",
        "causation_id": None,
        "dedup_key": "00000000-0000-4000-8000-0000000007a1:v1",
        "provenance_json": '{"source":"client"}',
        "rule_version": "v1",
    }
    [stored] = _lesson_events(application, learner_id)
    assert stored.completion_id == "cmp-A"
    assert stored.source_event_id == "00000000-0000-4000-8000-0000000007a1"
    assert stored.correlation_id == "00000000-0000-4000-8000-0000000007c1"
    assert stored.causation_id is None
    assert "causation_id" in stored._as_dict()


def _accepted_envelope(learner_id: str) -> dict:
    envelope = _envelope(learner_id, "cmp-A", EVENT_ID)
    envelope["correlation_id"] = CORRELATION_ID
    assert envelope["causation_id"] is None
    return envelope


def test_accepted_delivery_keeps_exact_envelope_provenance(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        envelope = _accepted_envelope(learner_id)
        assert _ingest(application, evidence, envelope) == ("accepted", None)
        _assert_full_provenance(application, evidence, learner_id, envelope)
        # Provenance is recorded, not folded: XP is unchanged by the fix.
        assert application.repository.get(UUID(learner_id)).xp_total == 100
    finally:
        evidence.close()
        application.close()


def test_non_null_causation_id_is_kept(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        envelope = _accepted_envelope(learner_id)
        envelope["causation_id"] = CAUSATION_ID
        assert _ingest(application, evidence, envelope) == ("accepted", None)

        assert _provenance_row(evidence, EVENT_ID)["causation_id"] == (
            "00000000-0000-4000-8000-0000000007d1"
        )
        [stored] = _lesson_events(application, learner_id)
        assert stored.causation_id == "00000000-0000-4000-8000-0000000007d1"
    finally:
        evidence.close()
        application.close()


def test_provenance_survives_restart_on_the_same_files(tmp_path):
    db = str(tmp_path / "events.db")
    application, evidence = _open(db)
    try:
        learner_id = _new_learner(application)
        envelope = _accepted_envelope(learner_id)
        assert _ingest(application, evidence, envelope) == ("accepted", None)
    finally:
        evidence.close()
        application.close()
    del application, evidence

    restarted, restarted_evidence = _open(db)
    try:
        _assert_full_provenance(restarted, restarted_evidence, learner_id, envelope)
        assert restarted.repository.get(UUID(learner_id)).xp_total == 100
    finally:
        restarted_evidence.close()
        restarted.close()


def test_old_shape_lesson_completed_event_still_replays(tmp_path):
    """A LessonCompleted stored before this change has no correlation/causation keys."""
    db = str(tmp_path / "events.db")
    application = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
    )
    try:
        learner = Learner()
        assert learner.complete_lesson(
            "cmp-old",
            "lesson-1",
            occurred_at="2026-09-01T00:00:00+00:00",
            timezone="Asia/Kolkata",
            source_event_id="delivery-old",
        )
        application.save(learner)
        learner_id = learner.id
    finally:
        application.close()

    # Rewrite the stored event into the exact pre-Fix-7a shape.
    with sqlite3.connect(db) as connection:
        [(rowid, state)] = connection.execute(
            "SELECT rowid, state FROM stored_events WHERE topic = ?",
            ("src.state_machine.learner:Learner.LessonCompleted",),
        ).fetchall()
        old_shape = json.loads(state)
        assert (old_shape.pop("correlation_id"), old_shape.pop("causation_id")) == (None, None)
        assert sorted(old_shape) == [
            "activity_date",
            "completion_id",
            "lesson_id",
            "occurred_at",
            "source_event_id",
            "timestamp",
            "timezone_name",
        ]
        connection.execute(
            "UPDATE stored_events SET state = ? WHERE rowid = ?",
            (json.dumps(old_shape, separators=(",", ":")).encode(), rowid),
        )
    connection.close()

    restarted = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
    )
    try:
        replayed = restarted.repository.get(learner_id)
        assert replayed.completed_lesson_ids == {"cmp-old"}
        assert replayed.completion_sources == {"cmp-old": "delivery-old"}
        assert replayed.completed_lesson_count == 1
        assert replayed.lesson_completion_counts == {"lesson-1": 1}
        assert replayed.activity_dates == {"2026-09-01"}
        assert replayed.xp_total == 100
        assert replayed.new_streak == 1
        assert replayed.timezone_history == [
            {
                "effective_at": "2026-09-01T00:00:00+00:00",
                "timezone": "Asia/Kolkata",
                "action_id": "cmp-old",
            }
        ]
        [stored] = [
            event
            for event in restarted.events.get(learner_id)
            if isinstance(event, Learner.LessonCompleted)
        ]
        # The old event really is old-shaped: the attributes are absent, not None.
        assert "correlation_id" not in stored._as_dict()
        assert "causation_id" not in stored._as_dict()
        assert getattr(stored, "correlation_id", None) is None
    finally:
        restarted.close()


def _make_pre_change_delivery_ids(db: str) -> None:
    """Rebuilds delivery_ids in the four-column shape it had before Fix 7a."""
    with sqlite3.connect(db) as connection:
        connection.executescript(
            """
            CREATE TABLE delivery_ids_old (
                event_id TEXT PRIMARY KEY,
                learner_id TEXT NOT NULL,
                action_id TEXT NOT NULL,
                digest TEXT
            );
            INSERT INTO delivery_ids_old
                SELECT event_id, learner_id, action_id, digest FROM delivery_ids;
            DROP TABLE delivery_ids;
            ALTER TABLE delivery_ids_old RENAME TO delivery_ids;
            """
        )
    connection.close()


def _columns(db: str) -> list[str]:
    with sqlite3.connect(db) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(delivery_ids)")]
    connection.close()
    return columns


def test_pre_change_database_opens_keeps_rows_and_stores_new_provenance(tmp_path):
    db = str(tmp_path / "events.db")
    application, evidence = _open(db)
    try:
        learner_id = _new_learner(application)
        first = _accepted_envelope(learner_id)
        assert _ingest(application, evidence, first) == ("accepted", None)
    finally:
        evidence.close()
        application.close()

    _make_pre_change_delivery_ids(db)
    assert _columns(db) == ["event_id", "learner_id", "action_id", "digest"]

    application, evidence = _open(db)
    try:
        assert _columns(db) == [
            "event_id",
            "learner_id",
            "action_id",
            "digest",
            "producer",
            "event_type",
            "schema_version",
            "occurred_at",
            "ingested_at",
            "correlation_id",
            "causation_id",
            "dedup_key",
            "provenance_json",
            "rule_version",
            "rule_digest",
            "received_at",
        ]
        # The pre-change row is kept as it was, with NULL provenance.
        assert _provenance_row(evidence, EVENT_ID) == {
            "learner_id": learner_id,
            "action_id": "cmp-A",
            "digest": canonical_digest(first),
            **{column: None for column in PROVENANCE_COLUMNS},
        }
        assert _ingest(application, evidence, _envelope(learner_id, "cmp-B", EVENT_ID)) == (
            "rejected",
            "conflicting_event_id_reuse",
        )

        later_event_id = "00000000-0000-4000-8000-0000000007a2"
        later = _envelope(learner_id, "cmp-later", later_event_id)
        later["correlation_id"] = "00000000-0000-4000-8000-0000000007c2"
        assert _ingest(application, evidence, later) == ("accepted", None)
        assert _provenance_row(evidence, later_event_id) == {
            "learner_id": learner_id,
            "action_id": "cmp-later",
            "digest": canonical_digest(later),
            "producer": "learning-svc",
            "event_type": "learning.lesson_completed",
            "schema_version": 1,
            "occurred_at": "2026-09-01T00:00:00+00:00",
            "ingested_at": "2026-09-01T00:01:00+00:00",
            "correlation_id": "00000000-0000-4000-8000-0000000007c2",
            "causation_id": None,
            "dedup_key": "00000000-0000-4000-8000-0000000007a2:v1",
            "provenance_json": '{"source":"client"}',
            "rule_version": "v1",
        }
        assert application.repository.get(UUID(learner_id)).xp_total == 200
    finally:
        evidence.close()
        application.close()

    # Reopening an already migrated file adds nothing and keeps both rows.
    EvidenceStore(db).close()
    assert len(_columns(db)) == 16
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM delivery_ids").fetchone() == (2,)
    connection.close()


def test_backfilled_crash_gap_row_keeps_null_provenance(tmp_path, monkeypatch):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        envelope = _accepted_envelope(learner_id)

        def crash(*args, **kwargs):
            raise RuntimeError("simulated crash")

        monkeypatch.setattr(evidence, "bind_delivery", crash)
        with pytest.raises(RuntimeError, match="simulated crash"):
            _ingest(application, evidence, envelope)
        monkeypatch.undo()

        assert _ingest(application, evidence, envelope) == ("duplicate", "duplicate_event_id")
        row = _provenance_row(evidence, EVENT_ID)
        assert (row["action_id"], row["digest"]) == ("cmp-A", canonical_digest(envelope))
        assert {column: row[column] for column in PROVENANCE_COLUMNS} == {
            column: None for column in PROVENANCE_COLUMNS
        }
        # The domain fact was saved before the crash, so it does carry provenance.
        [stored] = _lesson_events(application, learner_id)
        assert stored.correlation_id == "00000000-0000-4000-8000-0000000007c1"
    finally:
        evidence.close()
        application.close()
