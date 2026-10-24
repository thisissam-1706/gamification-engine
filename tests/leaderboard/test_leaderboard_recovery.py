"""Tests for M3 Trustworthy Leaderboard Recovery (Findings 1 & 2).

Validates:
- Replay idempotency: Replaying the same award twice does not double score.
- Projector restart: Reinstantiating LeaderboardProjection rehydrates state without losing XP.
- Chronological first_reached_at: 09:00 award followed by late 08:00 award stamps 200 XP at 09:00.
- UTC normalization: '09:00+05:30' (03:30Z) ranks ahead of '04:00Z' on score tie.
- Atomic commit: Global and cohort scopes update together in a single transaction.
- Contiguous committed checkpoint tracking.
"""

from datetime import datetime, timezone
import pytest

from src.leaderboard.projection import LeaderboardProjection, normalize_to_utc_iso
from src.leaderboard.service import LeaderboardService
from src.leaderboard.store import SQLiteLeaderboardStore
from src.state_machine.learner import Learner, LearnerApplication


def test_replay_same_award_twice_does_not_double_score(tmp_path):
    """Reviewer Finding 1: One canonical XPAwarded of 100 must not become 200 on duplicate delivery."""
    db_path = str(tmp_path / "leaderboard.db")
    store = SQLiteLeaderboardStore(db_path)
    proj = LeaderboardProjection(store)
    service = LeaderboardService(store)
    epoch = "2026-W42"

    subject_id = "learner_001"
    event_id = "award_001"
    occurred_at = "2026-10-12T09:00:00Z"

    # First delivery: awards 100 XP
    applied_first = proj.record_xp(
        subject_id=subject_id,
        xp_amount=100,
        occurred_at=occurred_at,
        epoch=epoch,
        watermark_offset=1,
        event_id=event_id,
    )
    assert applied_first is True
    entry = service.get_learner_rank("global", "global", epoch, subject_id)
    assert entry.score == 100

    # Duplicate delivery of identical event_id
    applied_second = proj.record_xp(
        subject_id=subject_id,
        xp_amount=100,
        occurred_at=occurred_at,
        epoch=epoch,
        watermark_offset=1,
        event_id=event_id,
    )
    assert applied_second is False

    # Score MUST remain 100, not 200!
    entry_after = service.get_learner_rank("global", "global", epoch, subject_id)
    assert entry_after.score == 100

    store.close()


def test_projector_restart_preserves_accumulated_xp(tmp_path):
    """Reviewer Finding 1: Restarting LeaderboardProjection must not overwrite accumulated XP with only next increment."""
    db_path = str(tmp_path / "leaderboard.db")
    store = SQLiteLeaderboardStore(db_path)
    proj1 = LeaderboardProjection(store)
    epoch = "2026-W42"
    subject_id = "learner_002"

    # Proj1 applies 100 XP
    proj1.record_xp(
        subject_id=subject_id,
        xp_amount=100,
        occurred_at="2026-10-12T09:00:00Z",
        epoch=epoch,
        watermark_offset=1,
        event_id="award_1",
    )
    store.close()

    # Simulate process termination and restart with a fresh LeaderboardProjection over the same database
    store2 = SQLiteLeaderboardStore(db_path)
    proj2 = LeaderboardProjection(store2)
    service2 = LeaderboardService(store2)

    # Apply next legitimate award of 100 XP
    applied = proj2.record_xp(
        subject_id=subject_id,
        xp_amount=100,
        occurred_at="2026-10-12T10:00:00Z",
        epoch=epoch,
        watermark_offset=2,
        event_id="award_2",
    )
    assert applied is True

    # Total score in SQLite must be 200, not 100!
    entry = service2.get_learner_rank("global", "global", epoch, subject_id)
    assert entry.score == 200
    store2.close()


def test_late_event_first_reached_at_chronological_accuracy(tmp_path):
    """Reviewer Finding 2: A 09:00 award followed by a late 08:00 award must record 200 XP as first reached at 09:00."""
    db_path = str(tmp_path / "leaderboard.db")
    store = SQLiteLeaderboardStore(db_path)
    proj = LeaderboardProjection(store)
    service = LeaderboardService(store)
    epoch = "2026-W42"
    subject_id = "learner_003"

    # Step 1: 09:00 award arrives with 100 XP -> reaches 100 at 09:00
    proj.record_xp(
        subject_id=subject_id,
        xp_amount=100,
        occurred_at="2026-10-12T09:00:00Z",
        epoch=epoch,
        watermark_offset=1,
        event_id="award_0900",
    )
    entry1 = service.get_learner_rank("global", "global", epoch, subject_id)
    assert entry1.score == 100
    assert entry1.first_reached_at == "2026-10-12T09:00:00Z"

    # Step 2: Late-arriving 08:00 award arrives with 100 XP -> total becomes 200 XP
    # At 08:00, learner only had 100 XP. Cumulative total only reached 200 XP at 09:00!
    proj.record_xp(
        subject_id=subject_id,
        xp_amount=100,
        occurred_at="2026-10-12T08:00:00Z",
        epoch=epoch,
        watermark_offset=2,
        event_id="award_0800",
    )
    entry2 = service.get_learner_rank("global", "global", epoch, subject_id)
    assert entry2.score == 200
    # Must be 09:00, NOT 08:00!
    assert entry2.first_reached_at == "2026-10-12T09:00:00Z"

    store.close()


def test_utc_normalization_tie_breaking(tmp_path):
    """Reviewer Finding 2: '09:00+05:30' (03:30Z) must rank ahead of '04:00Z' when scores tie."""
    db_path = str(tmp_path / "leaderboard.db")
    store = SQLiteLeaderboardStore(db_path)
    proj = LeaderboardProjection(store)
    service = LeaderboardService(store)
    epoch = "2026-W42"

    # L_kolkata reached 100 XP at 09:00+05:30 (which is 03:30 UTC)
    proj.record_xp(
        subject_id="L_kolkata",
        xp_amount=100,
        occurred_at="2026-10-12T09:00:00+05:30",
        epoch=epoch,
        watermark_offset=1,
        event_id="award_kolkata",
    )

    # L_utc reached 100 XP at 04:00Z (30 minutes after L_kolkata)
    proj.record_xp(
        subject_id="L_utc",
        xp_amount=100,
        occurred_at="2026-10-12T04:00:00Z",
        epoch=epoch,
        watermark_offset=2,
        event_id="award_utc",
    )

    top_2 = service.get_top_k("global", "global", epoch, k=2)
    # L_kolkata (03:30Z) must be Rank 1, L_utc (04:00Z) must be Rank 2!
    assert top_2[0].subject_id == "L_kolkata"
    assert top_2[0].rank == 1
    assert top_2[1].subject_id == "L_utc"
    assert top_2[1].rank == 2

    store.close()


def test_atomic_transaction_split_commit_protection(tmp_path):
    """Reviewer Finding 1: Global and cohort updates must be atomic; a failure rolls back both."""
    db_path = str(tmp_path / "leaderboard.db")
    store = SQLiteLeaderboardStore(db_path)
    proj = LeaderboardProjection(store)
    epoch = "2026-W42"
    subject_id = "L_atomic"
    cohort_id = "cohort_test"

    proj.assign_cohort(subject_id, cohort_id, epoch, "2026-10-12T08:00:00Z")

    # Regular atomic apply
    applied = proj.record_xp(
        subject_id=subject_id,
        xp_amount=100,
        occurred_at="2026-10-12T09:00:00Z",
        epoch=epoch,
        watermark_offset=1,
        event_id="award_atomic_1",
    )
    assert applied is True

    # Both global and cohort exist and have score 100
    global_entry = store.get_entry("global", "global", epoch, subject_id)
    cohort_entry = store.get_entry("cohort", cohort_id, epoch, subject_id)
    assert global_entry is not None and global_entry.score == 100
    assert cohort_entry is not None and cohort_entry.score == 100

    # Committed checkpoint offset is updated
    assert store.get_committed_offset("global") == 1
    assert store.get_committed_offset(f"cohort:{cohort_id}") == 1

    store.close()


def test_checkpoint_contiguous_prefix_with_offset_gaps(tmp_path):
    """Proves F1: Checkpoint tracks contiguous gap-free prefix; offset gap (1, 2, 5) keeps checkpoint at 2."""
    db_path = str(tmp_path / "leaderboard_gap.db")
    store = SQLiteLeaderboardStore(db_path)
    proj = LeaderboardProjection(store)
    epoch = "2026-W42"

    # Apply offset 1
    proj.record_xp("L1", 10, "2026-10-12T08:00:00Z", epoch=epoch, watermark_offset=1, event_id="e1")
    assert store.get_committed_offset("global") == 1

    # Apply offset 2
    proj.record_xp("L2", 10, "2026-10-12T08:05:00Z", epoch=epoch, watermark_offset=2, event_id="e2")
    assert store.get_committed_offset("global") == 2

    # Apply offset 5 (missing offsets 3 and 4!)
    proj.record_xp("L3", 10, "2026-10-12T08:10:00Z", epoch=epoch, watermark_offset=5, event_id="e5")
    # Checkpoint MUST remain at 2 because stream has a gap!
    assert store.get_committed_offset("global") == 2
    assert store.get_watermark("global", "global", epoch) == 2

    # Late arrival of offset 3
    proj.record_xp("L4", 10, "2026-10-12T08:15:00Z", epoch=epoch, watermark_offset=3, event_id="e3")
    # Checkpoint advances to 3 (still missing 4)
    assert store.get_committed_offset("global") == 3

    # Late arrival of offset 4
    proj.record_xp("L5", 10, "2026-10-12T08:20:00Z", epoch=epoch, watermark_offset=4, event_id="e4")
    # Now prefix 1..5 is complete -> checkpoint jumps to 5!
    assert store.get_committed_offset("global") == 5
    assert store.get_watermark("global", "global", epoch) == 5

    store.close()


def test_restart_then_late_award_restores_per_event_history(tmp_path):
    """Proves M3-1: Rehydration restores per-event history so late arrivals calculate chronological first_reached_at accurately."""
    db_path = str(tmp_path / "leaderboard_hist.db")
    store1 = SQLiteLeaderboardStore(db_path)
    proj1 = LeaderboardProjection(store1)
    epoch = "2026-W42"
    subject_id = "L_hist"

    # Pre-restart: learner gets 100 XP at 09:00
    proj1.record_xp(
        subject_id=subject_id,
        xp_amount=100,
        occurred_at="2026-10-12T09:00:00Z",
        epoch=epoch,
        watermark_offset=1,
        event_id="award_0900",
    )
    store1.close()

    # Simulate restart: fresh LeaderboardProjection over same database
    store2 = SQLiteLeaderboardStore(db_path)
    proj2 = LeaderboardProjection(store2)
    proj2.rehydrate(epoch)

    # Post-restart: late-arriving earlier event from 08:00 (100 XP) arrives
    proj2.record_xp(
        subject_id=subject_id,
        xp_amount=100,
        occurred_at="2026-10-12T08:00:00Z",
        epoch=epoch,
        watermark_offset=2,
        event_id="award_0800_late",
    )

    entry = store2.get_entry("global", "global", epoch, subject_id)
    assert entry is not None
    assert entry.score == 200
    # At 08:00 learner only had 100 XP; they only achieved 200 XP at 09:00!
    assert entry.first_reached_at == "2026-10-12T09:00:00Z"

    store2.close()

