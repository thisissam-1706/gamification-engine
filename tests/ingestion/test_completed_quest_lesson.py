"""A new lesson tagged with an already-completed quest must still be accepted."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from src.ingestion.evidence import EvidenceStore
from src.ingestion.service import ingest_lesson_completed
from src.state_machine.learner import Learner, LearnerApplication
from src.state_machine.projection import rebuild_learner_state


def _envelope(learner_id: str, completion_id: str, event_id: str | None = None):
    occurred = datetime(2026, 9, 1, tzinfo=timezone.utc)
    return {
        "event_id": event_id or str(uuid4()),
        "event_type": "learning.lesson_completed",
        "schema_version": 1,
        "occurred_at": occurred.isoformat(),
        "ingested_at": (occurred + timedelta(minutes=1)).isoformat(),
        "subject_id": learner_id,
        "producer": "learning-svc",
        "correlation_id": str(uuid4()),
        "causation_id": None,
        "dedup_key": f"lesson_completed:{completion_id}",
        "payload": {
            "completion_id": completion_id,
            "lesson_id": "lesson-1",
            "timezone": "Asia/Kolkata",
        },
        "provenance": {"source": "client"},
    }


def test_new_lesson_for_completed_quest_is_accepted_with_xp(tmp_path):
    db = str(tmp_path / "events.db")
    environment = {"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
    application = LearnerApplication(env=environment)
    evidence = EvidenceStore(db)
    try:
        learner = Learner()
        application.save(learner)
        learner_id = str(learner.id)
        learner = application.repository.get(learner.id)
        learner.assign_quest(
            "lesson_starter_v1",
            assignment_id="assignment-1",
            assigned_at="2026-08-25T00:00:00Z",
        )
        application.save(learner)

        for index in range(4):
            envelope = _envelope(
                learner_id,
                f"quest-{index}",
                f"00000000-0000-4000-8000-00000000040{index}",
            )
            envelope["payload"]["assignment_id"] = "assignment-1"
            result = ingest_lesson_completed(
                application, evidence, envelope, authenticated_producer="learning-svc"
            )
            if index < 3:
                assert (result.status, result.reason) == ("accepted", None), index
                assert evidence.list(learner_id) == [], index
            else:
                assert (result.status, result.reason) == (
                    "accepted",
                    "quest_already_completed",
                )
                fourth_event_id = envelope["event_id"]
            if index == 2:
                mid = application.repository.get(learner.id)
                assert mid.xp_total == 300
                assert mid.quest_instances["assignment-1"]["status"] == "completed"

        final = application.repository.get(learner.id)
        assert final.xp_total == 400
        assert final.completed_lesson_count == 4
        assert final.quest_instances["assignment-1"]["status"] == "completed"
        assert final.quest_instances["assignment-1"]["progress"] == 3

        events = list(application.events.get(learner.id))
        assert sum(isinstance(e, Learner.XPAwarded) for e in events) == 4
        assert sum(isinstance(e, Learner.QuestCompleted) for e in events) == 1
        rows = evidence.list(learner_id)
        assert len(rows) == 1
        assert rows[0].reason == "quest_already_completed"
        assert rows[0].action_id == "quest_completion:assignment-1"
        assert rows[0].original_ref == "quest_completion:assignment-1"
        assert rows[0].event_id == fourth_event_id
    finally:
        evidence.close()
        application.close()


def _setup(tmp_path, *, assign=True, deadline_at=None, assigned_at="2026-08-25T00:00:00Z"):
    db = str(tmp_path / "events.db")
    environment = {"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
    application = LearnerApplication(env=environment)
    evidence = EvidenceStore(db)
    learner = Learner()
    application.save(learner)
    if assign:
        learner = application.repository.get(learner.id)
        kwargs = {"deadline_at": deadline_at} if deadline_at else {}
        learner.assign_quest(
            "lesson_starter_v1",
            assignment_id="assignment-1",
            assigned_at=assigned_at,
            **kwargs,
        )
        application.save(learner)
    return application, evidence, learner.id


def _ingest(application, evidence, learner_id, completion_id, event_id, assignment_id):
    envelope = _envelope(str(learner_id), completion_id, event_id)
    envelope["payload"]["assignment_id"] = assignment_id
    return ingest_lesson_completed(
        application, evidence, envelope, authenticated_producer="learning-svc"
    )


def _complete_quest_and_fourth_lesson(application, evidence, learner_id):
    for index in range(4):
        result = _ingest(
            application,
            evidence,
            learner_id,
            f"quest-{index}",
            f"00000000-0000-4000-8000-00000000050{index}",
            "assignment-1",
        )
        assert result.status == "accepted", index


def test_resent_fourth_completion_id_is_duplicate_and_xp_unchanged(tmp_path):
    application, evidence, learner_id = _setup(tmp_path)
    try:
        _complete_quest_and_fourth_lesson(application, evidence, learner_id)
        assert application.repository.get(learner_id).xp_total == 400
        result = _ingest(
            application,
            evidence,
            learner_id,
            "quest-3",
            "00000000-0000-4000-8000-0000000005ff",
            "assignment-1",
        )
        assert (result.status, result.reason) == ("duplicate", "duplicate_completion_id")
        assert application.repository.get(learner_id).xp_total == 400
    finally:
        evidence.close()
        application.close()


def test_lesson_tagged_to_never_assigned_assignment_is_accepted_without_quest_evidence(tmp_path):
    application, evidence, learner_id = _setup(tmp_path, assign=False)
    try:
        result = _ingest(
            application,
            evidence,
            learner_id,
            "orphan-1",
            "00000000-0000-4000-8000-000000000601",
            "assignment-never-assigned",
        )
        assert result.status == "accepted"
        assert application.repository.get(learner_id).xp_total == 100
        assert evidence.list(str(learner_id)) == []
    finally:
        evidence.close()
        application.close()


def test_lesson_tagged_to_expired_quest_is_accepted_without_completed_quest_evidence(tmp_path):
    # The lesson occurs 2026-09-01, after the 2026-08-15 deadline.
    application, evidence, learner_id = _setup(
        tmp_path,
        assigned_at="2026-08-01T00:00:00Z",
        deadline_at="2026-08-15T00:00:00Z",
    )
    try:
        result = _ingest(
            application,
            evidence,
            learner_id,
            "late-1",
            "00000000-0000-4000-8000-000000000701",
            "assignment-1",
        )
        assert result.status == "accepted"
        learner = application.repository.get(learner_id)
        assert learner.xp_total == 100
        assert learner.quest_instances["assignment-1"]["status"] == "expired"
        assert not any(
            row.reason == "quest_already_completed"
            for row in evidence.list(str(learner_id))
        )
    finally:
        evidence.close()
        application.close()


def test_rebuild_after_fourth_lesson_matches_live_aggregate(tmp_path):
    application, evidence, learner_id = _setup(tmp_path)
    try:
        _complete_quest_and_fourth_lesson(application, evidence, learner_id)
        live = application.repository.get(learner_id)
        rebuilt = rebuild_learner_state(list(application.events.get(learner_id)))
        assert rebuilt.xp_total == 400
        assert rebuilt.xp_total == live.xp_total
        assert rebuilt.new_streak == live.new_streak
        assert rebuilt.freezes_remaining == live.freezes_remaining
        assert rebuilt.activity_dates == tuple(sorted(live.activity_dates))
        assert rebuilt.completed_quests == ("lesson_starter_v1",)
    finally:
        evidence.close()
        application.close()
