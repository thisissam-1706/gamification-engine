"""M1 step 8: correlation/causation ids are provenance only, and old data still works.

1. The same lesson sequence, ingested with and without ids, yields identical learner state.
2. A notification database created before the id columns existed opens, migrates, and its
   old rows read back with None ids.
"""

import sqlite3
from datetime import datetime, timezone
from uuid import UUID

from src.ingestion.evidence import EvidenceStore
from src.notifications.channels.push import SimulatedPushAdapter
from src.notifications.dispatcher import NotificationDispatcher
from src.notifications.models import ChannelType, LearnerPreferences, NotificationStatus
from src.notifications.store import NotificationStore
from src.state_machine.learner import Learner, LearnerApplication
from tests.ingestion.support import ingest_lesson_completed  # late_window=None
from tests.ingestion.test_derived_event_ids import _envelope


def _run_sequence(application, evidence, tag: str, *, with_ids: bool) -> UUID:
    """Seven daily lessons: a 3-step quest completes, a short one expires, the 7-day badge is earned."""
    learner = Learner()
    application.save(learner)
    learner = application.repository.get(learner.id)
    learner.assign_quest(
        "lesson_starter_v1",
        assignment_id="assignment-long",
        assigned_at="2026-08-31T00:00:00Z",
        deadline_at="2026-09-30T00:00:00Z",
    )
    learner.assign_quest(
        "lesson_starter_v1",
        assignment_id="assignment-short",
        assigned_at="2026-08-31T00:00:00Z",
        deadline_at="2026-09-03T00:00:00Z",
    )
    application.save(learner)

    for day in range(1, 8):
        event_id = f"00000000-0000-4000-8000-{ord(tag):04x}00000{day:03d}"
        envelope = _envelope(
            str(learner.id),
            f"c-{day}",
            occurred_at=datetime(2026, 9, day, 12, tzinfo=timezone.utc),
            event_id=event_id,
        )
        envelope["payload"]["lesson_id"] = "lesson-shared"
        if with_ids:
            envelope["correlation_id"] = f"corr-{tag}"
            envelope["causation_id"] = f"cause-{tag}"
        else:
            # The envelope schema requires a string correlation_id, so "no ids" at the
            # ingestion boundary means the minimum it allows: causation_id None.
            envelope["correlation_id"] = f"corr-{tag}"
            envelope["causation_id"] = None
        result = ingest_lesson_completed(
            application, evidence, envelope, authenticated_producer="learning-svc"
        )
        assert result.status == "accepted", (day, result)
    return learner.id


def _run_sequence_in_domain(application, tag: str) -> UUID:
    """The same sequence straight through the aggregate with no ids at all (all None)."""
    learner = Learner()
    application.save(learner)
    learner = application.repository.get(learner.id)
    learner.assign_quest(
        "lesson_starter_v1",
        assignment_id="assignment-long",
        assigned_at="2026-08-31T00:00:00Z",
        deadline_at="2026-09-30T00:00:00Z",
    )
    learner.assign_quest(
        "lesson_starter_v1",
        assignment_id="assignment-short",
        assigned_at="2026-08-31T00:00:00Z",
        deadline_at="2026-09-03T00:00:00Z",
    )
    application.save(learner)
    for day in range(1, 8):
        learner = application.repository.get(learner.id)
        learner.complete_lesson(
            f"c-{day}",
            "lesson-shared",
            occurred_at=datetime(2026, 9, day, 12, tzinfo=timezone.utc).isoformat(),
            timezone="Asia/Kolkata",
        )
        application.save(learner)
    return learner.id


def _state(application, learner_id) -> dict:
    learner = application.repository.get(learner_id)
    return {
        "xp_total": learner.xp_total,
        "new_streak": learner.new_streak,
        "quests": {k: v["status"] for k, v in learner.quest_instances.items()},
        "progress": {k: v["progress"] for k, v in learner.quest_instances.items()},
        "badges_issued": dict(learner.badges_issued),
        "completed_lessons": sorted(learner.completed_lesson_ids),
        "freezes_remaining": learner.freezes_remaining,
    }


def test_ids_never_affect_learner_state(tmp_path):
    db = str(tmp_path / "events.db")
    application = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
    )
    evidence = EvidenceStore(db)
    try:
        with_ids = _run_sequence(application, evidence, "a", with_ids=True)
        without_ids = _run_sequence(application, evidence, "b", with_ids=False)

        no_ids_at_all = _run_sequence_in_domain(application, "c")
        state_with = _state(application, with_ids)
        state_without = _state(application, without_ids)
        state_none = _state(application, no_ids_at_all)

        # The scenario really exercised XP, streak, quest completion/expiry and the badge.
        assert state_with["xp_total"] > 0 and state_with["new_streak"] == 7
        assert state_with["quests"] == {
            "assignment-long": "completed",
            "assignment-short": "expired",
        }
        assert state_with["badges_issued"]

        # Badge award ids embed the learner id only through the badge id, so compare as given.
        assert state_with == state_without == state_none

        # The ids really were stored on one learner and absent on the other.
        def derived_ids(learner_id):
            return {
                (getattr(e, "correlation_id", None), getattr(e, "causation_id", None))
                for e in application.events.get(learner_id)
                if type(e).__name__ == "XPAwarded"
            }

        assert all(c.startswith("corr-a") for c, _ in derived_ids(with_ids))
        assert derived_ids(no_ids_at_all) == {(None, None)}
    finally:
        evidence.close()
        application.close()


# --- old notification database ------------------------------------------------------

OLD_SCHEMA = (
    """CREATE TABLE notification_proposals (
        proposal_id TEXT PRIMARY KEY,
        learner_id TEXT NOT NULL,
        channel TEXT NOT NULL,
        category TEXT NOT NULL,
        created_at TEXT NOT NULL,
        status TEXT NOT NULL
    )""",
    """CREATE TABLE notification_dispatches (
        dispatch_id TEXT PRIMARY KEY,
        proposal_id TEXT NOT NULL,
        learner_id TEXT NOT NULL,
        channel TEXT NOT NULL,
        category TEXT NOT NULL,
        dispatched_at TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )""",
    """CREATE TABLE notification_suppressions (
        suppression_id TEXT PRIMARY KEY,
        proposal_id TEXT NOT NULL,
        learner_id TEXT NOT NULL,
        channel TEXT NOT NULL,
        category TEXT NOT NULL,
        suppressed_at TEXT NOT NULL,
        reason TEXT NOT NULL
    )""",
    """CREATE TABLE notification_dlq (
        dlq_id TEXT PRIMARY KEY,
        proposal_id TEXT NOT NULL,
        learner_id TEXT NOT NULL,
        channel TEXT NOT NULL,
        category TEXT NOT NULL,
        failed_at TEXT NOT NULL,
        reason TEXT NOT NULL,
        attempts INTEGER NOT NULL,
        payload_json TEXT NOT NULL,
        resolved INTEGER NOT NULL DEFAULT 0,
        created_at TEXT DEFAULT '',
        template_id TEXT DEFAULT 'default_template',
        ttl_seconds INTEGER DEFAULT 86400
    )""",
    """CREATE TABLE notification_rescheduled (
        proposal_id TEXT PRIMARY KEY,
        learner_id TEXT NOT NULL,
        channel TEXT NOT NULL,
        category TEXT NOT NULL,
        rescheduled_for TEXT NOT NULL,
        created_at TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        released INTEGER NOT NULL DEFAULT 0,
        template_id TEXT DEFAULT 'default_template',
        ttl_seconds INTEGER DEFAULT 86400
    )""",
)


def _columns(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def test_old_notification_database_migrates_and_old_rows_have_none_ids(tmp_path):
    db = str(tmp_path / "old_notif.db")
    raw = sqlite3.connect(db)
    for statement in OLD_SCHEMA:
        raw.execute(statement)
    raw.execute(
        "INSERT INTO notification_proposals VALUES "
        "('old-p', 'L1', 'push', 'general', '2026-10-12T12:00:00Z', 'dispatched')"
    )
    raw.execute(
        "INSERT INTO notification_proposals VALUES "
        "('old-q', 'L1', 'push', 'general', '2026-10-12T17:30:00Z', 'queued')"
    )
    raw.execute(
        "INSERT INTO notification_rescheduled (proposal_id, learner_id, channel, category, "
        "rescheduled_for, created_at, payload_json) VALUES "
        "('old-q', 'L1', 'push', 'general', '2026-10-13T02:30:00Z', '2026-10-12T17:30:00Z', '{}')"
    )
    raw.execute(
        "INSERT INTO notification_dlq (dlq_id, proposal_id, learner_id, channel, category, "
        "failed_at, reason, attempts, payload_json) VALUES "
        "('old-dlq', 'old-d', 'L1', 'push', 'general', '2026-10-12T12:00:00Z', 'provider_exhausted', 3, '{}')"
    )
    raw.commit()
    for table in ("notification_proposals", "notification_rescheduled", "notification_dlq"):
        assert not {"correlation_id", "causation_id"} & _columns(raw, table)  # really old
    raw.close()

    store = NotificationStore(db)  # must not fail
    try:
        for table in ("notification_proposals", "notification_rescheduled", "notification_dlq"):
            assert {"correlation_id", "causation_id"} <= _columns(store.connection, table), table

        # Old rows read back with None ids.
        assert store.connection.execute(
            "SELECT correlation_id, causation_id FROM notification_proposals WHERE proposal_id = 'old-p'"
        ).fetchone() == (None, None)
        entry = store.get_rescheduled_entry("old-q")
        assert (entry["correlation_id"], entry["causation_id"]) == (None, None)
        (dlq,) = store.get_dlq_entries()
        assert (dlq["correlation_id"], dlq["causation_id"]) == (None, None)
        assert store.list_proposals_by_causation("anything") == []

        # Old rescheduled rows still release, with None ids on the fact.
        dispatcher = NotificationDispatcher(
            store=store, adapters={ChannelType.PUSH: SimulatedPushAdapter()}
        )
        prefs = LearnerPreferences(learner_id="L1")
        facts = dispatcher.release_rescheduled(
            now_utc="2026-10-13T09:00:00Z", prefs_provider={"L1": prefs}
        )
        assert [(f.proposal_id, f.status) for f in facts] == [
            ("old-q", NotificationStatus.DISPATCHED)
        ]
        assert (facts[0].correlation_id, facts[0].causation_id) == (None, None)

        # And the old DLQ row replays with None ids.
        replay = dispatcher.replay_dlq("old-dlq", prefs=prefs, now_utc="2026-10-13T09:00:00Z")
        assert replay.status == NotificationStatus.DISPATCHED
        assert (replay.correlation_id, replay.causation_id) == (None, None)
    finally:
        store.close()
