"""AT-7: Leaderboard Reconciliation (M3)

Given projection and canonical state diverge on L1’s score by a dropped event,
when the daily reconciliation job runs, then it applies a differential correction
(not a full flush) and resolves any tie using occurred_at of the first event
reaching that score, then subject_id.
"""

import pytest

from src.leaderboard.reconciliation import LeaderboardReconciler
from src.leaderboard.service import LeaderboardService
from src.leaderboard.store import SQLiteLeaderboardStore, InMemoryLeaderboardStore


@pytest.mark.parametrize("store_type", ["sqlite", "in_memory"])
def test_leaderboard_reconciliation_applies_differential_corrections(tmp_path, store_type):
    epoch = "2026-W41"
    cohort_id = "cohort_42"

    if store_type == "sqlite":
        db_path = str(tmp_path / "recon_test.db")
        store = SQLiteLeaderboardStore(db_path)
    else:
        store = InMemoryLeaderboardStore()

    service = LeaderboardService(store)
    reconciler = LeaderboardReconciler(store)

    # 1. Establish state in read projection where L1 suffered a dropped event
    # Canonical reality: L1 has 200 XP (reached at 09:00:00), L2 has 200 XP (reached at 09:30:00)
    # But read projection only has 100 XP for L1 (diverged)
    store.upsert_score("cohort", cohort_id, epoch, "L1", 100, "2026-10-12T08:30:00Z", "2026-10-12T08:30:00Z")
    store.upsert_score("cohort", cohort_id, epoch, "L2", 200, "2026-10-12T09:30:00Z", "2026-10-12T09:30:00Z")
    store.upsert_score("cohort", cohort_id, epoch, "L_phantom", 50, "2026-10-12T08:00:00Z", "2026-10-12T08:00:00Z")

    # In diverged projection: L2 is Rank 1, L1 is Rank 2
    r_before_l1 = service.get_learner_rank("cohort", cohort_id, epoch, "L1")
    r_before_l2 = service.get_learner_rank("cohort", cohort_id, epoch, "L2")
    assert r_before_l2.rank == 1
    assert r_before_l1.rank == 2

    # 2. Canonical truth from authoritative event store
    canonical_state = {
        "L1": (200, "2026-10-12T09:00:00Z"),
        "L2": (200, "2026-10-12T09:30:00Z"),
        "L3_new": (50, "2026-10-12T09:45:00Z"),
    }

    # 3. Run reconciliation job
    audit = reconciler.reconcile_scope(
        canonical_state=canonical_state,
        scope_type="cohort",
        scope_id=cohort_id,
        epoch=epoch,
        watermark_offset=105,
    )

    # 4. Assert differential corrections (pinpoint updates, NO full flush)
    assert audit.divergence_count == 3  # L1 updated, L3_new inserted, L_phantom deleted
    assert audit.updated_count == 1
    assert audit.inserted_count == 1
    assert audit.deleted_count == 1
    assert "L1" in audit.corrected_subject_ids
    assert "L3_new" in audit.corrected_subject_ids
    assert "L_phantom" in audit.corrected_subject_ids

    # L2 was completely untouched by differential updates
    assert "L2" not in audit.corrected_subject_ids

    # 5. Assert tie resolution after correction
    # Both L1 and L2 have 200 XP.
    # L1 reached 200 XP at 09:00:00 (earlier), so L1 MUST be Rank 1!
    # L2 reached 200 XP at 09:30:00 (later), so L2 MUST be Rank 2!
    top_3 = service.get_top_k("cohort", cohort_id, epoch, k=3)
    assert len(top_3) == 3

    assert top_3[0].subject_id == "L1"
    assert top_3[0].rank == 1
    assert top_3[0].score == 200

    assert top_3[1].subject_id == "L2"
    assert top_3[1].rank == 2
    assert top_3[1].score == 200

    assert top_3[2].subject_id == "L3_new"
    assert top_3[2].rank == 3
    assert top_3[2].score == 50

    # Assert phantom was purged
    assert service.get_learner_rank("cohort", cohort_id, epoch, "L_phantom") is None

    store.close()
