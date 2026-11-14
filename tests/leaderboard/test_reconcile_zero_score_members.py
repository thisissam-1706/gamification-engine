"""Reconciling a cohort must keep assigned members who have no XP yet (score-0 entries).

Cohort membership IS the cohort-scope rows of the read model, and canonical state only lists subjects with XP,
so a zero-score member used to look like an orphan and was deleted (membership lost).
"""

import pytest

from src.leaderboard.models import CohortDefinition
from src.leaderboard.projection import LeaderboardProjection
from src.leaderboard.reconciliation import LeaderboardReconciler
from src.leaderboard.store import InMemoryLeaderboardStore, SQLiteLeaderboardStore

EPOCH = "2026-W42"
COHORT = "cohort_alpha"


@pytest.fixture(params=["sqlite", "in_memory"])
def store(request, tmp_path):
    s = SQLiteLeaderboardStore(str(tmp_path / "board.db")) if request.param == "sqlite" else InMemoryLeaderboardStore()
    yield s
    s.close()


@pytest.fixture
def cohort(store):
    """Three assigned members: L1 has 100 XP, L2 and L3 have none."""
    projection = LeaderboardProjection(store)
    projection.register_cohort(CohortDefinition(COHORT, strategy="activity_volume", epoch=EPOCH, status="forming"))
    for n, learner in enumerate(("L1", "L2", "L3"), start=1):
        projection.assign_cohort(learner, COHORT, EPOCH, "2026-10-12T08:00:00Z", watermark_offset=n)
    assert projection.record_xp("L1", 100, "2026-10-12T09:00:00Z", epoch=EPOCH, watermark_offset=10)
    return store


def _rows(store, scope_type="cohort", scope_id=COHORT):
    return {e.subject_id: e.score for e in store.get_all_entries(scope_type, scope_id, EPOCH)}


def test_zero_score_cohort_members_are_kept(cohort):
    audit = LeaderboardReconciler(cohort).reconcile_scope("cohort", COHORT, EPOCH)

    assert audit.deleted_count == 0
    assert _rows(cohort) == {"L1": 100, "L2": 0, "L3": 0}


def test_a_phantom_entry_is_still_deleted(cohort):
    cohort.upsert_score("cohort", COHORT, EPOCH, "L_phantom", 50, "2026-10-12T08:00:00Z", "2026-10-12T08:00:00Z")

    audit = LeaderboardReconciler(cohort).reconcile_scope("cohort", COHORT, EPOCH)

    assert audit.deleted_count == 1
    assert audit.corrected_subject_ids == ("L_phantom",)
    assert _rows(cohort) == {"L1": 100, "L2": 0, "L3": 0}


def test_reconciling_twice_changes_nothing_the_second_time(cohort):
    reconciler = LeaderboardReconciler(cohort)
    cohort.upsert_score("cohort", COHORT, EPOCH, "L_phantom", 50, "2026-10-12T08:00:00Z", "2026-10-12T08:00:00Z")
    reconciler.reconcile_scope("cohort", COHORT, EPOCH)
    after_first = _rows(cohort)

    second = reconciler.reconcile_scope("cohort", COHORT, EPOCH)

    assert second.divergence_count == 0
    assert (second.inserted_count, second.updated_count, second.deleted_count) == (0, 0, 0)
    assert _rows(cohort) == after_first == {"L1": 100, "L2": 0, "L3": 0}


def test_a_zero_score_row_in_the_global_scope_is_still_removed(cohort):
    cohort.upsert_score("global", "global", EPOCH, "L_zero", 0, "2026-10-12T08:00:00Z", "2026-10-12T08:00:00Z")

    audit = LeaderboardReconciler(cohort).reconcile_scope("global", "global", EPOCH)

    assert audit.deleted_count == 1
    assert "L_zero" not in _rows(cohort, "global", "global")
