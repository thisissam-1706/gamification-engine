"""The leaderboard poller (src/leaderboard/poller.py): the caller runs it; nothing sleeps or threads.

``poll_once`` reads notification-log entries after the store's committed offset and applies each with
``projection.apply(n, offset=n.id)``; it returns how many entries it newly processed.
``run_until_idle`` repeats until a poll processes nothing.

Written before the module exists, so every test first fails with ImportError. The event shapes copy
tests/leaderboard/test_leaderboard_e2e.py (13 notifications for the two-learner scenario).
"""

from uuid import UUID

import pytest

from src.leaderboard.projection import LeaderboardProjection
from src.leaderboard.service import LeaderboardService
from src.leaderboard.store import SQLiteLeaderboardStore
from src.state_machine.learner import Learner, LearnerApplication
from tests.leaderboard.test_leaderboard_e2e import read_all_notifications

EPOCH = "2026-W42"


def _poll_once(app, projection, **kwargs):
    # Imported here so a missing module fails each test with a clear ImportError.
    from src.leaderboard.poller import poll_once

    return poll_once(app, projection, **kwargs)


def _run_until_idle(app, projection, **kwargs):
    from src.leaderboard.poller import run_until_idle

    return run_until_idle(app, projection, **kwargs)


class World:
    """An event store plus a leaderboard store and projection over files in ``tmp_path``."""

    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.events_db = str(tmp_path / "events.db")
        self.board_db = str(tmp_path / "board.db")
        self.app = LearnerApplication(
            env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": self.events_db}
        )
        self.open_board()

    def open_board(self):
        self.store = SQLiteLeaderboardStore(self.board_db)
        self.projection = LeaderboardProjection(self.store)
        self.service = LeaderboardService(self.store)

    def restart_board(self):
        """Kills the projection process: a new store and projection on the same database."""
        self.store.close()
        self.open_board()

    def close(self):
        self.store.close()
        self.app.close()

    def add(self, cohort, assigned_at, lessons):
        learner = Learner()
        learner.assign_cohort(cohort, EPOCH, assigned_at=assigned_at)
        for completion_id, lesson_id, occurred_at in lessons:
            learner.complete_lesson(completion_id, lesson_id, occurred_at=occurred_at, timezone="UTC")
        self.app.save(learner)
        return str(learner.id)

    def add_standard(self):
        """The two-learner scenario of the e2e tests: 13 notifications, scores 200 and 100."""
        l1 = self.add(
            "cohort_alpha",
            "2026-10-12T08:00:00Z",
            [("c1", "lesson_1", "2026-10-12T09:00:00Z"), ("c2", "lesson_2", "2026-10-12T10:00:00Z")],
        )
        l2 = self.add("cohort_alpha", "2026-10-12T08:30:00Z", [("c3", "lesson_3", "2026-10-12T09:30:00Z")])
        return l1, l2

    def notifications(self):
        return read_all_notifications(self.app, start=1)

    def rank(self, scope_type, scope_id, learner_id):
        return self.service.get_learner_rank(scope_type, scope_id, EPOCH, learner_id)


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    yield w
    w.close()


def _board(store, learners):
    """Everything a reader can see: top-K and rank per learner, global and per cohort."""
    scopes = [("global", "global"), ("cohort", "cohort_alpha"), ("cohort", "cohort_beta")]
    return {
        (scope, scope_id): (
            store.get_top_k(scope, scope_id, EPOCH, k=100),
            {learner: store.get_rank(scope, scope_id, EPOCH, learner) for learner in learners},
        )
        for scope, scope_id in scopes
    }


# --- new events appear ----------------------------------------------------------------------


def test_new_events_appear_in_the_leaderboard_after_poll_once(world):
    l1, l2 = world.add_standard()
    assert world.rank("global", "global", l1) is None  # nothing projected yet

    applied = _poll_once(world.app, world.projection)

    assert applied == 13
    assert world.store.get_committed_offset("global") == 13
    assert (world.rank("global", "global", l1).score, world.rank("global", "global", l1).rank) == (200, 1)
    assert (world.rank("global", "global", l2).score, world.rank("global", "global", l2).rank) == (100, 2)
    assert world.rank("cohort", "cohort_alpha", l1).rank == 1

    l3 = world.add("cohort_alpha", "2026-10-12T08:45:00Z", [("c4", "lesson_4", "2026-10-12T11:00:00Z")])
    new_entries = len(world.notifications()) - 13
    assert _poll_once(world.app, world.projection) == new_entries  # only the new entries
    assert world.rank("global", "global", l3).score == 100


def test_a_second_call_with_nothing_new_returns_zero_and_changes_nothing(world):
    l1, l2 = world.add_standard()
    _poll_once(world.app, world.projection)
    before = _board(world.store, [l1, l2])

    assert _poll_once(world.app, world.projection) == 0

    assert world.store.get_committed_offset("global") == 13
    assert _board(world.store, [l1, l2]) == before


def test_batch_size_limits_one_poll_and_run_until_idle_drains_the_log(world):
    l1, l2 = world.add_standard()

    assert _poll_once(world.app, world.projection, batch_size=5) == 5
    assert world.store.get_committed_offset("global") == 5

    assert _run_until_idle(world.app, world.projection, batch_size=4) == 8  # the remaining 8, in polls of 4
    assert world.store.get_committed_offset("global") == 13
    assert _poll_once(world.app, world.projection) == 0
    assert world.rank("global", "global", l1).score == 200


# --- restart ---------------------------------------------------------------------------------


def test_after_a_restart_it_resumes_from_the_committed_offset_and_applies_nothing_twice(world):
    l1, l2 = world.add_standard()
    assert _poll_once(world.app, world.projection, batch_size=6) == 6
    assert world.store.get_committed_offset("global") == 6

    world.restart_board()  # new projection and store on the same database
    assert world.store.get_committed_offset("global") == 6

    assert _poll_once(world.app, world.projection) == 7  # entries 7..13 only
    assert world.store.get_committed_offset("global") == 13
    assert world.rank("global", "global", l1).score == 200  # not 400
    assert world.rank("global", "global", l2).score == 100
    assert len(world.store.get_applied_events(epoch=EPOCH)) == 3  # one per XP award

    world.restart_board()
    assert _poll_once(world.app, world.projection) == 0


# --- equals a full rebuild -------------------------------------------------------------------


def test_scores_and_ranks_equal_a_full_rebuild_from_the_log(world, tmp_path):
    l1, l2 = world.add_standard()
    l3 = world.add(
        "cohort_beta",
        "2026-10-12T09:00:00Z",
        [("c5", "lesson_5", "2026-10-12T09:15:00Z"), ("c6", "lesson_6", "2026-10-12T10:00:00Z")],
    )
    _run_until_idle(world.app, world.projection, batch_size=3)

    rebuilt_store = SQLiteLeaderboardStore(str(tmp_path / "rebuilt.db"))
    try:
        LeaderboardProjection(rebuilt_store).rebuild(world.notifications())

        learners = [l1, l2, l3]
        assert _board(world.store, learners) == _board(rebuilt_store, learners)
        assert world.store.get_committed_offset("global") == rebuilt_store.get_committed_offset("global")
    finally:
        rebuilt_store.close()


# --- a failure retries the failed event ------------------------------------------------------


def test_a_failure_while_applying_one_event_keeps_earlier_events_and_retries_the_failed_one(
    world, monkeypatch
):
    l1, l2 = world.add_standard()
    entries = world.notifications()
    target = next(n.id for n in entries if n.topic.endswith("LessonCompleted"))  # first lesson of l1
    assert target > 1

    original = world.store.record_processed_offset
    state = {"failed": False}

    def flaky(offset, *args, **kwargs):
        if offset == target and not state["failed"]:
            state["failed"] = True
            raise RuntimeError("simulated failure while applying")
        return original(offset, *args, **kwargs)

    monkeypatch.setattr(world.store, "record_processed_offset", flaky)

    with pytest.raises(RuntimeError, match="simulated failure"):
        _poll_once(world.app, world.projection)

    assert world.store.get_committed_offset("global") == target - 1  # earlier events stay applied
    assert all(world.store.is_offset_processed(i) for i in range(1, target))
    assert not world.store.is_offset_processed(target)  # the failed one left no trace
    assert world.store.get_member_cohort(EPOCH, l1) == "cohort_alpha"  # an earlier event took effect

    assert _poll_once(world.app, world.projection) == 13 - (target - 1)  # retried from the failed entry
    assert world.store.get_committed_offset("global") == 13
    assert world.rank("global", "global", l1).score == 200  # the failed event applied exactly once
    assert world.rank("global", "global", l2).score == 100
    assert _poll_once(world.app, world.projection) == 0


# --- duplicate and late events behave as in the e2e tests -------------------------------------


def _duplicate_and_late_scenario(world):
    shared = "comp_shared_xyz"  # two learners, one completion id: independent awards (e2e scenario 4)
    a = world.add("cohort_alpha", "2026-10-12T08:00:00Z", [(shared, "lesson_1", "2026-10-12T09:00:00Z")])
    b = world.add("cohort_alpha", "2026-10-12T08:30:00Z", [(shared, "lesson_2", "2026-10-12T10:00:00Z")])
    # A late event: it happened before a's first lesson but reaches the log afterwards.
    learner = world.app.repository.get(UUID(a))
    learner.complete_lesson("late-1", "lesson_3", occurred_at="2026-10-12T08:45:00Z", timezone="UTC")
    world.app.save(learner)
    # A repeated completion id on the same learner is refused by the aggregate: no new entry.
    learner = world.app.repository.get(UUID(a))
    assert learner.complete_lesson(shared, "lesson_1", occurred_at="2026-10-12T11:00:00Z", timezone="UTC") is False
    world.app.save(learner)
    return a, b


def test_duplicate_and_late_events_give_the_same_result_as_the_e2e_apply_loop(world, tmp_path):
    a, b = _duplicate_and_late_scenario(world)
    entries = world.notifications()

    direct_store = SQLiteLeaderboardStore(str(tmp_path / "direct.db"))
    try:
        direct = LeaderboardProjection(direct_store)
        statuses = [direct.apply(n, offset=n.id) for n in entries]  # the loop the e2e tests use

        assert _poll_once(world.app, world.projection) == len(entries)

        assert _board(world.store, [a, b]) == _board(direct_store, [a, b])
        assert world.store.get_committed_offset("global") == direct_store.get_committed_offset("global")
        assert world.store.get_applied_events(epoch=EPOCH) == direct_store.get_applied_events(epoch=EPOCH)
        assert statuses.count("duplicate") == 0  # neither learner is rejected as a duplicate
        assert world.rank("global", "global", a).score == 200  # 100 + the late 100
        assert world.rank("global", "global", b).score == 100
        # The late event arrived last but happened earlier: a first reached 200 at its own last lesson.
        assert world.rank("global", "global", a).first_reached_at == "2026-10-12T09:00:00Z"
    finally:
        direct_store.close()


def test_replaying_the_whole_log_after_polling_is_all_duplicates_and_changes_nothing(world):
    a, b = _duplicate_and_late_scenario(world)
    _poll_once(world.app, world.projection)
    before = _board(world.store, [a, b])

    statuses = [world.projection.apply(n, offset=n.id) for n in world.notifications()]  # e2e scenario 3

    assert set(statuses) == {"duplicate"}
    assert _board(world.store, [a, b]) == before
    assert _poll_once(world.app, world.projection) == 0
