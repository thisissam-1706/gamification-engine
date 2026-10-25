"""Week 4 Fix 8b: new actions across a rule-version change, replay, late arrival.

Week 3 review row 26. One learner on one SQLite file:

    cmp-1  occurred 2026-09-01, accepted under v1  -> 100 XP
    cmp-2  occurred 2026-09-02, accepted under v1  -> 100 XP
    ---- config switched to v2 (notionally on 2026-09-04) ----
    cmp-3  occurred 2026-09-05, accepted under v2  -> 150 XP   (xp_total 350)
    restart; replay; redeliver cmp-1's message      -> duplicate, still 350
    cmp-4  occurred 2026-09-03 (before the switch),
           arrives after it, accepted under v2      -> 150 XP   (xp_total 500)

All expected values are written by hand.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from src.ingestion.config import DEFAULT_CONFIG, IngestConfig
from src.ingestion.evidence import EvidenceStore
from tests.ingestion.support import ingest_lesson_completed  # late_window=None
from src.ingestion.trace import XPAwardedFact, trace_delivery
from src.state_machine.learner import Learner, LearnerApplication
from src.state_machine.projection import rebuild_learner_state
from src.state_machine.rules import _REGISTRY, RULESETS, RuleSet
from tests.ingestion.test_ingestion_service import _envelope

EVENT_1 = "00000000-0000-4000-8000-0000000008b1"
EVENT_2 = "00000000-0000-4000-8000-0000000008b2"
EVENT_3 = "00000000-0000-4000-8000-0000000008b3"
EVENT_4 = "00000000-0000-4000-8000-0000000008b4"


def _register_v2(monkeypatch) -> IngestConfig:
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


def _lesson(
    learner_id: str,
    completion_id: str,
    event_id: str,
    version: str,
    occurred: datetime,
    received: datetime,
) -> dict:
    envelope = _envelope(learner_id, completion_id, event_id)
    envelope["occurred_at"] = occurred.isoformat()
    envelope["ingested_at"] = received.isoformat()
    envelope["dedup_key"] = f"{event_id}:{version}"
    return envelope


def _ingest(application, evidence, envelope, config):
    result = ingest_lesson_completed(
        application,
        evidence,
        envelope,
        authenticated_producer="learning-svc",
        config=config,
    )
    return result.status, result.reason


def _xp_awards(application, learner_id: str) -> list[tuple[str, int, str]]:
    return [
        (event.reward_id, event.xp_amount, event.rule_version)
        for event in application.events.get(UUID(learner_id))
        if isinstance(event, Learner.XPAwarded)
    ]


def _day(day: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, 9, minute, tzinfo=timezone.utc)


def test_actions_across_a_rule_version_change(tmp_path, monkeypatch):
    db = str(tmp_path / "events.db")

    # -- 1. two lessons under v1, then one under v2 ---------------------------
    application, evidence = _open(db)
    try:
        learner = Learner()
        application.save(learner)
        learner_id = str(learner.id)

        first = _lesson(learner_id, "cmp-1", EVENT_1, "v1", _day(1), _day(1, 1))
        second = _lesson(learner_id, "cmp-2", EVENT_2, "v1", _day(2), _day(2, 1))
        assert _ingest(application, evidence, first, DEFAULT_CONFIG) == ("accepted", None)
        assert _ingest(application, evidence, second, DEFAULT_CONFIG) == ("accepted", None)

        v2 = _register_v2(monkeypatch)
        third = _lesson(learner_id, "cmp-3", EVENT_3, "v2", _day(5), _day(5, 1))
        assert _ingest(application, evidence, third, v2) == ("accepted", None)

        assert application.repository.get(UUID(learner_id)).xp_total == 350
        assert _xp_awards(application, learner_id) == [
            ("xp:cmp-1", 100, "v1"),
            ("xp:cmp-2", 100, "v1"),
            ("xp:cmp-3", 150, "v2"),
        ]
    finally:
        evidence.close()
        application.close()
    del application, evidence, learner

    # -- 2. restart; aggregate and pure replay agree; history not recomputed --
    # Make v1 worth 999 XP during the replay: if anything re-derived the old
    # awards from the rule set, the total would move away from 350.
    original_v1 = RULESETS["v1"]
    monkeypatch.setitem(
        _REGISTRY,
        "v1",
        RuleSet(
            xp_amount=999,
            quest_targets=dict(original_v1.quest_targets),
            badge_threshold=original_v1.badge_threshold,
            badge_id=original_v1.badge_id,
        ),
    )
    application, evidence = _open(db)
    try:
        assert application.repository.get(UUID(learner_id)).xp_total == 350
        assert rebuild_learner_state(application.events.get(UUID(learner_id))).xp_total == 350
        assert _xp_awards(application, learner_id)[:2] == [
            ("xp:cmp-1", 100, "v1"),
            ("xp:cmp-2", 100, "v1"),
        ]
        monkeypatch.setitem(_REGISTRY, "v1", original_v1)

        # -- 3. redeliver the first v1 message while v2 is configured --------
        events_before = list(application.events.get(UUID(learner_id)))
        assert first["dedup_key"].endswith(":v1")
        assert _ingest(application, evidence, dict(first), v2) == (
            "duplicate",
            "duplicate_event_id",
        )
        assert list(application.events.get(UUID(learner_id))) == events_before
        assert application.repository.get(UUID(learner_id)).xp_total == 350

        # -- 4. late arrival: occurred before the switch, accepted after it ---
        late = _lesson(learner_id, "cmp-4", EVENT_4, "v2", _day(3), _day(6))
        assert late["occurred_at"] == "2026-09-03T09:00:00+00:00"
        assert _ingest(application, evidence, late, v2) == ("accepted", None)
        assert application.repository.get(UUID(learner_id)).xp_total == 500
        assert _xp_awards(application, learner_id) == [
            ("xp:cmp-1", 100, "v1"),
            ("xp:cmp-2", 100, "v1"),
            ("xp:cmp-3", 150, "v2"),
            ("xp:cmp-4", 150, "v2"),
        ]

        # -- 5. each delivery traces to its own version -----------------------
        expected = {
            EVENT_1: ("cmp-1", "v1", XPAwardedFact("xp:cmp-1", 100, "v1")),
            EVENT_2: ("cmp-2", "v1", XPAwardedFact("xp:cmp-2", 100, "v1")),
            EVENT_3: ("cmp-3", "v2", XPAwardedFact("xp:cmp-3", 150, "v2")),
            EVENT_4: ("cmp-4", "v2", XPAwardedFact("xp:cmp-4", 150, "v2")),
        }
        for event_id, (action_id, version, award) in expected.items():
            trace = trace_delivery(application, evidence, event_id)
            assert trace is not None, event_id
            assert trace.provenance.action_id == action_id
            assert trace.provenance.rule_version == version
            assert trace.provenance.dedup_key == f"{event_id}:{version}"
            assert trace.xp_awards == (award,)

        first_trace = trace_delivery(application, evidence, EVENT_1)
        assert [(row.kind, row.reason) for row in first_trace.evidence] == [
            ("duplicate", "duplicate_event_id")
        ]
        late_trace = trace_delivery(application, evidence, EVENT_4)
        assert late_trace.provenance.occurred_at == "2026-09-03T09:00:00+00:00"
        assert late_trace.provenance.ingested_at == "2026-09-06T09:00:00+00:00"
    finally:
        evidence.close()
        application.close()


def test_unregistered_version_cannot_be_configured_after_teardown():
    """The v2 registration above is per test; it does not leak."""
    assert "v2" not in RULESETS
    assert RULESETS["v1"].xp_amount == 100
    with pytest.raises(ValueError):
        IngestConfig("v2")
