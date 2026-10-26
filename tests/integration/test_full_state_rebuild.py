"""AT-15: Full State Rebuild (M2, M7, M8)

Given a simulated database wipe for the full synthetic population, when the event log
is replayed from offset 0, then all learners’ {XP, StreakCount, Freezes} match their
pre-wipe values exactly, and leaderboard/cohort projections rebuild to the same ranks.
"""

from src.scale_and_recovery.rebuild import FullStateRebuildHarness
from src.scale_and_recovery.workload import WorkloadProfile, generate_synthetic_workload


def test_full_state_rebuild(tmp_path):
    """Proves AT-15: Replaying authoritative SQLite event log from offset 0 completely restores state and ranks."""
    events_db = str(tmp_path / "events_at15.db")
    leaderboard_db = str(tmp_path / "leaderboard_at15.db")
    epoch = "2026-W42"

    # Pinned small reproducibility workload fixture
    profile = WorkloadProfile(
        population_size=15,
        cohort_count=2,
        events_per_learner=12,
        duplicate_rate=0.05,
        late_rate=0.05,
        seed=42,
        epoch=epoch,
    )
    workload = generate_synthetic_workload(profile)
    assert workload.total_events > 150

    harness = FullStateRebuildHarness(events_db, leaderboard_db, epoch=epoch)
    rebuild_result = harness.run_rebuild_verification(workload)

    # 1. State vectors and leaderboard ranks must match bit-identically across all learners
    assert rebuild_result.bit_identical is True
    assert rebuild_result.mismatches == []
    assert rebuild_result.pre_checksum == rebuild_result.post_checksum
    assert len(rebuild_result.pre_checksum) == 64

    # 2. Replay must process all domain events and meet throughput SLA
    assert rebuild_result.total_learners == 15
    assert rebuild_result.total_events > 500  # Total notifications generated
    assert rebuild_result.rebuild_duration_sec < 10.0  # Well within recovery bound
    assert rebuild_result.throughput_events_per_sec > 100.0  # >100 events/sec throughput SLA
