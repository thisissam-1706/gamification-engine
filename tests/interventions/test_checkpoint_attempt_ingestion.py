"""M4: ingestion of learning.checkpoint_attempted, v1.

Written before the implementation. Assumed new API (see the Week 6 M4 step 1 audit):
``src.ingestion.service.ingest_checkpoint_attempted`` (same signature as
``ingest_lesson_completed``), ``Learner.record_checkpoint_attempt`` emitting one
``CheckpointAttempted`` event, ``Learner.checkpoint_attempts`` (attempt_id -> stored
fields) and ``Learner.first_checkpoint_attempt_ids`` (assessment_id -> attempt_id).
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from src.ingestion.config import DEFAULT_CONFIG, IngestConfig
from src.ingestion.evidence import EvidenceStore
from src.ingestion.validation import DEFAULT_INGEST_RULE_VERSION, expected_dedup_key
from src.state_machine.learner import Learner, LearnerApplication
from src.state_machine.projection import rebuild_learner_state
from tests.ingestion.support import ingest_lesson_completed  # late_window=None
from tests.ingestion.test_ingestion_service import _envelope as _lesson_envelope

# Fixed 2026-09 timestamps are far outside the real-clock 72h window.
UNBOUNDED_CONFIG = replace(DEFAULT_CONFIG, late_window=None)
OCCURRED = datetime(2026, 9, 2, 9, 0, tzinfo=timezone.utc)


def _open(db: str) -> tuple[LearnerApplication, EvidenceStore]:
    application = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
    )
    return application, EvidenceStore(db)


def _new_learner(application: LearnerApplication) -> str:
    learner = Learner()
    application.save(learner)
    return str(learner.id)


def _checkpoint_envelope(
    learner_id: str,
    attempt_id: str,
    event_id: str | None = None,
    *,
    occurred_at: datetime = OCCURRED,
    assessment_id: str = "checkpoint_math_v1",
):
    event_id = event_id or str(uuid4())
    return {
        "event_id": event_id,
        "event_type": "learning.checkpoint_attempted",
        "schema_version": 1,
        "occurred_at": occurred_at.isoformat(),
        "ingested_at": (occurred_at + timedelta(minutes=1)).isoformat(),
        "subject_id": learner_id,
        "producer": "learning-svc",
        "correlation_id": str(uuid4()),
        "causation_id": None,
        "dedup_key": expected_dedup_key(event_id, DEFAULT_INGEST_RULE_VERSION),
        "payload": {
            "attempt_id": attempt_id,
            "assessment_id": assessment_id,
            "assessment_version": 1,
            "passed": False,
            "timezone": "Asia/Kolkata",
        },
        "provenance": {"source": "client"},
    }


def _ingest_checkpoint(application, evidence, envelope, config=UNBOUNDED_CONFIG):
    # Imported here so a missing feature fails each test with a clear ImportError.
    from src.ingestion.service import ingest_checkpoint_attempted

    return ingest_checkpoint_attempted(
        application,
        evidence,
        envelope,
        authenticated_producer="learning-svc",
        config=config,
    )


def _outcome(result) -> tuple[str, str | None]:
    return result.status, result.reason


def _clocked(now: datetime) -> IngestConfig:
    """The default 72 h window with a settable trusted clock."""
    return IngestConfig(DEFAULT_INGEST_RULE_VERSION, clock=lambda: now)


def _learner(application: LearnerApplication, learner_id: str) -> Learner:
    return application.repository.get(UUID(learner_id))


def _checkpoint_events(application: LearnerApplication, learner_id: str) -> list:
    return [
        event
        for event in application.events.get(UUID(learner_id))
        if type(event).__name__ == "CheckpointAttempted"
    ]


def test_valid_checkpoint_attempt_is_accepted_and_stored_once(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        envelope = _checkpoint_envelope(learner_id, "att-1")
        envelope["payload"]["passed"] = True
        envelope["payload"]["assessment_version"] = 3

        result = _ingest_checkpoint(application, evidence, envelope)

        assert (result.status, result.reason) == ("accepted", None)
        learner = _learner(application, learner_id)
        assert len(learner.checkpoint_attempts) == 1
        stored = learner.checkpoint_attempts["att-1"]
        assert stored["attempt_id"] == "att-1"
        assert stored["assessment_id"] == "checkpoint_math_v1"
        assert stored["assessment_version"] == 3
        assert stored["passed"] is True
        assert stored["timezone"] == "Asia/Kolkata"
        assert stored["occurred_at"] == envelope["occurred_at"]
        assert len(_checkpoint_events(application, learner_id)) == 1
        assert evidence.list(learner_id) == []
    finally:
        evidence.close()
        application.close()


@pytest.mark.parametrize(
    ("name", "mutate"),
    (
        ("missing_attempt_id", lambda payload: payload.pop("attempt_id")),
        ("passed_is_integer_1", lambda payload: payload.update(passed=1)),
        ("passed_is_string_true", lambda payload: payload.update(passed="true")),
        ("assessment_version_0", lambda payload: payload.update(assessment_version=0)),
        ("unknown_extra_field", lambda payload: payload.update(score=0.5)),
    ),
)
def test_invalid_checkpoint_payload_is_rejected_without_state_change(
    tmp_path, name, mutate
):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        envelope = _checkpoint_envelope(learner_id, "att-1")
        mutate(envelope["payload"])
        events_before = list(application.events.get(UUID(learner_id)))

        result = _ingest_checkpoint(application, evidence, envelope)

        assert (result.status, result.reason) == ("rejected", "schema_invalid"), name
        assert list(application.events.get(UUID(learner_id))) == events_before
        assert [row.reason for row in evidence.list(learner_id)] == ["schema_invalid"]
    finally:
        evidence.close()
        application.close()


def test_accepting_an_attempt_leaves_xp_streak_and_quests_unchanged(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        learner = _learner(application, learner_id)
        learner.assign_quest(
            "lesson_starter_v1",
            assignment_id="assignment-1",
            assigned_at="2026-08-25T00:00:00Z",
        )
        application.save(learner)
        lesson = _lesson_envelope(learner_id, "cmp-1")
        assert (
            ingest_lesson_completed(
                application, evidence, lesson, authenticated_producer="learning-svc"
            ).status
            == "accepted"
        )
        before = _learner(application, learner_id)
        assert before.xp_total > 0 and before.new_streak == 1
        assert before.quest_progress["assignment-1"] == 1
        events_before = list(application.events.get(UUID(learner_id)))

        # Next local day, so any streak or quest side effect would be visible.
        envelope = _checkpoint_envelope(
            learner_id, "att-1", occurred_at=OCCURRED + timedelta(days=1)
        )
        assert _ingest_checkpoint(application, evidence, envelope).status == "accepted"

        after = _learner(application, learner_id)
        assert after.xp_total == before.xp_total
        assert after.awarded_reward_ids == before.awarded_reward_ids
        assert after.new_streak == before.new_streak
        assert after.last_activity_date == before.last_activity_date
        assert after.activity_dates == before.activity_dates
        assert after.quest_progress == before.quest_progress
        assert after.completed_quest_ids == before.completed_quest_ids
        assert after.completed_lesson_count == before.completed_lesson_count
        new_events = list(application.events.get(UUID(learner_id)))[len(events_before):]
        assert [type(event).__name__ for event in new_events] == ["CheckpointAttempted"]
    finally:
        evidence.close()
        application.close()


def test_first_attempt_per_assessment_ignores_arrival_order(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        later = _checkpoint_envelope(
            learner_id, "att-later", occurred_at=OCCURRED + timedelta(hours=5)
        )
        earlier = _checkpoint_envelope(learner_id, "att-earlier", occurred_at=OCCURRED)
        other = _checkpoint_envelope(
            learner_id,
            "att-other",
            occurred_at=OCCURRED + timedelta(hours=9),
            assessment_id="checkpoint_science_v1",
        )
        for envelope in (later, other, earlier):  # the earlier attempt arrives last
            assert _ingest_checkpoint(application, evidence, envelope).status == "accepted"

        learner = _learner(application, learner_id)
        assert len(learner.checkpoint_attempts) == 3
        assert learner.first_checkpoint_attempt_ids == {
            "checkpoint_math_v1": "att-earlier",
            "checkpoint_science_v1": "att-other",
        }
    finally:
        evidence.close()
        application.close()


def test_first_attempt_ties_on_occurred_at_break_by_attempt_id(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        for attempt_id in ("att-b", "att-a"):  # same instant; "att-a" sorts first
            envelope = _checkpoint_envelope(learner_id, attempt_id)
            assert _ingest_checkpoint(application, evidence, envelope).status == "accepted"

        learner = _learner(application, learner_id)
        assert learner.first_checkpoint_attempt_ids == {"checkpoint_math_v1": "att-a"}
    finally:
        evidence.close()
        application.close()


# -- edge cases ------------------------------------------------------------------


def test_same_event_id_delivered_twice_is_a_duplicate_event(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        envelope = _checkpoint_envelope(learner_id, "att-1")
        assert _outcome(_ingest_checkpoint(application, evidence, envelope)) == (
            "accepted",
            None,
        )
        assert _outcome(_ingest_checkpoint(application, evidence, dict(envelope))) == (
            "duplicate",
            "duplicate_event_id",
        )

        assert len(_learner(application, learner_id).checkpoint_attempts) == 1
        assert len(_checkpoint_events(application, learner_id)) == 1
    finally:
        evidence.close()
        application.close()


def test_same_attempt_id_with_new_event_id_and_same_content_adds_no_event(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        first = _checkpoint_envelope(learner_id, "att-1")
        assert _ingest_checkpoint(application, evidence, first).status == "accepted"
        resend = _checkpoint_envelope(learner_id, "att-1")  # new event_id, same content

        assert _outcome(_ingest_checkpoint(application, evidence, resend)) == (
            "duplicate",
            "duplicate_attempt_id",
        )

        assert len(_checkpoint_events(application, learner_id)) == 1
        assert [(row.reason, row.original_ref) for row in evidence.list(learner_id)] == [
            ("duplicate_attempt_id", first["event_id"])
        ]
        # A duplicate delivery does not bind its own event_id.
        assert evidence.delivery_binding(resend["event_id"]) is None
    finally:
        evidence.close()
        application.close()


def test_same_attempt_id_with_new_event_id_and_changed_content_keeps_the_first(tmp_path):
    """Chosen behaviour (mirrors complete_lesson): a repeat attempt_id is a plain
    duplicate; its content is not compared and the first accepted content stays."""
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        first = _checkpoint_envelope(learner_id, "att-1")
        assert _ingest_checkpoint(application, evidence, first).status == "accepted"
        changed = _checkpoint_envelope(learner_id, "att-1")
        changed["payload"]["passed"] = True
        changed["payload"]["assessment_id"] = "checkpoint_science_v1"

        assert _outcome(_ingest_checkpoint(application, evidence, changed)) == (
            "duplicate",
            "duplicate_attempt_id",
        )

        learner = _learner(application, learner_id)
        assert len(_checkpoint_events(application, learner_id)) == 1
        assert learner.checkpoint_attempts["att-1"]["passed"] is False
        assert learner.checkpoint_attempts["att-1"]["assessment_id"] == "checkpoint_math_v1"
        assert learner.first_checkpoint_attempt_ids == {"checkpoint_math_v1": "att-1"}
        assert [(row.kind, row.reason) for row in evidence.list(learner_id)] == [
            ("duplicate", "duplicate_attempt_id")
        ]
    finally:
        evidence.close()
        application.close()


def test_received_more_than_72_hours_after_occurred_at_is_rejected(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        envelope = _checkpoint_envelope(learner_id, "att-1")
        late = OCCURRED + timedelta(hours=72, seconds=1)
        events_before = list(application.events.get(UUID(learner_id)))

        result = _ingest_checkpoint(application, evidence, envelope, _clocked(late))

        assert _outcome(result) == ("rejected", "outside_late_window")
        assert list(application.events.get(UUID(learner_id))) == events_before
        assert _checkpoint_events(application, learner_id) == []
        assert [
            (row.kind, row.reason, row.event_id, row.received_at)
            for row in evidence.list(learner_id)
        ] == [("rejected", "outside_late_window", envelope["event_id"], late.isoformat())]
        assert evidence.delivery_binding(envelope["event_id"]) is None
    finally:
        evidence.close()
        application.close()


def test_received_exactly_72_hours_after_occurred_at_is_accepted(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        envelope = _checkpoint_envelope(learner_id, "att-1")

        result = _ingest_checkpoint(
            application, evidence, envelope, _clocked(OCCURRED + timedelta(hours=72))
        )

        assert _outcome(result) == ("accepted", None)
        assert len(_checkpoint_events(application, learner_id)) == 1
    finally:
        evidence.close()
        application.close()


def test_wrong_dedup_key_rule_version_is_rejected(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        envelope = _checkpoint_envelope(learner_id, "att-1")
        envelope["dedup_key"] = expected_dedup_key(envelope["event_id"], "v2")

        assert _outcome(_ingest_checkpoint(application, evidence, envelope)) == (
            "rejected",
            "dedup_key_mismatch",
        )

        assert _checkpoint_events(application, learner_id) == []
        assert [row.reason for row in evidence.list(learner_id)] == ["dedup_key_mismatch"]
        assert evidence.delivery_binding(envelope["event_id"]) is None
    finally:
        evidence.close()
        application.close()


def test_replay_rebuilds_attempts_and_first_attempt_per_assessment(tmp_path):
    db = str(tmp_path / "events.db")
    application, evidence = _open(db)
    try:
        learner_id = _new_learner(application)
        for attempt_id, hours, assessment_id in (
            ("att-late", 5, "checkpoint_math_v1"),
            ("att-sci", 9, "checkpoint_science_v1"),
            ("att-early", 0, "checkpoint_math_v1"),
        ):
            envelope = _checkpoint_envelope(
                learner_id,
                attempt_id,
                occurred_at=OCCURRED + timedelta(hours=hours),
                assessment_id=assessment_id,
            )
            assert _ingest_checkpoint(application, evidence, envelope).status == "accepted"
        live = _learner(application, learner_id)
        attempts, first, sources = (
            dict(live.checkpoint_attempts),
            dict(live.first_checkpoint_attempt_ids),
            dict(live.checkpoint_sources),
        )
    finally:
        evidence.close()
        application.close()

    reopened, _ = _open(db)  # a fresh application folds the stored events again
    try:
        rebuilt = _learner(reopened, learner_id)
        assert rebuilt.checkpoint_attempts == attempts
        assert rebuilt.first_checkpoint_attempt_ids == first == {
            "checkpoint_math_v1": "att-early",
            "checkpoint_science_v1": "att-sci",
        }
        assert rebuilt.checkpoint_sources == sources
    finally:
        reopened.close()


def test_replay_with_lessons_and_attempts_keeps_xp_streak_and_attempts(tmp_path):
    db = str(tmp_path / "events.db")
    application, evidence = _open(db)
    try:
        learner_id = _new_learner(application)
        for index, day in enumerate((1, 2)):
            lesson = _lesson_envelope(learner_id, f"cmp-{index}")
            lesson["occurred_at"] = datetime(2026, 9, day, 9, tzinfo=timezone.utc).isoformat()
            assert (
                ingest_lesson_completed(
                    application, evidence, lesson, authenticated_producer="learning-svc"
                ).status
                == "accepted"
            )
        envelope = _checkpoint_envelope(learner_id, "att-1", occurred_at=OCCURRED)
        assert _ingest_checkpoint(application, evidence, envelope).status == "accepted"
        live = _learner(application, learner_id)
        assert live.xp_total > 0 and live.new_streak == 2
        before = (
            live.xp_total,
            live.new_streak,
            live.completed_lesson_count,
            dict(live.checkpoint_attempts),
            dict(live.first_checkpoint_attempt_ids),
        )
    finally:
        evidence.close()
        application.close()

    reopened, _ = _open(db)
    try:
        rebuilt = _learner(reopened, learner_id)
        assert (
            rebuilt.xp_total,
            rebuilt.new_streak,
            rebuilt.completed_lesson_count,
            dict(rebuilt.checkpoint_attempts),
            dict(rebuilt.first_checkpoint_attempt_ids),
        ) == before
        # The pure replay projection ignores attempts and agrees on XP and streak.
        state = rebuild_learner_state(reopened.events.get(UUID(learner_id)))
        assert (state.xp_total, state.new_streak) == (before[0], before[1])
    finally:
        reopened.close()


# -- cross-type collisions -------------------------------------------------------


def test_lesson_completion_id_and_checkpoint_attempt_id_do_not_collide(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        lesson = _lesson_envelope(learner_id, "same-123")
        checkpoint = _checkpoint_envelope(learner_id, "same-123")
        assert lesson["event_id"] != checkpoint["event_id"]

        lesson_result = ingest_lesson_completed(
            application, evidence, lesson, authenticated_producer="learning-svc"
        )
        checkpoint_result = _ingest_checkpoint(application, evidence, checkpoint)

        assert _outcome(lesson_result) == ("accepted", None)
        assert _outcome(checkpoint_result) == ("accepted", None)
        learner = _learner(application, learner_id)
        assert learner.completed_lesson_count == 1
        assert learner.completed_lesson_ids == {"same-123"}
        assert list(learner.checkpoint_attempts) == ["same-123"]
        # Neither delivery is evidence of a duplicate or rejection.
        assert evidence.list(learner_id) == []
        # Each type has its own action id in the delivery tables.
        assert evidence.delivery_binding(lesson["event_id"]).action_id == "same-123"
        assert (
            evidence.delivery_binding(checkpoint["event_id"]).action_id
            == "checkpoint_attempt:same-123"
        )
        assert evidence.original_event_id(learner_id, "same-123") == lesson["event_id"]
        assert (
            evidence.original_event_id(learner_id, "checkpoint_attempt:same-123")
            == checkpoint["event_id"]
        )
    finally:
        evidence.close()
        application.close()


def test_event_id_accepted_for_a_lesson_cannot_be_reused_for_a_checkpoint(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        lesson = _lesson_envelope(learner_id, "cmp-1")
        assert (
            ingest_lesson_completed(
                application, evidence, lesson, authenticated_producer="learning-svc"
            ).status
            == "accepted"
        )
        events_before = list(application.events.get(UUID(learner_id)))
        checkpoint = _checkpoint_envelope(learner_id, "att-1", lesson["event_id"])

        result = _ingest_checkpoint(application, evidence, checkpoint)

        assert _outcome(result) == ("rejected", "conflicting_event_id_reuse")
        assert list(application.events.get(UUID(learner_id))) == events_before
        assert _learner(application, learner_id).checkpoint_attempts == {}
        assert [
            (row.kind, row.reason, row.action_id, row.event_id, row.original_ref)
            for row in evidence.list(learner_id)
        ] == [
            (
                "rejected",
                "conflicting_event_id_reuse",
                "checkpoint_attempt:att-1",
                lesson["event_id"],
                "cmp-1",
            )
        ]
        # The lesson's binding is untouched.
        assert evidence.delivery_binding(lesson["event_id"]).action_id == "cmp-1"
    finally:
        evidence.close()
        application.close()
