"""Integration test for LeaderboardProjection with eventsourcing aggregate and replay."""

import pytest

from src.leaderboard.store import SQLiteLeaderboardStore
from src.leaderboard.projection import LeaderboardProjection
from src.leaderboard.service import LeaderboardService
from src.leaderboard.cohorts import get_calendar_week
from src.state_machine.learner import Learner, LearnerApplication


def test_leaderboard_projection_from_learner_events_and_rebuild(tmp_path):
    events_db = str(tmp_path / "events.db")
    leaderboard_db = str(tmp_path / "leaderboard.db")

    app = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": events_db}
    )
    store = SQLiteLeaderboardStore(leaderboard_db)
    projection = LeaderboardProjection(store)
    service = LeaderboardService(store)

    epoch = get_calendar_week("2026-10-12T08:00:00Z")
    cohort_id = "cohort_alpha"

    try:
        # Create learner 1
        l1 = Learner()
        l1.assign_cohort(cohort_id, epoch, assigned_at="2026-10-12T08:00:00Z")
        l1.complete_lesson("c1", "l1", occurred_at="2026-10-12T09:00:00Z", timezone="UTC")
        app.save(l1)

        # Create learner 2
        l2 = Learner()
        l2.assign_cohort(cohort_id, epoch, assigned_at="2026-10-12T08:00:00Z")
        l2.complete_lesson("c2", "l1", occurred_at="2026-10-12T09:15:00Z", timezone="UTC")
        l2.complete_lesson("c3", "l2", occurred_at="2026-10-12T09:30:00Z", timezone="UTC")
        app.save(l2)

        # Apply events to projection
        all_events = list(app.events.get(l1.id)) + list(app.events.get(l2.id))
        for event in all_events:
            projection.apply(event)

        # Query cohort leaderboard
        cohort_top = service.get_top_k("cohort", cohort_id, epoch, k=10)
        assert len(cohort_top) == 2
        # L2 has 200 XP (rank 1), L1 has 100 XP (rank 2)
        assert cohort_top[0].subject_id == str(l2.id)
        assert cohort_top[0].rank == 1
        assert cohort_top[0].score == 200

        assert cohort_top[1].subject_id == str(l1.id)
        assert cohort_top[1].rank == 2
        assert cohort_top[1].score == 100

        # Query global leaderboard
        global_top = service.get_top_k("global", "global", epoch, k=10)
        assert len(global_top) == 2
        assert global_top[0].subject_id == str(l2.id)
        assert global_top[1].subject_id == str(l1.id)

        # Test rebuild from genesis events
        store.clear()
        assert len(service.get_top_k("cohort", cohort_id, epoch, k=10)) == 0

        projection.rebuild(all_events)
        rebuilt_top = service.get_top_k("cohort", cohort_id, epoch, k=10)
        assert len(rebuilt_top) == 2
        assert rebuilt_top[0].subject_id == str(l2.id)
        assert rebuilt_top[0].score == 200
        assert rebuilt_top[1].subject_id == str(l1.id)
        assert rebuilt_top[1].score == 100
    finally:
        store.close()
        app.close()


def test_two_learners_with_same_completion_id_both_score(tmp_path):
    """(a) Two learners with the same completion_id both score without collision."""
    events_db = str(tmp_path / "events.db")
    leaderboard_db = str(tmp_path / "leaderboard.db")

    app = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": events_db}
    )
    store = SQLiteLeaderboardStore(leaderboard_db)
    projection = LeaderboardProjection(store)
    service = LeaderboardService(store)

    epoch = get_calendar_week("2026-10-12T08:00:00Z")
    cohort_id = "cohort_shared"
    shared_completion_id = "shared_lesson_101"

    try:
        # Learner 1 completes shared lesson
        l1 = Learner()
        l1.assign_cohort(cohort_id, epoch, assigned_at="2026-10-12T08:00:00Z")
        l1.complete_lesson(shared_completion_id, "l1", occurred_at="2026-10-12T09:00:00Z", timezone="UTC")
        app.save(l1)

        # Learner 2 completes the exact same lesson with the same completion_id
        l2 = Learner()
        l2.assign_cohort(cohort_id, epoch, assigned_at="2026-10-12T08:00:00Z")
        l2.complete_lesson(shared_completion_id, "l1", occurred_at="2026-10-12T09:15:00Z", timezone="UTC")
        app.save(l2)

        # Feed real LessonCompleted and XPAwarded events through apply()
        for ev in app.events.get(l1.id):
            projection.apply(ev)
        for ev in app.events.get(l2.id):
            projection.apply(ev)

        # Verify BOTH learners scored on cohort leaderboard
        cohort_top = service.get_top_k("cohort", cohort_id, epoch, k=10)
        assert len(cohort_top) == 2
        scores = {entry.subject_id: entry.score for entry in cohort_top}
        assert scores[str(l1.id)] == 100
        assert scores[str(l2.id)] == 100

        # Verify BOTH learners scored on global leaderboard
        global_top = service.get_top_k("global", "global", epoch, k=10)
        assert len(global_top) == 2
        global_scores = {entry.subject_id: entry.score for entry in global_top}
        assert global_scores[str(l1.id)] == 100
        assert global_scores[str(l2.id)] == 100

        # Verify applied events tracked both distinct learner events
        applied_l1 = store.get_applied_events(subject_id=str(l1.id))
        applied_l2 = store.get_applied_events(subject_id=str(l2.id))
        assert len(applied_l1) == 1
        assert len(applied_l2) == 1
        assert applied_l1[0]["xp_amount"] == 100
        assert applied_l2[0]["xp_amount"] == 100
    finally:
        store.close()
        app.close()


def test_restart_between_lesson_completed_and_xp_awarded_lands_in_right_epoch(tmp_path):
    """(b) Restart between LessonCompleted and XPAwarded still lands in the right epoch."""
    events_db = str(tmp_path / "events.db")
    leaderboard_db = str(tmp_path / "leaderboard.db")

    app = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": events_db}
    )
    store1 = SQLiteLeaderboardStore(leaderboard_db)
    projection1 = LeaderboardProjection(store1)

    # Specific historical epoch: 2026-W42 (Oct 12, 2026)
    target_time = "2026-10-12T09:00:00Z"
    target_epoch = get_calendar_week(target_time)
    assert target_epoch == "2026-W42"

    try:
        l = Learner()
        l.complete_lesson("c_restart_test", "l1", occurred_at=target_time, timezone="UTC")
        app.save(l)

        all_events = list(app.events.get(l.id))
        lesson_completed_event = [e for e in all_events if isinstance(e, Learner.LessonCompleted)][0]
        xp_awarded_event = [e for e in all_events if isinstance(e, Learner.XPAwarded)][0]

        # 1. Apply ONLY LessonCompleted in the first projection instance
        projection1.apply(lesson_completed_event)

        # 2. Simulate complete service restart: close store1, clear memory
        store1.close()

        # 3. New process / restart starts up: new store, new projection instance
        store2 = SQLiteLeaderboardStore(leaderboard_db)
        projection2 = LeaderboardProjection(store2)
        service2 = LeaderboardService(store2)

        # In-memory map is completely empty upon restart:
        assert len(projection2._completion_occurred_at) == 0

        # 4. Now XPAwarded arrives after restart
        status = projection2.apply(xp_awarded_event)
        assert status == "applied"

        # 5. Verify the XP award landed in the historical target epoch (2026-W42)
        target_entries = service2.get_top_k("global", "global", target_epoch, k=10)
        assert len(target_entries) == 1
        assert target_entries[0].subject_id == str(l.id)
        assert target_entries[0].score == 100

        # Verify it did not create an entry in any other epoch
        all_epochs = store2.get_all_epochs()
        assert all_epochs == [target_epoch]
    finally:
        if "store2" in locals():
            store2.close()
        app.close()


def test_missing_time_never_lands_in_current_week(tmp_path):
    """(c) Missing time never lands in current week: parked as pending_time with error or status."""
    from datetime import datetime, timezone
    from src.leaderboard.projection import MissingTimestampError

    events_db = str(tmp_path / "events.db")
    leaderboard_db = str(tmp_path / "leaderboard.db")

    app = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": events_db}
    )
    store = SQLiteLeaderboardStore(leaderboard_db)
    projection = LeaderboardProjection(store)
    service = LeaderboardService(store)

    try:
        # Learner completes lesson with NO occurred_at
        l = Learner()
        l.complete_lesson("c_missing_time", "l1", occurred_at=None, timezone=None)
        app.save(l)

        all_events = list(app.events.get(l.id))
        xp_awarded_event = [e for e in all_events if isinstance(e, Learner.XPAwarded)][0]

        now_utc = datetime.now(timezone.utc)
        current_week = f"{now_utc.isocalendar()[0]}-W{now_utc.isocalendar()[1]:02d}"

        # 1. Feeding orphan XPAwarded with raise_on_missing_time=False returns "pending_time"
        status = projection.apply(xp_awarded_event, raise_on_missing_time=False)
        assert status == "pending_time"

        # 2. Feeding orphan XPAwarded with raise_on_missing_time=True raises MissingTimestampError
        with pytest.raises(MissingTimestampError) as exc_info:
            projection.apply(xp_awarded_event, raise_on_missing_time=True)
        assert exc_info.value.status == "pending_time"
        assert "Missing occurred_at" in str(exc_info.value)

        # 3. CRITICAL: The award must NEVER land in the current week!
        current_week_entries = service.get_top_k("global", "global", current_week, k=10)
        assert len(current_week_entries) == 0

        # No scores exist in any epoch
        assert len(store.get_all_epochs()) == 0

        # 4. The award is safely parked in SQLite with status 'pending_time'
        pending = store.get_pending_awards(subject_id=str(l.id))
        assert len(pending) == 1
        assert pending[0]["completion_id"] == "c_missing_time"
        assert pending[0]["status"] == "pending_time"
        assert pending[0]["xp_amount"] == 100
    finally:
        store.close()
        app.close()


def test_sqlite_applied_events_safe_schema_migration(tmp_path):
    """Verifies that an existing database with single-column PK on event_id is safely migrated."""
    import sqlite3

    leaderboard_db = str(tmp_path / "legacy_leaderboard.db")
    conn = sqlite3.connect(leaderboard_db)
    conn.execute(
        """
        CREATE TABLE leaderboard_applied_events (
            event_id TEXT PRIMARY KEY,
            subject_id TEXT NOT NULL,
            epoch TEXT NOT NULL,
            xp_amount INTEGER NOT NULL,
            applied_at TEXT NOT NULL,
            stream_offset INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        INSERT INTO leaderboard_applied_events VALUES (
            'legacy_ev_1', 'learner_old', '2026-W40', 100, '2026-10-01T10:00:00Z', 1
        )
        """
    )
    conn.commit()
    conn.close()

    # Opening with SQLiteLeaderboardStore triggers safe schema migration
    store = SQLiteLeaderboardStore(leaderboard_db)
    try:
        # Pre-existing data is preserved intact
        events = store.get_applied_events(subject_id="learner_old")
        assert len(events) == 1
        assert events[0]["event_id"] == "legacy_ev_1"

        # Verify compound primary key: (event_id, subject_id)
        cursor = store.connection.cursor()
        cursor.execute("PRAGMA table_info(leaderboard_applied_events)")
        cols = cursor.fetchall()
        pk_cols = {col[1] for col in cols if col[5] > 0}
        assert pk_cols == {"event_id", "subject_id"}

        # Now two learners with the same event_id can be applied without collision
        ok1 = store.apply_award_atomic(
            event_id="same_event_id",
            subject_id="learner_a",
            epoch="2026-W40",
            xp_amount=50,
            occurred_at="2026-10-01T11:00:00Z",
            global_score=50,
            global_first_reached_at="2026-10-01T11:00:00Z",
            cohort_id=None,
            cohort_score=None,
            cohort_first_reached_at=None,
            watermark_offset=2,
        )
        ok2 = store.apply_award_atomic(
            event_id="same_event_id",
            subject_id="learner_b",
            epoch="2026-W40",
            xp_amount=50,
            occurred_at="2026-10-01T11:05:00Z",
            global_score=50,
            global_first_reached_at="2026-10-01T11:05:00Z",
            cohort_id=None,
            cohort_score=None,
            cohort_first_reached_at=None,
            watermark_offset=3,
        )
        assert ok1 is True
        assert ok2 is True
    finally:
        store.close()
