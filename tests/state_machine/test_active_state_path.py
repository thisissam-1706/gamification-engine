"""Week 3 review P0: every active reader must agree with the aggregate and the pure fold.

Covers: LearnerStreakProjection (arrival order / freezes), LearnerQuestProjection
(per-assignment identity, expiry) and ingestion of a fresh lesson tagged to an
already-completed quest.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from src.ingestion.evidence import EvidenceStore
from tests.ingestion.support import ingest_lesson_completed  # late_window=None
from src.ingestion.validation import DEFAULT_INGEST_RULE_VERSION, expected_dedup_key
from src.state_machine.learner import Learner, LearnerApplication
from src.state_machine.projection import (
    LearnerQuestProjection,
    LearnerStreakProjection,
    rebuild_learner_state,
)

QUEST = "lesson_starter_v1"


def _events(learner: Learner) -> list:
    return list(learner.collect_events())


def _complete(learner: Learner, cid: str, day: str) -> None:
    learner.complete_lesson(cid, "lesson-1", activity_date=day)


def _lesson_at(learner: Learner, cid: str, occurred_at: str) -> None:
    learner.complete_lesson(cid, "lesson-1", occurred_at=occurred_at, timezone="UTC")


# ---------------------------------------------------------------------------
# Streak reader == aggregate == pure fold
# ---------------------------------------------------------------------------
def test_streak_projection_matches_aggregate_for_late_oct1():
    learner = Learner()
    _complete(learner, "c1", "2026-10-01")
    _complete(learner, "c2", "2026-10-02")
    _complete(learner, "c3", "2026-10-01")  # new completion on an earlier day
    events = _events(learner)

    replayed = rebuild_learner_state(events)
    projection = LearnerStreakProjection()
    for event in events:
        projection.apply(event)
    summary = projection.streak_summaries[learner.id]

    assert replayed.new_streak == 2
    assert learner.new_streak == 2
    assert summary.new_streak == 2
    assert summary.freezes_remaining == learner.freezes_remaining


def test_streak_projection_counts_freeze_acquired_after_latest_activity():
    learner = Learner()
    _complete(learner, "c1", "2026-10-01")
    _complete(learner, "c2", "2026-10-02")
    learner.acquire_freeze("f1", activity_date="2026-10-03")
    events = _events(learner)

    replayed = rebuild_learner_state(events)
    projection = LearnerStreakProjection()
    for event in events:
        projection.apply(event)
    summary = projection.streak_summaries[learner.id]

    assert learner.freezes_remaining == 1
    assert replayed.freezes_remaining == 1
    assert summary.freezes_remaining == 1
    assert summary.new_streak == replayed.new_streak == 2


def test_streak_projection_is_idempotent_when_events_are_delivered_twice():
    learner = Learner()
    _complete(learner, "c1", "2026-10-01")
    learner.acquire_freeze("f1", activity_date="2026-10-01")
    events = _events(learner)
    projection = LearnerStreakProjection()
    for event in events + events:
        projection.apply(event)
    summary = projection.streak_summaries[learner.id]
    assert (summary.new_streak, summary.freezes_remaining) == (1, 1)


# ---------------------------------------------------------------------------
# Quest reader keeps assignment identity, status and deadline
# ---------------------------------------------------------------------------
def _two_assignments() -> Learner:
    learner = Learner()
    learner.assign_quest(
        QUEST, assignment_id="a1", assigned_at="2026-10-01T00:00:00Z",
        deadline_at="2026-10-08T00:00:00Z",
    )
    for index in range(3):
        _lesson_at(learner, f"first-{index}", f"2026-10-0{index + 2}T10:00:00Z")
    learner.assign_quest(
        QUEST, assignment_id="a2", assigned_at="2026-10-05T00:00:00Z",
        deadline_at="2026-10-12T00:00:00Z",
    )
    _lesson_at(learner, "second-0", "2026-10-06T10:00:00Z")
    return learner


def test_quest_projection_keeps_repeated_assignments_separate():
    learner = _two_assignments()
    assert learner.quest_instances["a1"]["status"] == "completed"
    assert learner.quest_instances["a2"]["status"] == "assigned"

    projection = LearnerQuestProjection()
    for event in _events(learner):
        projection.apply(event)
    summary = projection.get_summary(learner.id)

    assert set(summary.assignments) == {"a1", "a2"}
    assert summary.assignments["a1"].status == "completed"
    assert summary.assignments["a2"].status == "assigned"
    assert summary.assignments["a1"].progress == 3
    assert summary.assignments["a2"].progress == 1
    assert summary.assignments["a2"].deadline_at == datetime(
        2026, 10, 12, tzinfo=timezone.utc
    )
    assert summary.quest_progress == {"a1": 3, "a2": 1}
    # The same quest id assigned twice is not collapsed.
    assert summary.assigned_quests == (QUEST, QUEST)
    assert summary.completed_quests == (QUEST,)


def test_quest_projection_marks_overdue_assignment_expired_without_new_lesson():
    learner = _two_assignments()
    projection = LearnerQuestProjection()
    for event in _events(learner):
        projection.apply(event)

    before_deadline = projection.get_summary(
        learner.id, as_of=datetime(2026, 10, 11, tzinfo=timezone.utc)
    )
    after_deadline = projection.get_summary(
        learner.id, as_of=datetime(2026, 10, 13, tzinfo=timezone.utc)
    )
    assert before_deadline.assignments["a2"].status == "assigned"
    assert after_deadline.assignments["a2"].status == "expired"
    # A completed assignment never becomes expired.
    assert after_deadline.assignments["a1"].status == "completed"


def test_quest_projection_applies_expiry_events():
    learner = Learner()
    learner.assign_quest(
        QUEST, assignment_id="a1", assigned_at="2026-10-01T00:00:00Z",
        deadline_at="2026-10-03T00:00:00Z",
    )
    _lesson_at(learner, "late", "2026-10-09T10:00:00Z")  # past deadline -> expiry event
    projection = LearnerQuestProjection()
    for event in _events(learner):
        projection.apply(event)
    summary = projection.get_summary(learner.id)
    assert learner.quest_instances["a1"]["status"] == "expired"
    assert summary.assignments["a1"].status == "expired"
    assert summary.completed_quests == ()


# ---------------------------------------------------------------------------
# Ingestion: a fresh lesson is independent of a finished quest
# ---------------------------------------------------------------------------
def _envelope(learner_id: str, completion_id: str, event_id: str, assignment_id=None):
    occurred = datetime(2026, 9, 1, tzinfo=timezone.utc)
    envelope = {
        "event_id": event_id,
        "event_type": "learning.lesson_completed",
        "schema_version": 1,
        "occurred_at": occurred.isoformat(),
        "ingested_at": (occurred + timedelta(minutes=1)).isoformat(),
        "subject_id": learner_id,
        "producer": "learning-svc",
        "correlation_id": str(uuid4()),
        "causation_id": None,
        "dedup_key": expected_dedup_key(event_id, DEFAULT_INGEST_RULE_VERSION),
        "payload": {
            "completion_id": completion_id,
            "lesson_id": "lesson-1",
            "timezone": "Asia/Kolkata",
        },
        "provenance": {"source": "client"},
    }
    if assignment_id is not None:
        envelope["payload"]["assignment_id"] = assignment_id
    return envelope


def _setup(tmp_path):
    db = str(tmp_path / "events.db")
    env = {"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
    application = LearnerApplication(env=env)
    evidence = EvidenceStore(db)
    learner = Learner()
    application.save(learner)
    learner = application.repository.get(learner.id)
    learner.assign_quest(
        QUEST, assignment_id="assignment-1", assigned_at="2026-08-25T00:00:00Z"
    )
    application.save(learner)
    return application, evidence, str(learner.id)


def test_fresh_lesson_tagged_to_completed_quest_is_accepted(tmp_path):
    application, evidence, learner_id = _setup(tmp_path)
    try:
        for index in range(3):
            envelope = _envelope(
                learner_id, f"quest-{index}",
                f"00000000-0000-4000-8000-00000000030{index}", "assignment-1",
            )
            assert ingest_lesson_completed(
                application, evidence, envelope, authenticated_producer="learning-svc"
            ).status == "accepted"
        learner = application.repository.get(UUID(learner_id))
        assert learner.xp_total == 300
        assert "quest_completion:assignment-1" in learner.completed_quest_ids

        fourth = _envelope(
            learner_id, "quest-3", "00000000-0000-4000-8000-000000000303",
            "assignment-1",
        )
        result = ingest_lesson_completed(
            application, evidence, fourth, authenticated_producer="learning-svc"
        )
        assert result.status == "accepted"

        learner = application.repository.get(UUID(learner_id))
        assert learner.xp_total == 400
        assert learner.completed_lesson_count == 4
        # Quest effects are unchanged: still complete, progress not advanced.
        assert learner.quest_instances["assignment-1"]["status"] == "completed"
        assert learner.quest_instances["assignment-1"]["progress"] == 3
        events = list(application.events.get(learner.id))
        assert sum(isinstance(e, Learner.XPAwarded) for e in events) == 4
        assert sum(isinstance(e, Learner.QuestCompleted) for e in events) == 1

        # The quest no-op is recorded as evidence, separately from the lesson.
        rows = evidence.list(learner_id)
        assert [r.reason for r in rows] == ["quest_already_completed"]
        assert rows[0].action_id == "quest_completion:assignment-1"
        assert rows[0].event_id == fourth["event_id"]
    finally:
        evidence.close()
        application.close()


def test_resent_completion_tagged_to_completed_quest_is_still_a_duplicate(tmp_path):
    application, evidence, learner_id = _setup(tmp_path)
    try:
        for index in range(4):
            envelope = _envelope(
                learner_id, f"quest-{index}",
                f"00000000-0000-4000-8000-00000000040{index}", "assignment-1",
            )
            assert ingest_lesson_completed(
                application, evidence, envelope, authenticated_producer="learning-svc"
            ).status == "accepted"
        resend = _envelope(
            learner_id, "quest-3", "00000000-0000-4000-8000-000000000409",
            "assignment-1",
        )
        result = ingest_lesson_completed(
            application, evidence, resend, authenticated_producer="learning-svc"
        )
        assert (result.status, result.reason) == ("duplicate", "duplicate_completion_id")
        learner = application.repository.get(UUID(learner_id))
        assert learner.xp_total == 400
    finally:
        evidence.close()
        application.close()
