"""Week 3 review P0: ingestion of a fresh lesson tagged to an already-completed quest.

The streak and quest projection tests live in test_streak_reader.py and
test_quest_reader.py.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from src.ingestion.evidence import EvidenceStore
from tests.ingestion.support import ingest_lesson_completed  # late_window=None
from src.ingestion.validation import DEFAULT_INGEST_RULE_VERSION, expected_dedup_key
from src.state_machine.learner import Learner, LearnerApplication

QUEST = "lesson_starter_v1"



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
