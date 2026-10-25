"""Week 4 Fix 7b: read-only trace from source delivery to reward (to exposure).

Week 3 review row 26 asks to trace source -> reward -> exposure. Expected values
below are written by hand from the scenario, not read back from the code.
"""

from datetime import datetime
import hashlib
from pathlib import Path
import sqlite3
from uuid import UUID, uuid4

from src.ingestion.evidence import EvidenceStore
from src.ingestion.service import canonical_digest
from tests.ingestion.support import ingest_lesson_completed  # late_window=None
from src.ingestion.trace import (
    DeliveryProvenance,
    LessonCompletedFact,
    XPAwardedFact,
    trace_delivery,
)
from src.state_machine.learner import Learner, LearnerApplication
from tests.ingestion.test_ingestion_service import _envelope

EVENT_ID = "00000000-0000-4000-8000-0000000007b1"
CORRELATION_ID = "00000000-0000-4000-8000-0000000007c1"


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


def _file_sha256(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _run_scenario(db: str) -> tuple[str, dict]:
    """Accept cmp-A, resend it unchanged, then reuse its event_id for cmp-B."""
    application, evidence = _open(db)
    try:
        learner = Learner()
        application.save(learner)  # originator_version 1
        learner_id = str(learner.id)
        first = _envelope(learner_id, "cmp-A", EVENT_ID)
        first["correlation_id"] = CORRELATION_ID
        assert _ingest(application, evidence, first) == ("accepted", None)
        assert _ingest(application, evidence, dict(first)) == (
            "duplicate",
            "duplicate_event_id",
        )
        assert _ingest(application, evidence, _envelope(learner_id, "cmp-B", EVENT_ID)) == (
            "rejected",
            "conflicting_event_id_reuse",
        )
    finally:
        evidence.close()
        application.close()
    return learner_id, first


def _assert_scenario_trace(application, evidence, learner_id, first):
    trace = trace_delivery(application, evidence, EVENT_ID)
    assert trace is not None
    assert trace.provenance == DeliveryProvenance(
        event_id="00000000-0000-4000-8000-0000000007b1",
        learner_id=learner_id,
        action_id="cmp-A",
        digest=canonical_digest(first),
        producer="learning-svc",
        event_type="learning.lesson_completed",
        schema_version=1,
        occurred_at="2026-09-01T00:00:00+00:00",
        ingested_at="2026-09-01T00:01:00+00:00",
        correlation_id="00000000-0000-4000-8000-0000000007c1",
        causation_id=None,
        dedup_key="00000000-0000-4000-8000-0000000007b1:v1",
        rule_version="v1",
        rule_digest="ba6d0a3821ebdb1358840ec7a0dd2f67136b4eeb3062d79f3369b53cda6b4430",
    )
    # Learner created at version 1; LessonCompleted is 2, XPAwarded is 3.
    assert trace.lesson_completed == LessonCompletedFact(
        originator_version=2,
        source_event_id="00000000-0000-4000-8000-0000000007b1",
    )
    assert trace.xp_awards == (
        XPAwardedFact(reward_id="xp:cmp-A", amount=100, rule_version="v1"),
    )
    assert [
        (row.id, row.kind, row.learner_id, row.action_id, row.event_id, row.reason, row.original_ref)
        for row in trace.evidence
    ] == [
        (
            1,
            "duplicate",
            learner_id,
            "cmp-A",
            "00000000-0000-4000-8000-0000000007b1",
            "duplicate_event_id",
            "00000000-0000-4000-8000-0000000007b1",
        ),
        (
            2,
            "rejected",
            learner_id,
            "cmp-B",
            "00000000-0000-4000-8000-0000000007b1",
            "conflicting_event_id_reuse",
            "cmp-A",
        ),
    ]
    for row in trace.evidence:
        assert datetime.fromisoformat(row.received_at).tzinfo is not None
    assert trace.exposures == ()
    return trace


def test_trace_follows_one_delivery_to_its_reward_and_evidence(tmp_path):
    db = str(tmp_path / "events.db")
    learner_id, first = _run_scenario(db)
    application, evidence = _open(db)
    try:
        events_before = list(application.events.get(UUID(learner_id)))
        changes_before = evidence.connection.total_changes
        file_before = _file_sha256(db)

        _assert_scenario_trace(application, evidence, learner_id, first)

        # Read-only: no evidence write, no new event, the file is byte-identical.
        assert evidence.connection.total_changes == changes_before
        assert list(application.events.get(UUID(learner_id))) == events_before
        assert _file_sha256(db) == file_before
        assert application.repository.get(UUID(learner_id)).xp_total == 100
    finally:
        evidence.close()
        application.close()


def test_trace_is_identical_after_restart(tmp_path):
    db = str(tmp_path / "events.db")
    learner_id, first = _run_scenario(db)

    application, evidence = _open(db)
    try:
        before_restart = _assert_scenario_trace(application, evidence, learner_id, first)
    finally:
        evidence.close()
        application.close()
    del application, evidence

    restarted, restarted_evidence = _open(db)
    try:
        after_restart = _assert_scenario_trace(
            restarted, restarted_evidence, learner_id, first
        )
        assert after_restart == before_restart
    finally:
        restarted_evidence.close()
        restarted.close()


def test_unknown_event_id_traces_to_none(tmp_path):
    db = str(tmp_path / "events.db")
    _run_scenario(db)
    application, evidence = _open(db)
    try:
        file_before = _file_sha256(db)
        assert trace_delivery(application, evidence, "00000000-0000-4000-8000-0000000007ff") is None
        assert _file_sha256(db) == file_before
    finally:
        evidence.close()
        application.close()


def test_resend_under_a_new_event_id_is_traced_through_original_ref(tmp_path):
    db = str(tmp_path / "events.db")
    application, evidence = _open(db)
    try:
        learner = Learner()
        application.save(learner)
        learner_id = str(learner.id)
        assert _ingest(application, evidence, _envelope(learner_id, "cmp-A", EVENT_ID)) == (
            "accepted",
            None,
        )
        resend_event_id = str(uuid4())
        assert _ingest(
            application, evidence, _envelope(learner_id, "cmp-A", resend_event_id)
        ) == ("duplicate", "duplicate_completion_id")

        trace = trace_delivery(application, evidence, EVENT_ID)
        assert trace is not None
        assert [(row.reason, row.event_id, row.original_ref) for row in trace.evidence] == [
            ("duplicate_completion_id", resend_event_id, "00000000-0000-4000-8000-0000000007b1")
        ]
        # The resend was never bound, so it has no trace of its own.
        assert trace_delivery(application, evidence, resend_event_id) is None
    finally:
        evidence.close()
        application.close()


def test_delivery_that_predates_provenance_still_traces(tmp_path):
    db = str(tmp_path / "events.db")
    application, evidence = _open(db)
    try:
        learner = Learner()
        application.save(learner)
        learner_id = str(learner.id)
        assert _ingest(application, evidence, _envelope(learner_id, "cmp-A", EVENT_ID)) == (
            "accepted",
            None,
        )
    finally:
        evidence.close()
        application.close()

    # Back to a pre-Fix-6a file: only accepted_deliveries knew the delivery.
    with sqlite3.connect(db) as connection:
        connection.execute("DROP TABLE delivery_ids")
    connection.close()

    application, evidence = _open(db)
    try:
        trace = trace_delivery(application, evidence, EVENT_ID)
        assert trace is not None
        assert trace.provenance == DeliveryProvenance(
            event_id="00000000-0000-4000-8000-0000000007b1",
            learner_id=learner_id,
            action_id="cmp-A",
            digest=None,
            producer=None,
            event_type=None,
            schema_version=None,
            occurred_at=None,
            ingested_at=None,
            correlation_id=None,
            causation_id=None,
            dedup_key=None,
            rule_version=None,
            rule_digest=None,
        )
        assert trace.lesson_completed == LessonCompletedFact(
            originator_version=2,
            source_event_id="00000000-0000-4000-8000-0000000007b1",
        )
        assert trace.xp_awards == (
            XPAwardedFact(reward_id="xp:cmp-A", amount=100, rule_version="v1"),
        )
        assert trace.evidence == ()
        assert trace.exposures == ()
    finally:
        evidence.close()
        application.close()
