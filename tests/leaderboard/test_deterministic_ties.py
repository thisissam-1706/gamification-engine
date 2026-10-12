"""Tests for deterministic ranking, tie-breaking rules, and non-monotonic rank shifts."""

import pytest

from src.leaderboard.store import InMemoryLeaderboardStore, SQLiteLeaderboardStore
from src.leaderboard.service import LeaderboardService


@pytest.fixture(params=["sqlite", "in_memory"])
def store(request, tmp_path):
    if request.param == "sqlite":
        db_path = str(tmp_path / "leaderboard.db")
        s = SQLiteLeaderboardStore(db_path)
        yield s
        s.close()
    else:
        s = InMemoryLeaderboardStore()
        yield s
        s.close()


def test_two_learners_distinct_scores(store):
    service = LeaderboardService(store)
    epoch = "2026-W41"

    store.upsert_score("cohort", "c1", epoch, "L1", 100, "2026-10-12T10:00:00Z", "2026-10-12T10:00:00Z")
    store.upsert_score("cohort", "c1", epoch, "L2", 200, "2026-10-12T10:05:00Z", "2026-10-12T10:05:00Z")

    top_2 = service.get_top_k("cohort", "c1", epoch, k=2)
    assert len(top_2) == 2
    assert top_2[0].subject_id == "L2"
    assert top_2[0].rank == 1
    assert top_2[0].score == 200

    assert top_2[1].subject_id == "L1"
    assert top_2[1].rank == 2
    assert top_2[1].score == 100


def test_tie_broken_by_earliest_timestamp(store):
    """When scores are tied, the learner who reached the score first wins."""
    service = LeaderboardService(store)
    epoch = "2026-W41"

    # L1 reached 150 XP at 09:00:00
    store.upsert_score("cohort", "c1", epoch, "L1", 150, "2026-10-12T09:00:00Z", "2026-10-12T09:00:00Z")
    # L2 reached 150 XP later at 09:30:00
    store.upsert_score("cohort", "c1", epoch, "L2", 150, "2026-10-12T09:30:00Z", "2026-10-12T09:30:00Z")

    top_2 = service.get_top_k("cohort", "c1", epoch, k=2)
    assert top_2[0].subject_id == "L1"
    assert top_2[0].rank == 1
    assert top_2[1].subject_id == "L2"
    assert top_2[1].rank == 2

    # Verify individual rank lookups agree with top-k ordering
    r1 = service.get_learner_rank("cohort", "c1", epoch, "L1")
    r2 = service.get_learner_rank("cohort", "c1", epoch, "L2")
    assert r1.rank == 1
    assert r2.rank == 2


def test_tie_broken_by_subject_id_when_timestamps_identical(store):
    """When scores and timestamps are identical, subject_id breaks the tie lexicographically."""
    service = LeaderboardService(store)
    epoch = "2026-W41"

    timestamp = "2026-10-12T10:00:00Z"
    # L_beta vs L_alpha
    store.upsert_score("cohort", "c1", epoch, "L_beta", 150, timestamp, timestamp)
    store.upsert_score("cohort", "c1", epoch, "L_alpha", 150, timestamp, timestamp)

    top_2 = service.get_top_k("cohort", "c1", epoch, k=2)
    assert top_2[0].subject_id == "L_alpha"
    assert top_2[0].rank == 1
    assert top_2[1].subject_id == "L_beta"
    assert top_2[1].rank == 2

    r_alpha = service.get_learner_rank("cohort", "c1", epoch, "L_alpha")
    r_beta = service.get_learner_rank("cohort", "c1", epoch, "L_beta")
    assert r_alpha.rank == 1
    assert r_beta.rank == 2


def test_rank_validly_worsens_when_peers_improve(store):
    """Refuting 'rank never moves backward': rank can worsen when peers advance."""
    service = LeaderboardService(store)
    epoch = "2026-W41"

    # L1 starts at rank 1 with 100 XP
    store.upsert_score("cohort", "c1", epoch, "L1", 100, "2026-10-12T10:00:00Z", "2026-10-12T10:00:00Z")
    r1_before = service.get_learner_rank("cohort", "c1", epoch, "L1")
    assert r1_before.rank == 1
    assert r1_before.score == 100

    # L2 earns 200 XP
    store.upsert_score("cohort", "c1", epoch, "L2", 200, "2026-10-12T10:10:00Z", "2026-10-12T10:10:00Z")

    # L1's rank validly worsens to 2, while L1's score remains unchanged
    r1_after = service.get_learner_rank("cohort", "c1", epoch, "L1")
    assert r1_after.rank == 2
    assert r1_after.score == 100


def test_surrounding_rank_window(store):
    service = LeaderboardService(store)
    epoch = "2026-W41"

    # Insert 10 learners
    for i in range(1, 11):
        score = i * 10
        store.upsert_score(
            "cohort",
            "c1",
            epoch,
            f"user_{i:02d}",
            score,
            f"2026-10-12T10:{i:02d}:00Z",
            f"2026-10-12T10:{i:02d}:00Z",
        )

    # user_05 has score 50 (ranked 6th among 10 users: 100 down to 10)
    surrounding = service.get_surrounding("cohort", "c1", epoch, "user_05", window=2)
    assert len(surrounding) == 5
    ranks = [s.rank for s in surrounding]
    assert ranks == [4, 5, 6, 7, 8]
    assert surrounding[2].subject_id == "user_05"
