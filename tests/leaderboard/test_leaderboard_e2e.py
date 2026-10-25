"""End-to-end integration tests driving real Learner aggregate events through notification log into LeaderboardProjection.

Verifies:
1. Normal run: real events driven through notification log project scores, ranks, and offsets.
2. Kill and restart mid-stream: projection restarts cleanly and resumes from committed offset.
3. Replay log twice: duplicate delivery produces exact duplicate status counts without score duplication.
4. Two learners with same completion_id: independent scoring and deterministic tie-breaking.
5. Cohort reassignment and sealed-cohort refusal: dynamic reassignment and refusal of sealed arrivals.
"""

from collections.abc import Sequence
import pytest

from eventsourcing.application import Notification
from src.leaderboard.cohorts import CohortSealedError
from src.leaderboard.models import CohortDefinition
from src.leaderboard.projection import LeaderboardProjection
from src.leaderboard.service import LeaderboardService
from src.leaderboard.store import SQLiteLeaderboardStore
from src.state_machine.learner import Learner, LearnerApplication


def read_all_notifications(app: LearnerApplication, start: int = 1) -> list[Notification]:
    """Reads all notifications from application notification log starting at given position."""
    notifications = []
    pos = start
    while True:
        batch = list(
            app.notification_log.select(
                start=pos, limit=app.notification_log.section_size
            )
        )
        if not batch:
            break
        notifications.extend(batch)
        pos = batch[-1].id + 1
    return notifications


def test_scenario_1_normal_run(tmp_path):
    """Scenario 1: Normal run driving real aggregate events through notification log."""
    events_db = str(tmp_path / "events_s1.db")
    leaderboard_db = str(tmp_path / "leaderboard_s1.db")
    epoch = "2026-W42"

    app = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": events_db}
    )
    store = SQLiteLeaderboardStore(leaderboard_db)
    proj = LeaderboardProjection(store)
    service = LeaderboardService(store)

    # Learner 1: 2 completed lessons (200 XP total)
    l1 = Learner()
    l1.assign_cohort("cohort_alpha", epoch, assigned_at="2026-10-12T08:00:00Z")
    l1.complete_lesson("c1", "lesson_1", occurred_at="2026-10-12T09:00:00Z", timezone="UTC")
    l1.complete_lesson("c2", "lesson_2", occurred_at="2026-10-12T10:00:00Z", timezone="UTC")
    app.save(l1)

    # Learner 2: 1 completed lesson (100 XP total)
    l2 = Learner()
    l2.assign_cohort("cohort_alpha", epoch, assigned_at="2026-10-12T08:30:00Z")
    l2.complete_lesson("c3", "lesson_3", occurred_at="2026-10-12T09:30:00Z", timezone="UTC")
    app.save(l2)

    notifications = read_all_notifications(app, start=1)
    assert len(notifications) == 13

    counts = {"applied": 0, "duplicate": 0, "pending_time": 0, "ignored": 0}
    for n in notifications:
        status = proj.apply(n, offset=n.id)
        counts[status] += 1

    # Assert exact apply status counts
    assert counts == {"applied": 8, "duplicate": 0, "pending_time": 0, "ignored": 5}

    # Assert exact committed offset
    assert store.get_committed_offset("global") == 13
    assert proj._watermark == 13

    # Assert exact global scores, ranks, and first_reached_at
    r1_g = service.get_learner_rank("global", "global", epoch, str(l1.id))
    assert r1_g is not None
    assert r1_g.score == 200
    assert r1_g.rank == 1
    assert r1_g.first_reached_at == "2026-10-12T10:00:00Z"

    r2_g = service.get_learner_rank("global", "global", epoch, str(l2.id))
    assert r2_g is not None
    assert r2_g.score == 100
    assert r2_g.rank == 2
    assert r2_g.first_reached_at == "2026-10-12T09:30:00Z"

    # Assert exact cohort scores, ranks, and first_reached_at
    r1_c = service.get_learner_rank("cohort", "cohort_alpha", epoch, str(l1.id))
    assert r1_c is not None
    assert r1_c.score == 200
    assert r1_c.rank == 1
    assert r1_c.first_reached_at == "2026-10-12T10:00:00Z"

    r2_c = service.get_learner_rank("cohort", "cohort_alpha", epoch, str(l2.id))
    assert r2_c is not None
    assert r2_c.score == 100
    assert r2_c.rank == 2
    assert r2_c.first_reached_at == "2026-10-12T09:30:00Z"

    store.close()
    app.close()


def test_scenario_2_kill_and_restart_mid_stream(tmp_path):
    """Scenario 2: Kill and restart the projection mid-stream, then resume from committed offset."""
    events_db = str(tmp_path / "events_s2.db")
    leaderboard_db = str(tmp_path / "leaderboard_s2.db")
    epoch = "2026-W42"

    app = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": events_db}
    )

    # 2 learners generating 13 notifications total
    l1 = Learner()
    l1.assign_cohort("cohort_alpha", epoch, assigned_at="2026-10-12T08:00:00Z")
    l1.complete_lesson("c1", "lesson_1", occurred_at="2026-10-12T09:00:00Z", timezone="UTC")
    l1.complete_lesson("c2", "lesson_2", occurred_at="2026-10-12T10:00:00Z", timezone="UTC")
    app.save(l1)

    l2 = Learner()
    l2.assign_cohort("cohort_alpha", epoch, assigned_at="2026-10-12T08:30:00Z")
    l2.complete_lesson("c3", "lesson_3", occurred_at="2026-10-12T09:30:00Z", timezone="UTC")
    app.save(l2)

    all_notifications = read_all_notifications(app, start=1)
    assert len(all_notifications) == 13

    # Phase 1: Projection instance 1 processes first 6 notifications mid-stream
    store1 = SQLiteLeaderboardStore(leaderboard_db)
    proj1 = LeaderboardProjection(store1)

    counts_phase1 = {"applied": 0, "duplicate": 0, "pending_time": 0, "ignored": 0}
    for n in all_notifications[:6]:
        status = proj1.apply(n, offset=n.id)
        counts_phase1[status] += 1

    assert counts_phase1 == {"applied": 4, "duplicate": 0, "pending_time": 0, "ignored": 2}
    mid_committed = store1.get_committed_offset("global")
    assert mid_committed == 6
    assert proj1._watermark == 6

    # Kill projection 1 process
    store1.close()
    del proj1

    # Phase 2: Restart projection with new instance on same SQLite file and rehydrate
    store2 = SQLiteLeaderboardStore(leaderboard_db)
    proj2 = LeaderboardProjection(store2)
    proj2.rehydrate(epoch)
    service2 = LeaderboardService(store2)

    # Checkpoint and watermark survive restart
    assert store2.get_committed_offset("global") == 6
    assert proj2._watermark == 6

    # Resume reading from committed offset + 1
    resume_offset = store2.get_committed_offset("global") + 1
    assert resume_offset == 7
    remaining_notifications = read_all_notifications(app, start=resume_offset)
    assert len(remaining_notifications) == 7

    counts_phase2 = {"applied": 0, "duplicate": 0, "pending_time": 0, "ignored": 0}
    for n in remaining_notifications:
        status = proj2.apply(n, offset=n.id)
        counts_phase2[status] += 1

    assert counts_phase2 == {"applied": 4, "duplicate": 0, "pending_time": 0, "ignored": 3}

    # Total counts across entire stream
    total_counts = {k: counts_phase1[k] + counts_phase2[k] for k in counts_phase1}
    assert total_counts == {"applied": 8, "duplicate": 0, "pending_time": 0, "ignored": 5}

    # Final committed offset matches stream end
    assert store2.get_committed_offset("global") == 13
    assert proj2._watermark == 13

    # Scores and ranks match Scenario 1 exactly
    r1_g = service2.get_learner_rank("global", "global", epoch, str(l1.id))
    assert r1_g is not None
    assert r1_g.score == 200
    assert r1_g.rank == 1
    assert r1_g.first_reached_at == "2026-10-12T10:00:00Z"

    r2_g = service2.get_learner_rank("global", "global", epoch, str(l2.id))
    assert r2_g is not None
    assert r2_g.score == 100
    assert r2_g.rank == 2
    assert r2_g.first_reached_at == "2026-10-12T09:30:00Z"

    store2.close()
    app.close()


def test_scenario_3_replay_whole_log_twice(tmp_path):
    """Scenario 3: Replay the whole log twice, asserting exact duplicate counts and unchanged scores."""
    events_db = str(tmp_path / "events_s3.db")
    leaderboard_db = str(tmp_path / "leaderboard_s3.db")
    epoch = "2026-W42"

    app = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": events_db}
    )
    store = SQLiteLeaderboardStore(leaderboard_db)
    proj = LeaderboardProjection(store)
    service = LeaderboardService(store)

    l1 = Learner()
    l1.assign_cohort("cohort_alpha", epoch, assigned_at="2026-10-12T08:00:00Z")
    l1.complete_lesson("c1", "lesson_1", occurred_at="2026-10-12T09:00:00Z", timezone="UTC")
    l1.complete_lesson("c2", "lesson_2", occurred_at="2026-10-12T10:00:00Z", timezone="UTC")
    app.save(l1)

    notifications = read_all_notifications(app, start=1)
    assert len(notifications) == 8

    # Pass 1: Initial delivery of all 8 notifications
    counts_pass1 = {"applied": 0, "duplicate": 0, "pending_time": 0, "ignored": 0}
    for n in notifications:
        status = proj.apply(n, offset=n.id)
        counts_pass1[status] += 1

    assert counts_pass1 == {"applied": 5, "duplicate": 0, "pending_time": 0, "ignored": 3}
    assert store.get_committed_offset("global") == 8
    assert proj._watermark == 8

    # Pass 2: Replay the entire log of 8 notifications
    counts_pass2 = {"applied": 0, "duplicate": 0, "pending_time": 0, "ignored": 0}
    for n in notifications:
        status = proj.apply(n, offset=n.id)
        counts_pass2[status] += 1

    assert counts_pass2 == {"applied": 0, "duplicate": 8, "pending_time": 0, "ignored": 0}
    assert store.get_committed_offset("global") == 8
    assert proj._watermark == 8

    # Total counts across Pass 1 + Pass 2
    total_counts = {k: counts_pass1[k] + counts_pass2[k] for k in counts_pass1}
    assert total_counts == {"applied": 5, "duplicate": 8, "pending_time": 0, "ignored": 3}

    # Score and rank remain completely idempotent (score must be 200, not 400)
    r1 = service.get_learner_rank("global", "global", epoch, str(l1.id))
    assert r1 is not None
    assert r1.score == 200
    assert r1.rank == 1
    assert r1.first_reached_at == "2026-10-12T10:00:00Z"

    store.close()
    app.close()


def test_scenario_4_two_learners_same_completion_id(tmp_path):
    """Scenario 4: Two learners with the same completion_id earn XP independently without collision."""
    events_db = str(tmp_path / "events_s4.db")
    leaderboard_db = str(tmp_path / "leaderboard_s4.db")
    epoch = "2026-W42"

    app = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": events_db}
    )
    store = SQLiteLeaderboardStore(leaderboard_db)
    proj = LeaderboardProjection(store)
    service = LeaderboardService(store)

    shared_completion_id = "comp_shared_xyz"

    l1 = Learner()
    l1.assign_cohort("cohort_alpha", epoch, assigned_at="2026-10-12T08:00:00Z")
    l1.complete_lesson(
        shared_completion_id, "lesson_1", occurred_at="2026-10-12T09:00:00Z", timezone="UTC"
    )
    app.save(l1)

    l2 = Learner()
    l2.assign_cohort("cohort_alpha", epoch, assigned_at="2026-10-12T08:30:00Z")
    l2.complete_lesson(
        shared_completion_id, "lesson_2", occurred_at="2026-10-12T10:00:00Z", timezone="UTC"
    )
    app.save(l2)

    notifications = read_all_notifications(app, start=1)
    assert len(notifications) == 10

    counts = {"applied": 0, "duplicate": 0, "pending_time": 0, "ignored": 0}
    for n in notifications:
        status = proj.apply(n, offset=n.id)
        counts[status] += 1

    # Assert exact apply status counts: neither learner is rejected as a duplicate
    assert counts == {"applied": 6, "duplicate": 0, "pending_time": 0, "ignored": 4}
    assert store.get_committed_offset("global") == 10
    assert proj._watermark == 10

    # Assert both learners received 100 XP and deterministic tie-breaking ranked L1 first
    r1 = service.get_learner_rank("global", "global", epoch, str(l1.id))
    assert r1 is not None
    assert r1.score == 100
    assert r1.rank == 1
    assert r1.first_reached_at == "2026-10-12T09:00:00Z"

    r2 = service.get_learner_rank("global", "global", epoch, str(l2.id))
    assert r2 is not None
    assert r2.score == 100
    assert r2.rank == 2
    assert r2.first_reached_at == "2026-10-12T10:00:00Z"

    # Both present in cohort leaderboard
    r1_c = service.get_learner_rank("cohort", "cohort_alpha", epoch, str(l1.id))
    assert r1_c.score == 100
    assert r1_c.rank == 1
    assert r1_c.first_reached_at == "2026-10-12T09:00:00Z"

    r2_c = service.get_learner_rank("cohort", "cohort_alpha", epoch, str(l2.id))
    assert r2_c.score == 100
    assert r2_c.rank == 2
    assert r2_c.first_reached_at == "2026-10-12T10:00:00Z"

    store.close()
    app.close()


def test_scenario_5_cohort_reassignment_and_sealed_refusal(tmp_path):
    """Scenario 5: Cohort reassignment moves score atomically, and sealed arrival is refused."""
    events_db = str(tmp_path / "events_s5.db")
    leaderboard_db = str(tmp_path / "leaderboard_s5.db")
    epoch = "2026-W42"

    app = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": events_db}
    )
    store = SQLiteLeaderboardStore(leaderboard_db)
    proj = LeaderboardProjection(store)
    service = LeaderboardService(store)

    # Pre-register sealed cohort in the projection store
    c_sealed = CohortDefinition(
        cohort_id="cohort_sealed", strategy="activity_volume", epoch=epoch, status="sealed"
    )
    proj.register_cohort(c_sealed)

    # Learner 1 joins alpha, earns 100 XP, then reassigns to beta
    l1 = Learner()
    l1.assign_cohort("cohort_alpha", epoch, assigned_at="2026-10-12T08:00:00Z")
    l1.complete_lesson("c1", "lesson_1", occurred_at="2026-10-12T09:00:00Z", timezone="UTC")
    l1.reassign_cohort("cohort_beta", epoch, assigned_at="2026-10-12T10:00:00Z")
    app.save(l1)

    # Learner 2 attempts to join sealed cohort
    l2 = Learner()
    l2.assign_cohort("cohort_sealed", epoch, assigned_at="2026-10-12T11:00:00Z")
    app.save(l2)

    notifications = read_all_notifications(app, start=1)
    assert len(notifications) == 8

    counts = {"applied": 0, "duplicate": 0, "pending_time": 0, "ignored": 0}
    refusals = 0

    for n in notifications:
        try:
            status = proj.apply(n, offset=n.id)
            counts[status] += 1
        except CohortSealedError:
            refusals += 1

    # Assert exact apply status counts and refusal
    assert counts == {"applied": 4, "duplicate": 0, "pending_time": 0, "ignored": 3}
    assert refusals == 1

    # Offset 8 was rolled back due to refusal; committed offset remains at 7
    assert store.get_committed_offset("global") == 7
    assert proj._watermark == 7

    # L1 score successfully moved to cohort_beta
    r1_beta = service.get_learner_rank("cohort", "cohort_beta", epoch, str(l1.id))
    assert r1_beta is not None
    assert r1_beta.score == 100
    assert r1_beta.rank == 1
    assert r1_beta.first_reached_at == "2026-10-12T09:00:00Z"

    # L1 purged from cohort_alpha
    assert store.get_entry("cohort", "cohort_alpha", epoch, str(l1.id)) is None

    # L2 completely absent from sealed cohort
    assert store.get_entry("cohort", "cohort_sealed", epoch, str(l2.id)) is None
    assert store.get_cohort_member_count("cohort_sealed", epoch) == 0

    # L1 retained in global leaderboard
    r1_g = service.get_learner_rank("global", "global", epoch, str(l1.id))
    assert r1_g is not None
    assert r1_g.score == 100
    assert r1_g.rank == 1
    assert r1_g.first_reached_at == "2026-10-12T09:00:00Z"

    store.close()
    app.close()
