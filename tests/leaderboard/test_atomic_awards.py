"""Tests for Week 4 LB-3: Atomic Leaderboard Transactions and State Management.

Validates:
1. apply_award_atomic rollback on failure:
   Injected failure between global and cohort writes rolls back both applied_event
   and global score rows; an unrelated commit on the connection does not persist
   partial state, and retry applies exactly once.
2. Concurrent / multi-projection score updates:
   Two projection instances on one SQLite file apply different awards with stale-cache
   ordering (A, B, A, B). Stored score equals the sum of applied events (220) and
   first_reached_at matches the chronological derivation.
3. Deterministic chronological tie-breaking for same-instant events:
   Two events sharing the exact same timestamp produce identical stored score,
   first_reached_at, applied-events ordering, and rankings regardless of arrival order.
4. InMemoryLeaderboardStore passes the exact same behavioral specifications.
5. assign_cohort atomicity:
   Removal of old cohort and upsert into new cohort executes in a single transaction.
"""

from datetime import datetime, timezone
import pytest

from src.leaderboard.models import LeaderboardEntry
from src.leaderboard.projection import LeaderboardProjection, normalize_to_utc_iso
from src.leaderboard.service import LeaderboardService
from src.leaderboard.store import InMemoryLeaderboardStore, LeaderboardStore, SQLiteLeaderboardStore
from src.state_machine.learner import Learner, LearnerApplication


@pytest.mark.parametrize("store_cls", ["sqlite", "in_memory"])
def test_atomic_rollback_on_cohort_write_failure(tmp_path, store_cls):
    """Defect 1 reproduction & fix validation:

    Inject a failure between global and cohort writes, then commit something
    unrelated on the same connection, then retry the same award.
    Assert global and cohort scores are both correct and retry applies exactly once.
    """
    epoch = "2026-W42"
    subject_id = "learner_atomic_1"
    cohort_id = "cohort_alpha"
    event_id = "award_evt_001"
    occurred_at = "2026-10-12T10:00:00Z"
    xp_amount = 100

    if store_cls == "sqlite":
        db_path = str(tmp_path / f"atomic_fail_{store_cls}.db")
        store = SQLiteLeaderboardStore(db_path)
    else:
        store = InMemoryLeaderboardStore()

    proj = LeaderboardProjection(store)
    # Assign cohort first
    proj.assign_cohort(subject_id, cohort_id, epoch, "2026-10-12T08:00:00Z", watermark_offset=1)

    # Verify initial cohort entry has 0 score
    cohort_init = store.get_entry("cohort", cohort_id, epoch, subject_id)
    assert cohort_init is not None
    assert cohort_init.score == 0

    # Inject failure between global and cohort writes
    setattr(store, "_fail_after_global_write", True)

    with pytest.raises(RuntimeError, match="Simulated failure between global and cohort writes"):
        proj.record_xp(
            subject_id=subject_id,
            xp_amount=xp_amount,
            occurred_at=occurred_at,
            epoch=epoch,
            watermark_offset=2,
            event_id=event_id,
        )

    # Simulate an unrelated commit on the same connection/store
    if store_cls == "sqlite":
        # Mutating operation on the connection to test if partial uncommitted state leaked
        store.connection.execute(
            "INSERT INTO leaderboard_checkpoints (scope_key, committed_offset, updated_at) VALUES ('unrelated', 99, '2026-10-12T10:05:00Z')"
        )
        store.connection.commit()
    else:
        store.set_committed_offset("unrelated", 99, "2026-10-12T10:05:00Z")

    # Assert that rollback occurred:
    # 1. Global score must NOT be 100 (must be 0 or nonexistent)
    global_entry = store.get_entry("global", "global", epoch, subject_id)
    assert global_entry is None or global_entry.score == 0

    # 2. Cohort score must remain 0
    cohort_entry = store.get_entry("cohort", cohort_id, epoch, subject_id)
    assert cohort_entry is not None
    assert cohort_entry.score == 0

    # 3. Applied event row must NOT exist in the store
    assert store.is_event_applied(event_id, subject_id) is False

    # Remove failure hook and retry the EXACT same award
    setattr(store, "_fail_after_global_write", False)
    applied = proj.record_xp(
        subject_id=subject_id,
        xp_amount=xp_amount,
        occurred_at=occurred_at,
        epoch=epoch,
        watermark_offset=2,
        event_id=event_id,
    )
    assert applied is True, "Retry of the award must succeed"

    # Assert both global and cohort scores are now 100
    global_after = store.get_entry("global", "global", epoch, subject_id)
    assert global_after is not None and global_after.score == 100
    assert global_after.first_reached_at == occurred_at

    cohort_after = store.get_entry("cohort", cohort_id, epoch, subject_id)
    assert cohort_after is not None and cohort_after.score == 100
    assert cohort_after.first_reached_at == occurred_at

    # Subsequent retry is rejected as duplicate
    duplicate_applied = proj.record_xp(
        subject_id=subject_id,
        xp_amount=xp_amount,
        occurred_at=occurred_at,
        epoch=epoch,
        watermark_offset=2,
        event_id=event_id,
    )
    assert duplicate_applied is False, "Duplicate delivery must be rejected"

    # Scores must remain exactly 100 (applied once)
    assert store.get_entry("global", "global", epoch, subject_id).score == 100
    assert store.get_entry("cohort", cohort_id, epoch, subject_id).score == 100

    store.close()


def test_two_projections_stale_cache_ordering_preserves_all_updates(tmp_path):
    """Defect 2 reproduction & fix validation:

    Two projection instances on one SQLite file apply different awards for the same learner
    with stale-cache ordering (A applies, B applies, A applies, B applies).
    Assert stored score equals the sum of applied events (220) and first_reached_at
    matches the chronological derivation.
    """
    db_path = str(tmp_path / "concurrent_projections.db")
    store_a = SQLiteLeaderboardStore(db_path)
    store_b = SQLiteLeaderboardStore(db_path)

    proj_a = LeaderboardProjection(store_a)
    proj_b = LeaderboardProjection(store_b)

    epoch = "2026-W42"
    subject_id = "learner_shared"

    # Step 1: Instance A applies 50 XP at 09:00 (offset 1)
    applied_a1 = proj_a.record_xp(
        subject_id=subject_id,
        xp_amount=50,
        occurred_at="2026-10-12T09:00:00Z",
        epoch=epoch,
        watermark_offset=1,
        event_id="evt_a1",
    )
    assert applied_a1 is True
    # At this point, store_a sees 50, proj_a cache has 50. Proj_b cache is still uninitialized (0).

    # Step 2: Instance B applies 60 XP at 09:30 (offset 2)
    applied_b1 = proj_b.record_xp(
        subject_id=subject_id,
        xp_amount=60,
        occurred_at="2026-10-12T09:30:00Z",
        epoch=epoch,
        watermark_offset=2,
        event_id="evt_b1",
    )
    assert applied_b1 is True
    # Stored score is now 50 + 60 = 110. Proj_b cache is 110.
    # CRITICAL: Proj_a cache is still STALE at 50!

    # Step 3: Instance A applies 60 XP at 10:00 (offset 3) with stale cache
    applied_a2 = proj_a.record_xp(
        subject_id=subject_id,
        xp_amount=60,
        occurred_at="2026-10-12T10:00:00Z",
        epoch=epoch,
        watermark_offset=3,
        event_id="evt_a2",
    )
    assert applied_a2 is True
    # With bug: proj_a calculated 50 + 60 = 110 and overwrote stored score to 110 (losing B's 60 XP).
    # With fix: store increments in SQL: 110 + 60 = 170. Proj_a cache refreshed to 170.
    # CRITICAL: Proj_b cache is still STALE at 110!

    # Step 4: Instance B applies 50 XP at 10:30 (offset 4) with stale cache
    applied_b2 = proj_b.record_xp(
        subject_id=subject_id,
        xp_amount=50,
        occurred_at="2026-10-12T10:30:00Z",
        epoch=epoch,
        watermark_offset=4,
        event_id="evt_b2",
    )
    assert applied_b2 is True
    # With bug: proj_b calculated stale 110 + 50 = 160 (or 170) -> stored 170!
    # With fix: store increments in SQL: 170 + 50 = 220. Proj_b cache refreshed to 220.

    # Assert stored score equals the sum of applied events: 50 + 60 + 60 + 50 = 220
    entry_a = store_a.get_entry("global", "global", epoch, subject_id)
    entry_b = store_b.get_entry("global", "global", epoch, subject_id)
    assert entry_a is not None and entry_b is not None
    assert entry_a.score == 220, f"Expected 220 but found {entry_a.score}"
    assert entry_b.score == 220, f"Expected 220 but found {entry_b.score}"

    applied_events = store_a.get_applied_events(subject_id, epoch)
    assert len(applied_events) == 4
    assert sum(e["xp_amount"] for e in applied_events) == 220

    # Assert first_reached_at matches the chronological derivation
    # Timeline:
    # 09:00: 50 XP
    # 09:30: 110 XP
    # 10:00: 170 XP
    # 10:30: 220 XP (achieved 220 for the first time at 10:30)
    assert entry_a.first_reached_at == "2026-10-12T10:30:00Z"
    assert entry_b.first_reached_at == "2026-10-12T10:30:00Z"

    store_a.close()
    store_b.close()


@pytest.mark.parametrize("store_type", ["sqlite", "in_memory"])
def test_same_instant_events_produce_identical_results_regardless_of_arrival_order(tmp_path, store_type):
    """Specification 3:

    Two events at the same instant produce the exact same result regardless of arrival order.
    Validates deterministic tie-breaking by (applied_at, stream_offset, event_id).
    """
    instant = "2026-10-12T11:00:00Z"
    epoch = "2026-W42"
    subject_id = "learner_same_instant"

    # Trial 1: Event 1 arrives first, then Event 2
    if store_type == "sqlite":
        store1 = SQLiteLeaderboardStore(str(tmp_path / "order1.db"))
        store2 = SQLiteLeaderboardStore(str(tmp_path / "order2.db"))
    else:
        store1 = InMemoryLeaderboardStore()
        store2 = InMemoryLeaderboardStore()

    proj1 = LeaderboardProjection(store1)
    proj2 = LeaderboardProjection(store2)

    # Event 1: offset 1, event_id 'ev_alpha', 50 XP
    # Event 2: offset 2, event_id 'ev_beta', 30 XP
    # Both share exact same occurred_at timestamp

    # Trial 1: Order 1 -> 2
    proj1.record_xp(subject_id, 50, instant, epoch=epoch, watermark_offset=1, event_id="ev_alpha")
    proj1.record_xp(subject_id, 30, instant, epoch=epoch, watermark_offset=2, event_id="ev_beta")

    # Trial 2: Order 2 -> 1 (reversed arrival)
    proj2.record_xp(subject_id, 30, instant, epoch=epoch, watermark_offset=2, event_id="ev_beta")
    proj2.record_xp(subject_id, 50, instant, epoch=epoch, watermark_offset=1, event_id="ev_alpha")

    # Stored entries must be identical
    entry1 = store1.get_entry("global", "global", epoch, subject_id)
    entry2 = store2.get_entry("global", "global", epoch, subject_id)
    assert entry1 is not None and entry2 is not None
    assert entry1.score == 80
    assert entry2.score == 80
    assert entry1.first_reached_at == entry2.first_reached_at == instant
    assert entry1.watermark_offset == entry2.watermark_offset == 2

    # Applied events list must be sorted deterministically in both stores
    events1 = store1.get_applied_events(subject_id, epoch)
    events2 = store2.get_applied_events(subject_id, epoch)
    assert [e["event_id"] for e in events1] == ["ev_alpha", "ev_beta"]
    assert [e["event_id"] for e in events2] == ["ev_alpha", "ev_beta"]

    # Rankings must be completely identical
    rank1 = store1.get_rank("global", "global", epoch, subject_id)
    rank2 = store2.get_rank("global", "global", epoch, subject_id)
    assert rank1 is not None and rank2 is not None
    assert rank1.rank == rank2.rank == 1
    assert rank1.score == rank2.score == 80

    store1.close()
    store2.close()


@pytest.mark.parametrize("store_type", ["sqlite", "in_memory"])
def test_assign_cohort_atomic_reassignment(tmp_path, store_type):
    """Specification 1:

    assign_cohort executes remove_member and upsert_score in a single atomic transaction.
    """
    if store_type == "sqlite":
        store = SQLiteLeaderboardStore(str(tmp_path / "cohort_atomic.db"))
    else:
        store = InMemoryLeaderboardStore()

    proj = LeaderboardProjection(store)
    epoch = "2026-W42"
    subject_id = "learner_cohort_move"

    # Give learner 100 XP globally
    proj.record_xp(subject_id, 100, "2026-10-12T09:00:00Z", epoch=epoch, watermark_offset=1, event_id="e1")

    # Assign to cohort A
    proj.assign_cohort(subject_id, "cohort_A", epoch, "2026-10-12T09:30:00Z", watermark_offset=2)
    entry_a = store.get_entry("cohort", "cohort_A", epoch, subject_id)
    assert entry_a is not None and entry_a.score == 100

    # Reassign to cohort B
    proj.assign_cohort(subject_id, "cohort_B", epoch, "2026-10-12T10:00:00Z", watermark_offset=3)

    # Cohort A entry must be removed, Cohort B must have the score
    assert store.get_entry("cohort", "cohort_A", epoch, subject_id) is None
    entry_b = store.get_entry("cohort", "cohort_B", epoch, subject_id)
    assert entry_b is not None and entry_b.score == 100

    store.close()


@pytest.mark.parametrize("store_type", ["sqlite", "in_memory"])
def test_atomic_awards_with_real_domain_events(tmp_path, store_type):
    """Drives real aggregate domain events generated by Learner aggregate via proj.apply."""
    events_db = str(tmp_path / f"events_{store_type}.db")
    app = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": events_db}
    )

    if store_type == "sqlite":
        store = SQLiteLeaderboardStore(str(tmp_path / f"domain_events_{store_type}.db"))
    else:
        store = InMemoryLeaderboardStore()

    proj = LeaderboardProjection(store)
    epoch = "2026-W42"
    cohort_id = "cohort_aggr"

    # Create learner aggregate and record cohort assignment + lesson completion
    learner = Learner()
    learner.assign_cohort(cohort_id, epoch, assigned_at="2026-10-12T08:00:00Z")
    learner.complete_lesson("c100", "l1", occurred_at="2026-10-12T09:00:00Z", timezone="UTC")
    app.save(learner)

    # Collect and apply domain events
    domain_events = list(app.events.get(learner.id))
    for idx, event in enumerate(domain_events):
        proj.apply(event, watermark_offset=idx + 1)

    subject_id = str(learner.id)
    g_entry = store.get_entry("global", "global", epoch, subject_id)
    c_entry = store.get_entry("cohort", cohort_id, epoch, subject_id)
    assert g_entry is not None and g_entry.score == 100
    assert c_entry is not None and c_entry.score == 100
    assert g_entry.first_reached_at == "2026-10-12T09:00:00Z"
    assert c_entry.first_reached_at == "2026-10-12T09:00:00Z"

    # Duplicate delivery of the events
    for idx, event in enumerate(domain_events):
        status = proj.apply(event, watermark_offset=idx + 1)
        if isinstance(event, Learner.XPAwarded):
            assert status == "duplicate"

    assert store.get_entry("global", "global", epoch, subject_id).score == 100
    assert store.get_entry("cohort", cohort_id, epoch, subject_id).score == 100

    store.close()
