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
