"""M1 step 7: trace_delivery shows everything one delivery caused.

M  a lesson delivery's derived events (XP, streak, quest, badge) are listed with its ids
N  a checkpoint delivery's remediation decision, quest and notification are traced
O  tracing is read-only: no write, and the aggregate is neither loaded nor saved
"""

from datetime import datetime, timezone
import hashlib
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from src.ingestion.trace import trace_delivery
from src.interventions.notification_hook import run_remediation_with_notification
from src.notifications.channels.push import SimulatedPushAdapter
from src.notifications.dispatcher import NotificationDispatcher
from src.notifications.models import ChannelType, LearnerPreferences
from src.notifications.store import NotificationStore
from src.state_machine.learner import Learner
from tests.ingestion.support import ingest_lesson_completed  # late_window=None
from tests.ingestion.test_derived_event_ids import (
    CORRELATION_ID,
    EVENT_ID,
    _envelope as _lesson_envelope,
    _open as _open_lesson_db,
)
from tests.interventions.test_checkpoint_attempt_ingestion import (
    _checkpoint_envelope,
    _ingest_checkpoint,
    _new_learner,
    _open as _open_checkpoint_db,
)

ATTEMPT_EVENT_ID = "00000000-0000-4000-8000-0000000007a1"
NOW = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)


def _types(trace) -> set[str]:
    return {fact.event_type for fact in trace.derived_events}


def _file_sha256(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _ingest_big_lesson(tmp_path):
    """Day 7 delivery that earns XP, a streak update, quest progress+completion and the badge."""
    application, evidence, learner_id = _open_lesson_db(tmp_path)

    def day(n):
        return datetime(2026, 9, n, 12, tzinfo=timezone.utc)

    def seed(n):
        learner = application.repository.get(learner_id)
        learner.complete_lesson(
            f"seed-{n}", f"lesson-seed-{n}", occurred_at=day(n).isoformat(), timezone="Asia/Kolkata"
        )
        application.save(learner)

    for n in range(1, 5):
        seed(n)
    learner = application.repository.get(learner_id)
    learner.assign_quest(
        "lesson_starter_v1", assignment_id="assignment-1", assigned_at="2026-09-05T00:00:00Z"
    )
    application.save(learner)
    for n in (5, 6):
        seed(n)
    envelope = _lesson_envelope(
        str(learner_id), "c-7", occurred_at=day(7), assignment_id="assignment-1"
    )
    result = ingest_lesson_completed(
        application, evidence, envelope, authenticated_producer="learning-svc"
    )
    assert result.status == "accepted"
    return application, evidence, learner_id


def _checkpoint_with_notification(tmp_path):
    application, evidence = _open_checkpoint_db(str(tmp_path / "events.db"))
    store = NotificationStore(str(tmp_path / "notif.db"))
    dispatcher = NotificationDispatcher(
        store=store, adapters={ChannelType.PUSH: SimulatedPushAdapter()}
    )
    learner_id = _new_learner(application)
    envelope = _checkpoint_envelope(learner_id, "att-1", ATTEMPT_EVENT_ID)
    envelope["payload"]["passed"] = False
    envelope["correlation_id"] = "att-corr"
    assert _ingest_checkpoint(application, evidence, envelope).status == "accepted"
    decision, fact = run_remediation_with_notification(
        application,
        learner_id,
        "att-1",
        clock=lambda: NOW,
        dispatcher=dispatcher,
        prefs=LearnerPreferences(learner_id=learner_id),
    )
    assert decision.action == "assign" and fact is not None  # setup sanity
    return application, evidence, store, learner_id


def test_m_lesson_trace_lists_every_derived_event(tmp_path):
    application, evidence, learner_id = _ingest_big_lesson(tmp_path)
    try:
        trace = trace_delivery(application, evidence, EVENT_ID)

        assert trace is not None
        assert {
            "XPAwarded",
            "StreakUpdated",
            "QuestProgressed",
            "QuestCompleted",
            "BadgeAwarded",
        } <= _types(trace)
        assert "LessonCompleted" not in _types(trace)  # the source fact is not "derived"
        for fact in trace.derived_events:
            assert (fact.correlation_id, fact.causation_id) == (CORRELATION_ID, EVENT_ID)
        # Only this delivery's events: the six seeded lessons are not in the trace.
        assert sum(f.event_type == "XPAwarded" for f in trace.derived_events) == 1
        assert trace.notifications == ()
    finally:
        evidence.close()
        application.close()


def test_n_checkpoint_trace_lists_remediation_events_and_notification(tmp_path):
    application, evidence, store, learner_id = _checkpoint_with_notification(tmp_path)
    try:
        trace = trace_delivery(application, evidence, ATTEMPT_EVENT_ID, notification_store=store)

        assert trace is not None
        assert {"RemediationDecided", "QuestAssigned"} <= _types(trace)
        for fact in trace.derived_events:
            assert (fact.correlation_id, fact.causation_id) == ("att-corr", ATTEMPT_EVENT_ID)
        assert [n["proposal_id"] for n in trace.notifications] == ["remediation_notice:att-1"]
        assert trace.notifications[0]["causation_id"] == ATTEMPT_EVENT_ID

        # Without a notification store the field stays empty and nothing breaks.
        assert trace_delivery(application, evidence, ATTEMPT_EVENT_ID).notifications == ()
    finally:
        store.close()
        evidence.close()
        application.close()


def test_o_trace_writes_nothing_and_never_touches_the_aggregate(tmp_path):
    application, evidence, store, learner_id = _checkpoint_with_notification(tmp_path)
    try:
        events_db = str(tmp_path / "events.db")
        notif_db = str(tmp_path / "notif.db")
        events_before = list(application.events.get(UUID(learner_id)))
        evidence_changes = evidence.connection.total_changes
        store_changes = store.connection.total_changes
        files_before = (_file_sha256(events_db), _file_sha256(notif_db))

        def forbidden(*args, **kwargs):
            raise AssertionError("trace_delivery must not load or save the aggregate")

        with patch.object(application.repository, "get", forbidden), patch.object(
            application, "save", forbidden
        ):
            trace = trace_delivery(
                application, evidence, ATTEMPT_EVENT_ID, notification_store=store
            )

        assert trace is not None
        assert evidence.connection.total_changes == evidence_changes
        assert store.connection.total_changes == store_changes
        assert list(application.events.get(UUID(learner_id))) == events_before
        assert (_file_sha256(events_db), _file_sha256(notif_db)) == files_before
    finally:
        store.close()
        evidence.close()
        application.close()
