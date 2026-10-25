"""Tests for contiguous checkpoint tracking, processed_offsets, and watermark lag."""

import pytest

from src.leaderboard.cohorts import get_calendar_week
from src.leaderboard.projection import LeaderboardProjection
from src.leaderboard.service import LeaderboardService
from src.leaderboard.store import InMemoryLeaderboardStore, SQLiteLeaderboardStore
from src.state_machine.learner import Learner, LearnerApplication


@pytest.fixture
def make_store(tmp_path):
    stores = []

    def _factory(store_type: str):
        if store_type == "sqlite":
            db_path = str(tmp_path / f"leaderboard_{len(stores)}.db")
            store = SQLiteLeaderboardStore(db_path)
        else:
            store = InMemoryLeaderboardStore()
        stores.append(store)
        return store

    yield _factory

    for s in stores:
        s.close()


@pytest.mark.parametrize("store_type", ["sqlite", "in_memory"])
def test_rebuilding_10_event_stream_with_xp_at_3_6_9_ends_with_committed_offset_10(
    store_type, make_store, tmp_path
):
    """Rebuilding a 10-event stream with XP at offsets 3, 6, 9 ends with committed offset 10."""
    app = LearnerApplication(
        env={
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(tmp_path / "events.db"),
        }
    )
    store = make_store(store_type)
    projection = LeaderboardProjection(store)
    service = LeaderboardService(store)

    epoch = "2026-W42"

    # Single learner completing 3 lessons produces exactly 10 domain events:
    # 1: Created, 2: LessonCompleted, 3: XPAwarded, 4: StreakUpdated,
    # 5: LessonCompleted, 6: XPAwarded, 7: StreakUpdated,
    # 8: LessonCompleted, 9: XPAwarded, 10: StreakUpdated
    l1 = Learner()
    l1.complete_lesson("c1", "l1", occurred_at="2026-10-12T09:00:00Z", timezone="UTC")
    l1.complete_lesson("c2", "l2", occurred_at="2026-10-12T10:00:00Z", timezone="UTC")
    l1.complete_lesson("c3", "l3", occurred_at="2026-10-12T11:00:00Z", timezone="UTC")
    app.save(l1)

    stream = list(app.events.get(l1.id))
    assert len(stream) == 10

    # Offsets 3, 6, 9 (1-indexed) are XPAwarded; all others are non-XP (Created, LessonCompleted, StreakUpdated)
    assert isinstance(stream[2], Learner.XPAwarded)
    assert isinstance(stream[5], Learner.XPAwarded)
    assert isinstance(stream[8], Learner.XPAwarded)
    assert not isinstance(stream[0], Learner.XPAwarded)
    assert not isinstance(stream[1], Learner.XPAwarded)
    assert not isinstance(stream[3], Learner.XPAwarded)
    assert not isinstance(stream[4], Learner.XPAwarded)
    assert not isinstance(stream[6], Learner.XPAwarded)
    assert not isinstance(stream[7], Learner.XPAwarded)
    assert not isinstance(stream[9], Learner.XPAwarded)

    # Rebuild from genesis stream
    projection.rebuild(stream)

    # The committed offset must end at 10, not stuck at 0
    assert store.get_committed_offset("global") == 10
    assert service.get_watermark("global", "global", epoch) == 10
    assert projection._watermark == 10

    app.close()


@pytest.mark.parametrize("store_type", ["sqlite", "in_memory"])
def test_out_of_order_arrival_stays_at_checkpoint_until_gaps_fill(
    store_type, make_store, tmp_path
):
    """Out-of-order arrival (1, 2, 5, then 3, 4): checkpoint stays at 2 until 3 and 4 arrive, then jumps to 5."""
    app = LearnerApplication(
        env={
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(tmp_path / "events_ooo.db"),
        }
    )
    store = make_store(store_type)
    projection = LeaderboardProjection(store)
    service = LeaderboardService(store)
    epoch = "2026-W42"

    l = Learner()
    l.assign_cohort("cohort_alpha", epoch, assigned_at="2026-10-12T08:00:00Z")
    l.complete_lesson("c1", "l1", occurred_at="2026-10-12T09:00:00Z", timezone="UTC")
    l.complete_lesson("c2", "l2", occurred_at="2026-10-12T09:30:00Z", timezone="UTC")
    app.save(l)

    events = list(app.events.get(l.id))
    # Events: Created (1), CohortAssigned (2), LessonCompleted (3), XPAwarded (4), StreakUpdated (5)...
    assert len(events) >= 5

    # 1. Apply offset 1
    projection.apply(events[0], offset=1)
    assert store.get_committed_offset("global") == 1

    # 2. Apply offset 2
    projection.apply(events[1], offset=2)
    assert store.get_committed_offset("global") == 2

    # 3. Apply offset 5 (missing 3 and 4)
    projection.apply(events[4], offset=5)
    # Checkpoint must stay at 2 because 3 and 4 are missing!
    assert store.get_committed_offset("global") == 2
    assert service.get_watermark("global", "global", epoch) == 2

    # 4. Duplicate delivery of offset 5 is a no-op that does not move checkpoint backwards
    dup_status = projection.apply(events[4], offset=5)
    assert dup_status == "duplicate"
    assert store.get_committed_offset("global") == 2

    # 5. Late arrival of offset 3
    projection.apply(events[2], offset=3)
    # Checkpoint advances to 3 (still missing 4)
    assert store.get_committed_offset("global") == 3
    assert service.get_watermark("global", "global", epoch) == 3

    # 6. Late arrival of offset 4
    projection.apply(events[3], offset=4)
    # Prefix 1..5 is now complete -> checkpoint jumps to 5!
    assert store.get_committed_offset("global") == 5
    assert service.get_watermark("global", "global", epoch) == 5

    # 7. Redelivering an earlier offset (e.g. 2) does not move checkpoint backwards
    dup_2_status = projection.apply(events[1], offset=2)
    assert dup_2_status == "duplicate"
    assert store.get_committed_offset("global") == 5

    app.close()


@pytest.mark.parametrize("store_type", ["sqlite", "in_memory"])
def test_late_offset_arriving_after_higher_is_recorded_under_own_offset(
    store_type, make_store, tmp_path
):
    """A late offset arriving after a higher one is recorded under its own offset, not the maximum."""
    app = LearnerApplication(
        env={
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(tmp_path / "events_late.db"),
        }
    )
    store = make_store(store_type)
    projection = LeaderboardProjection(store)

    l_high = Learner()
    l_high.complete_lesson("c_high", "l1", occurred_at="2026-10-12T10:00:00Z", timezone="UTC")
    app.save(l_high)

    l_late = Learner()
    l_late.complete_lesson("c_late", "l1", occurred_at="2026-10-12T09:00:00Z", timezone="UTC")
    app.save(l_late)

    high_events = list(app.events.get(l_high.id))
    high_lesson = [e for e in high_events if isinstance(e, Learner.LessonCompleted)][0]
    high_xp = [e for e in high_events if isinstance(e, Learner.XPAwarded)][0]

    late_events = list(app.events.get(l_late.id))
    late_lesson = [e for e in late_events if isinstance(e, Learner.LessonCompleted)][0]
    late_xp = [e for e in late_events if isinstance(e, Learner.XPAwarded)][0]

    # Pre-register lesson completion times so XP awards can resolve timestamps
    projection.apply(late_lesson, offset=2)
    projection.apply(high_lesson, offset=4)

    # 1. Higher offset arrives first (offset 5)
    projection.apply(high_xp, offset=5)

    # 2. Late offset arrives second (offset 3, arriving after 5)
    projection.apply(late_xp, offset=3)

    # Late event must be recorded under its OWN stream_offset 3, NOT maximum (5)!
    late_applied = store.get_applied_events(subject_id=str(l_late.id))
    assert len(late_applied) == 1
    assert late_applied[0]["stream_offset"] == 3

    high_applied = store.get_applied_events(subject_id=str(l_high.id))
    assert len(high_applied) == 1
    assert high_applied[0]["stream_offset"] == 5

    app.close()


def test_checkpoint_survives_restart(tmp_path):
    """The checkpoint survives a restart (new projection on the same file)."""
    db_path = str(tmp_path / "leaderboard_restart.db")
    events_db = str(tmp_path / "events_restart.db")

    app = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": events_db}
    )

    l = Learner()
    l.complete_lesson("c1", "l1", occurred_at="2026-10-12T09:00:00Z", timezone="UTC")
    l.complete_lesson("c2", "l2", occurred_at="2026-10-12T09:30:00Z", timezone="UTC")
    app.save(l)

    events = list(app.events.get(l.id))

    # Phase 1: Projection instance 1 applies first 3 events
    store1 = SQLiteLeaderboardStore(db_path)
    proj1 = LeaderboardProjection(store1)

    proj1.apply(events[0], offset=1)
    proj1.apply(events[1], offset=2)
    proj1.apply(events[2], offset=3)

    assert store1.get_committed_offset("global") == 3
    assert proj1._watermark == 3

    store1.close()

    # Phase 2: Complete process restart - new store and new projection instance on same SQLite file
    store2 = SQLiteLeaderboardStore(db_path)
    proj2 = LeaderboardProjection(store2)

    # Checkpoint must have survived restart
    assert store2.get_committed_offset("global") == 3
    assert proj2._watermark == 3

    # Apply event 4 on new projection instance
    proj2.apply(events[3], offset=4)

    assert store2.get_committed_offset("global") == 4
    assert proj2._watermark == 4

    store2.close()
    app.close()


@pytest.mark.parametrize("store_type", ["sqlite", "in_memory"])
def test_check_watermark_lag_reports_lag_during_gap_and_consistent_after_fills(
    store_type, make_store, tmp_path
):
    """check_watermark_lag reports lag during a gap and consistent after it fills."""
    app = LearnerApplication(
        env={
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(tmp_path / "events_lag.db"),
        }
    )
    store = make_store(store_type)
    projection = LeaderboardProjection(store)
    service = LeaderboardService(store)
    epoch = "2026-W42"

    l = Learner()
    l.assign_cohort("cohort_test", epoch, assigned_at="2026-10-12T08:00:00Z")
    l.complete_lesson("c1", "l1", occurred_at="2026-10-12T09:00:00Z", timezone="UTC")
    l.complete_lesson("c2", "l2", occurred_at="2026-10-12T09:30:00Z", timezone="UTC")
    app.save(l)

    events = list(app.events.get(l.id))

    # Apply offsets 1, 2, then jump to 5 (gap at 3, 4)
    projection.apply(events[0], offset=1)
    projection.apply(events[1], offset=2)
    projection.apply(events[4], offset=5)

    # Check lag for required_watermark = 5
    lag_status = service.check_watermark_lag(
        "global", "global", epoch, required_watermark=5
    )
    assert lag_status["current_watermark"] == 2
    assert lag_status["required_watermark"] == 5
    assert lag_status["is_consistent"] is False
    assert lag_status["lag"] == 3

    # Now fill the gap with late-arriving events 3 and 4
    projection.apply(events[2], offset=3)
    projection.apply(events[3], offset=4)

    # Check lag again: prefix is now complete, lag is 0, is_consistent is True
    filled_status = service.check_watermark_lag(
        "global", "global", epoch, required_watermark=5
    )
    assert filled_status["current_watermark"] == 5
    assert filled_status["required_watermark"] == 5
    assert filled_status["is_consistent"] is True
    assert filled_status["lag"] == 0

    app.close()
