"""Week 4 Fix 11b: late-event behaviour on one SQLite file with a fixed clock.

Acceptance case "Out-of-Order Within 72h", then an out-of-window delivery after
a restart. Every expected value was derived by hand from
src/state_machine/rules.py and Learner.complete_lesson before the first run;
the derivation is in docs/audit/week4-fix11b-late-behaviour.md.

    A  cmp-0922  occurred 2026-09-22T10:00Z  received 2026-09-22T10:05Z  ->  accepted
    B  cmp-0921  occurred 2026-09-21T10:00Z  received 2026-09-24T09:00Z  ->  accepted (71h)
       restart
    C  cmp-0922b occurred 2026-09-22T11:00Z  received 2026-09-25T12:00Z  ->  rejected (73h)
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from src.ingestion.config import IngestConfig
from src.ingestion.evidence import EvidenceStore
from src.ingestion.service import ingest_lesson_completed
from src.ingestion.trace import trace_delivery
from src.state_machine.learner import Learner, LearnerApplication
from src.state_machine.projection import rebuild_learner_state
from tests.ingestion.test_ingestion_service import _envelope

EVENT_A = "00000000-0000-4000-8000-000000000c01"
EVENT_B = "00000000-0000-4000-8000-000000000c02"
EVENT_C = "00000000-0000-4000-8000-000000000c03"


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _open(db: str) -> tuple[LearnerApplication, EvidenceStore]:
    application = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
    )
    return application, EvidenceStore(db)


def _lesson(learner_id: str, completion_id: str, event_id: str, occurred_at: str, ingested_at: str) -> dict:
    envelope = _envelope(learner_id, completion_id, event_id)
    envelope["occurred_at"] = occurred_at
    envelope["ingested_at"] = ingested_at
    assert envelope["payload"]["timezone"] == "Asia/Kolkata"
    return envelope


def _ingest(application, evidence, envelope, clock):
    result = ingest_lesson_completed(
        application,
        evidence,
        envelope,
        authenticated_producer="learning-svc",
        config=IngestConfig("v1", late_window=timedelta(hours=72), clock=clock),
    )
    return result.status, result.reason


def _streak_facts(application, learner_id: str) -> list[tuple]:
    return [
        (
            event.originator_version,
            event.completion_id,
            event.activity_date,
            event.new_streak,
            event.freezes_remaining,
            event.freezes_consumed,
            event.previous_streak,
            event.revision,
            event.supersedes,
            event.rule_version,
        )
        for event in application.events.get(UUID(learner_id))
        if isinstance(event, Learner.StreakUpdated)
    ]


def test_late_event_behaviour_across_the_window(tmp_path):
    db = str(tmp_path / "events.db")
    clock = FixedClock(datetime(2026, 9, 22, 10, 5, tzinfo=timezone.utc))

    # -- 1. out-of-order arrival within 72h ---------------------------------------
    application, evidence = _open(db)
    try:
        learner = Learner()
        application.save(learner)  # originator_version 1
        learner_id = str(learner.id)

        a = _lesson(learner_id, "cmp-0922", EVENT_A, "2026-09-22T10:00:00+00:00", "2026-09-22T10:01:00+00:00")
        assert _ingest(application, evidence, a, clock) == ("accepted", None)

        clock.now = datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc)  # 71h after B occurred
        # The envelope's ingested_at is provenance only; far outside the window here.
        b = _lesson(learner_id, "cmp-0921", EVENT_B, "2026-09-21T10:00:00+00:00", "2026-10-30T00:00:00+00:00")
        assert _ingest(application, evidence, b, clock) == ("accepted", None)

        assert _streak_facts(application, learner_id) == [
            (4, "cmp-0922", "2026-09-22", 1, 0, 0, 0, 1, None, "v1"),
            (7, "cmp-0921", "2026-09-21", 1, 0, 0, 1, 1, None, "v1"),
            (
                8,
                "cmp-0921",
                "2026-09-22",
                2,
                0,
                0,
                0,
                2,
                f"streak:{learner_id}:2026-09-22:1",
                "v1",
            ),
        ]
        events = list(application.events.get(UUID(learner_id)))
        assert [type(event).__name__ for event in events] == [
            "Created",
            "LessonCompleted",
            "XPAwarded",
            "StreakUpdated",
            "LessonCompleted",
            "XPAwarded",
            "StreakUpdated",
            "StreakUpdated",
        ]
        state = application.repository.get(UUID(learner_id))
        assert state.xp_total == 200
        assert state.new_streak == 2
        assert state.freezes_remaining == 0
        assert state.last_activity_date == "2026-09-22"
        assert state.activity_dates == {"2026-09-21", "2026-09-22"}
        assert state.streak_revisions == {"2026-09-21": 1, "2026-09-22": 2}
        assert state.streak_before_break is None
        assert state.badges_issued == {}
        replayed = rebuild_learner_state(events)
        assert (replayed.xp_total, replayed.new_streak, replayed.freezes_remaining) == (200, 2, 0)
        assert replayed.activity_dates == ("2026-09-21", "2026-09-22")

        # -- 3. provenance rows carry the clock's receive time --------------------------
        assert evidence.delivery_binding(EVENT_A).received_at == "2026-09-22T10:05:00+00:00"
        assert evidence.delivery_binding(EVENT_B).received_at == "2026-09-24T09:00:00+00:00"
        assert evidence.list(learner_id) == []
    finally:
        evidence.close()
        application.close()
    del application, evidence

    # -- 2. out-of-window input after a restart -----------------------------------
    application, evidence = _open(db)
    try:
        events_before = list(application.events.get(UUID(learner_id)))
        assert len(events_before) == 8

        clock.now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)  # 73h after C occurred
        c = _lesson(learner_id, "cmp-0922b", EVENT_C, "2026-09-22T11:00:00+00:00", "2026-09-22T11:01:00+00:00")
        assert _ingest(application, evidence, c, clock) == ("rejected", "outside_late_window")

        assert list(application.events.get(UUID(learner_id))) == events_before
        state = application.repository.get(UUID(learner_id))
        assert state.xp_total == 200
        assert state.new_streak == 2
        assert state.streak_revisions == {"2026-09-21": 1, "2026-09-22": 2}

        assert trace_delivery(application, evidence, EVENT_C) is None
        assert evidence.delivery_binding(EVENT_C) is None
        assert [
            (row.kind, row.reason, row.action_id, row.event_id, row.original_ref, row.received_at)
            for row in evidence.list(learner_id)
        ] == [
            (
                "rejected",
                "outside_late_window",
                "cmp-0922b",
                "00000000-0000-4000-8000-000000000c03",
                None,
                "2026-09-25T12:00:00+00:00",
            )
        ]
        # The accepted deliveries are untouched by the restart and the rejection.
        assert evidence.delivery_binding(EVENT_A).received_at == "2026-09-22T10:05:00+00:00"
        assert trace_delivery(application, evidence, EVENT_B).provenance.action_id == "cmp-0921"
    finally:
        evidence.close()
        application.close()
