"""Week 4 Fix 8a: the rule version is a trusted ingress setting.

Week 3 review row 26: the live ingestion route always selected rule v1. The
version now comes from IngestConfig, is applied to the XP decision, is bound
into the dedup_key of new events, and is persisted on the provenance row.
"""

import sqlite3
from uuid import UUID, uuid4

import pytest
from eventsourcing.persistence import IntegrityError

from src.ingestion.config import DEFAULT_CONFIG, IngestConfig
from src.ingestion.evidence import EvidenceStore
from tests.ingestion.support import ingest_lesson_completed  # late_window=None
from src.state_machine.learner import Learner, LearnerApplication
from src.state_machine.rules import _REGISTRY, RULESETS, RuleSet
from tests.ingestion.test_ingestion_service import _envelope

EVENT_ID = "00000000-0000-4000-8000-0000000008a1"


@pytest.fixture
def rule_v2(monkeypatch):
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
    return IngestConfig("v2")


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


def _keyed(learner_id: str, completion_id: str, event_id: str, version: str) -> dict:
    envelope = _envelope(learner_id, completion_id, event_id)
    envelope["dedup_key"] = f"{event_id}:{version}"
    return envelope


def _xp_awards(application, learner_id: str) -> list[tuple[str, int, str]]:
    return [
        (event.reward_id, event.xp_amount, event.rule_version)
        for event in application.events.get(UUID(learner_id))
        if isinstance(event, Learner.XPAwarded)
    ]


def test_ingest_config_rejects_an_unknown_rule_version():
    with pytest.raises(ValueError, match="Unknown rule version: v9"):
        IngestConfig("v9")
    assert IngestConfig("v1").rule_version == "v1"
    assert DEFAULT_CONFIG == IngestConfig("v1")


def test_ingest_config_is_frozen():
    with pytest.raises(AttributeError):
        DEFAULT_CONFIG.rule_version = "v2"  # type: ignore[misc]


def test_default_behaviour_is_unchanged(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        envelope = _envelope(learner_id, "cmp-A", EVENT_ID)
        assert envelope["dedup_key"] == "00000000-0000-4000-8000-0000000008a1:v1"

        assert _ingest(application, evidence, envelope) == ("accepted", None)

        assert application.repository.get(UUID(learner_id)).xp_total == 100
        assert _xp_awards(application, learner_id) == [("xp:cmp-A", 100, "v1")]
        assert evidence.delivery_binding(EVENT_ID).rule_version == "v1"
    finally:
        evidence.close()
        application.close()


def test_configured_rule_set_is_applied_and_bound_into_new_keys(tmp_path, rule_v2):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        v2_event = _keyed(learner_id, "cmp-A", EVENT_ID, "v2")
        assert v2_event["dedup_key"] == "00000000-0000-4000-8000-0000000008a1:v2"

        assert _ingest(application, evidence, v2_event, config=rule_v2) == ("accepted", None)
        assert application.repository.get(UUID(learner_id)).xp_total == 150
        assert _xp_awards(application, learner_id) == [("xp:cmp-A", 150, "v2")]
        assert evidence.delivery_binding(EVENT_ID).rule_version == "v2"

        events_before = list(application.events.get(UUID(learner_id)))
        v1_event_id = "00000000-0000-4000-8000-0000000008a2"
        v1_event = _keyed(learner_id, "cmp-B", v1_event_id, "v1")
        assert _ingest(application, evidence, v1_event, config=rule_v2) == (
            "rejected",
            "dedup_key_mismatch",
        )
        assert application.repository.get(UUID(learner_id)).xp_total == 150
        assert list(application.events.get(UUID(learner_id))) == events_before
        assert [
            (row.kind, row.reason, row.action_id, row.event_id)
            for row in evidence.list(learner_id)
        ] == [("rejected", "dedup_key_mismatch", "cmp-B", v1_event_id)]
        assert evidence.delivery_binding(v1_event_id) is None
    finally:
        evidence.close()
        application.close()


def test_old_message_redelivered_after_config_change_is_a_duplicate(tmp_path, rule_v2):
    db = str(tmp_path / "events.db")
    application, evidence = _open(db)
    try:
        learner_id = _new_learner(application)
        original = _envelope(learner_id, "cmp-A", EVENT_ID)
        assert _ingest(application, evidence, original) == ("accepted", None)
    finally:
        evidence.close()
        application.close()

    # Redeploy with v2 configured, then the producer redelivers the old message.
    application, evidence = _open(db)
    try:
        assert _ingest(application, evidence, dict(original), config=rule_v2) == (
            "duplicate",
            "duplicate_event_id",
        )
        # The bound version decides the key: re-keying it as v2 is a mismatch.
        rekeyed = _keyed(learner_id, "cmp-A", EVENT_ID, "v2")
        assert _ingest(application, evidence, rekeyed, config=rule_v2) == (
            "rejected",
            "dedup_key_mismatch",
        )
        assert application.repository.get(UUID(learner_id)).xp_total == 100
        assert _xp_awards(application, learner_id) == [("xp:cmp-A", 100, "v1")]
        assert evidence.delivery_binding(EVENT_ID).rule_version == "v1"
        assert [(row.kind, row.reason) for row in evidence.list(learner_id)] == [
            ("duplicate", "duplicate_event_id"),
            ("rejected", "dedup_key_mismatch"),
        ]
    finally:
        evidence.close()
        application.close()


def test_row_without_rule_version_falls_back_to_the_config(tmp_path, rule_v2):
    """A pre-Fix-7a row has rule_version NULL, so the configured version applies."""
    db = str(tmp_path / "events.db")
    application, evidence = _open(db)
    try:
        learner_id = _new_learner(application)
        original = _envelope(learner_id, "cmp-A", EVENT_ID)
        assert _ingest(application, evidence, original) == ("accepted", None)
    finally:
        evidence.close()
        application.close()

    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE delivery_ids SET rule_version = NULL")
    connection.close()

    application, evidence = _open(db)
    try:
        assert evidence.delivery_binding(EVENT_ID).rule_version is None
        assert _ingest(application, evidence, dict(original), config=rule_v2) == (
            "rejected",
            "dedup_key_mismatch",
        )
        assert _ingest(application, evidence, dict(original)) == (
            "duplicate",
            "duplicate_event_id",
        )
        assert application.repository.get(UUID(learner_id)).xp_total == 100
    finally:
        evidence.close()
        application.close()


def test_earlier_rejection_reasons_keep_precedence_under_a_config(tmp_path, rule_v2):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        untrusted = _keyed(learner_id, "cmp-A", str(uuid4()), "v1")
        untrusted["producer"] = "assessment-svc"
        result = ingest_lesson_completed(
            application,
            evidence,
            untrusted,
            authenticated_producer="assessment-svc",
            config=rule_v2,
        )
        assert (result.status, result.reason) == ("rejected", "untrusted_producer")

        reward_claim = _keyed(learner_id, "cmp-B", str(uuid4()), "v1")
        reward_claim["payload"]["xp_amount"] = 999
        assert _ingest(application, evidence, reward_claim, config=rule_v2) == (
            "rejected",
            "reward_field_in_source_event",
        )
        assert application.repository.get(UUID(learner_id)).xp_total == 0
    finally:
        evidence.close()
        application.close()


def test_retry_after_integrity_error_uses_the_configured_version(
    tmp_path, rule_v2, monkeypatch
):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        original_save = application.save
        calls = 0

        def conflict_once(*aggregates):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise IntegrityError("simulated concurrent writer")
            return original_save(*aggregates)

        monkeypatch.setattr(application, "save", conflict_once)
        envelope = _keyed(learner_id, "cmp-A", EVENT_ID, "v2")
        assert _ingest(application, evidence, envelope, config=rule_v2) == ("accepted", None)

        assert calls == 2
        assert _xp_awards(application, learner_id) == [("xp:cmp-A", 150, "v2")]
        assert evidence.delivery_binding(EVENT_ID).rule_version == "v2"
    finally:
        evidence.close()
        application.close()
