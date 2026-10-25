"""Week 4 Fix 9b: the accepted rule set's content digest is persisted.

delivery_ids.rule_digest records get_ruleset(config.rule_version).digest at
acceptance, so a provenance row names the exact rule content that decided it,
not only the version label.
"""

import sqlite3
from uuid import UUID

import pytest

from src.ingestion.config import IngestConfig
from src.ingestion.evidence import EvidenceStore
from tests.ingestion.support import ingest_lesson_completed  # late_window=None
from src.ingestion.trace import trace_delivery
from src.state_machine.learner import Learner, LearnerApplication
from src.state_machine.rules import _REGISTRY, RULESETS, RuleSet
from tests.ingestion.test_ingestion_service import _envelope

# v1 digest, pinned in tests/state_machine/test_rules_immutable.py as well.
V1_DIGEST = "ba6d0a3821ebdb1358840ec7a0dd2f67136b4eeb3062d79f3369b53cda6b4430"
# Digest of the test-only v2: xp_amount 150, v1's quest targets, 7, week_warrior_v1.
V2_DIGEST = "ced6f893cae47f7abb7fbec4edf98daf5ac953b491f2e5278a282a2a336e1b8b"

EVENT_ID = "00000000-0000-4000-8000-0000000009b1"


def _open(db: str) -> tuple[LearnerApplication, EvidenceStore]:
    application = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
    )
    return application, EvidenceStore(db)


def _ingest(application, evidence, envelope, **kwargs):
    result = ingest_lesson_completed(
        application, evidence, envelope, authenticated_producer="learning-svc", **kwargs
    )
    return result.status, result.reason


def _new_learner(application: LearnerApplication) -> str:
    learner = Learner()
    application.save(learner)
    return str(learner.id)


def _columns(db: str) -> list[str]:
    with sqlite3.connect(db) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(delivery_ids)")]
    connection.close()
    return columns


def test_accepted_delivery_records_v1_digest(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        assert _ingest(application, evidence, _envelope(learner_id, "cmp-A", EVENT_ID)) == (
            "accepted",
            None,
        )
        assert RULESETS["v1"].digest == V1_DIGEST
        assert evidence.delivery_binding(EVENT_ID).rule_digest == V1_DIGEST
        trace = trace_delivery(application, evidence, EVENT_ID)
        assert (trace.provenance.rule_version, trace.provenance.rule_digest) == ("v1", V1_DIGEST)
    finally:
        evidence.close()
        application.close()


def test_delivery_under_v2_records_v2_digest(tmp_path, monkeypatch):
    monkeypatch.setitem(
        _REGISTRY,
        "v2",
        RuleSet(
            xp_amount=150,
            quest_targets=dict(RULESETS["v1"].quest_targets),
            badge_threshold=7,
            badge_id="week_warrior_v1",
        ),
    )
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        v1_event_id = "00000000-0000-4000-8000-0000000009b2"
        assert _ingest(application, evidence, _envelope(learner_id, "cmp-A", v1_event_id)) == (
            "accepted",
            None,
        )
        v2_event = _envelope(learner_id, "cmp-B", EVENT_ID)
        v2_event["dedup_key"] = f"{EVENT_ID}:v2"
        assert _ingest(application, evidence, v2_event, config=IngestConfig("v2")) == (
            "accepted",
            None,
        )

        assert RULESETS["v2"].digest == V2_DIGEST != V1_DIGEST
        assert evidence.delivery_binding(EVENT_ID).rule_digest == V2_DIGEST
        assert evidence.delivery_binding(v1_event_id).rule_digest == V1_DIGEST
        trace = trace_delivery(application, evidence, EVENT_ID)
        assert (trace.provenance.rule_version, trace.provenance.rule_digest) == ("v2", V2_DIGEST)
        assert application.repository.get(UUID(learner_id)).xp_total == 250
    finally:
        evidence.close()
        application.close()


def test_pre_change_database_opens_with_null_rule_digest(tmp_path):
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

    # Back to the Fix 9a schema: delivery_ids without rule_digest.
    with sqlite3.connect(db) as connection:
        connection.execute("ALTER TABLE delivery_ids DROP COLUMN rule_digest")
    connection.close()
    assert "rule_digest" not in _columns(db)

    application, evidence = _open(db)
    try:
        assert _columns(db)[-1] == "rule_digest"
        old = evidence.delivery_binding(EVENT_ID)
        assert (old.action_id, old.rule_version, old.rule_digest) == ("cmp-A", "v1", None)
        assert trace_delivery(application, evidence, EVENT_ID).provenance.rule_digest is None

        later_event_id = "00000000-0000-4000-8000-0000000009b3"
        assert _ingest(
            application, evidence, _envelope(learner_id, "cmp-later", later_event_id)
        ) == ("accepted", None)
        assert evidence.delivery_binding(later_event_id).rule_digest == V1_DIGEST
    finally:
        evidence.close()
        application.close()

    EvidenceStore(db).close()
    assert _columns(db).count("rule_digest") == 1


def test_migrated_and_backfilled_rows_keep_null_rule_digest(tmp_path, monkeypatch):
    db = str(tmp_path / "events.db")
    application, evidence = _open(db)
    try:
        learner_id = _new_learner(application)

        def crash(*args, **kwargs):
            raise RuntimeError("simulated crash")

        crashed = _envelope(learner_id, "cmp-crash", EVENT_ID)
        monkeypatch.setattr(evidence, "bind_delivery", crash)
        with pytest.raises(RuntimeError, match="simulated crash"):
            _ingest(application, evidence, crashed)
        monkeypatch.undo()
        assert _ingest(application, evidence, crashed) == ("duplicate", "duplicate_event_id")
        assert evidence.delivery_binding(EVENT_ID).rule_digest is None

        migrated_event_id = "00000000-0000-4000-8000-0000000009b4"
        assert _ingest(
            application, evidence, _envelope(learner_id, "cmp-migrated", migrated_event_id)
        ) == ("accepted", None)
    finally:
        evidence.close()
        application.close()

    # A pre-Fix-6a file: the binding is rebuilt from accepted_deliveries only.
    with sqlite3.connect(db) as connection:
        connection.execute("DROP TABLE delivery_ids")
    connection.close()
    evidence = EvidenceStore(db)
    try:
        assert evidence.delivery_binding(migrated_event_id).rule_digest is None
    finally:
        evidence.close()
