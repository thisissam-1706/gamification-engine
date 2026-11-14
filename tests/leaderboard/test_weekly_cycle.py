"""run_weekly_cycle: finalize last week's cohorts, build this week's, bring the leaderboard up to date.

Written before src/leaderboard/weekly.py exists, so each test first fails with ImportError.
Two weeks are run end to end: week 1 is 2026-W42 (built from lessons in W41), lessons happen, then week 2
is 2026-W43 (built from the lessons of W42). Learner ids are fixed (uuid5), so cohorts are reproducible.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid5

import pytest

from src.leaderboard.cohort_builder import _tier_and_module
from src.leaderboard.projection import LeaderboardProjection
from src.leaderboard.store import SQLiteLeaderboardStore
from src.state_machine.learner import Learner, LearnerApplication
from tests.leaderboard.test_leaderboard_e2e import read_all_notifications

WEEK1, WEEK2 = "2026-W42", "2026-W43"
REF1 = datetime(2026, 10, 12, 8, tzinfo=timezone.utc)  # Monday of W42
REF2 = datetime(2026, 10, 19, 8, tzinfo=timezone.utc)  # Monday of W43
NAMESPACE = UUID("c7e1a9d2-3b64-4f08-8a15-6d0e2b9f4c71")
MIN_SIZE, MAX_SIZE = 2, 10


def _cycle(app, projection, learner_ids, epoch, reference_time, **kwargs):
    # Imported here so a missing module fails each test with a clear ImportError.
    from src.leaderboard.weekly import run_weekly_cycle

    return run_weekly_cycle(
        app, projection, learner_ids, epoch,
        reference_time=reference_time, min_size=MIN_SIZE, max_size=MAX_SIZE, **kwargs,
    )


class World:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.env = {
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(tmp_path / "events.db"),
        }
        self.app = LearnerApplication(env=self.env)
        self.board_path = str(tmp_path / "board.db")
        self.store = SQLiteLeaderboardStore(self.board_path)
        self.projection = LeaderboardProjection(self.store)

    def close(self):
        self.store.close()
        self.app.close()

    def learner(self, label, lessons_in, start, count, module):
        """A learner with a fixed id and ``count`` lessons of ``module`` from ``start``."""
        learner = Learner._create(Learner.Created, id=uuid5(NAMESPACE, label))
        self.app.save(learner)
        self.lessons(str(learner.id), start, count, module, tag=f"{label}-{lessons_in}")
        return str(learner.id)

    def lessons(self, learner_id, start, count, module, tag):
        learner = self.app.repository.get(UUID(learner_id))
        for k in range(count):
            when = start.replace(hour=start.hour + (k % 12), day=start.day + k // 12)
            learner.complete_lesson(
                f"{tag}-{k}", f"{module}/l{k}", occurred_at=when.isoformat(), timezone="UTC"
            )
        self.app.save(learner)

    def events(self, learner_id):
        return list(self.app.events.get(UUID(learner_id)))

    def cohort(self, learner_id, epoch):
        return self.app.repository.get(UUID(learner_id)).cohort_memberships[epoch]["cohort_id"]

    def statuses(self):
        cursor = self.store.connection.execute(
            "SELECT cohort_id, status FROM leaderboard_cohort_definitions ORDER BY cohort_id"
        )
        return dict(cursor.fetchall())


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    yield w
    w.close()


def _two_week_population(world):
    """Week 1 learners: 4 casual/math and 4 intensive/py, from the lessons of W41 (Oct 5 onwards)."""
    start = datetime(2026, 10, 5, 9, tzinfo=timezone.utc)
    casual = [world.learner(f"casual-{n}", "w41", start, 1, "math") for n in range(4)]
    intensive = [world.learner(f"intensive-{n}", "w41", start, 10, "py") for n in range(4)]
    return casual, intensive


def _play_week_one(world, casual, intensive):
    """Lessons in W42 that change next week's tiers: casual 0-1 do 4 (regular), intensive 0-1 do 1 (casual)."""
    start = datetime(2026, 10, 13, 9, tzinfo=timezone.utc)
    for learner_id in casual[:2]:
        world.lessons(learner_id, start, 4, "math", tag="w42")
    for learner_id in intensive[:2]:
        world.lessons(learner_id, start, 1, "py", tag="w42")
    for learner_id in intensive[2:]:
        world.lessons(learner_id, start, 10, "py", tag="w42")


def _board(store, learners, epochs, cohorts):
    scopes = [("global", "global")] + [("cohort", c) for c in sorted(cohorts)]
    return {
        (epoch, scope, scope_id): (
            store.get_top_k(scope, scope_id, epoch, k=100),
            {l: store.get_rank(scope, scope_id, epoch, l) for l in learners},
        )
        for epoch in epochs
        for scope, scope_id in scopes
    }


# --- two consecutive weeks end to end ------------------------------------------------------


def test_two_weeks_end_to_end(world):
    casual, intensive = _two_week_population(world)
    everyone = casual + intensive

    first = _cycle(world.app, world.projection, everyone, WEEK1, REF1)

    week1_cohorts = {world.cohort(l, WEEK1) for l in everyone}
    assert len(week1_cohorts) == 2  # casual/math and intensive/py
    assert set(first.build.assignments) == set(everyone)
    assert first.finalized == ()  # nothing existed before week 1
    assert set(world.statuses()) == week1_cohorts
    assert "finalized" not in world.statuses().values()  # and this week's cohorts are not touched

    _play_week_one(world, casual, intensive)
    # Two learners already have a lesson in the new week when the cycle runs on Monday morning.
    world.lessons(casual[0], datetime(2026, 10, 19, 10, tzinfo=timezone.utc), 2, "math", tag="w43")
    world.lessons(intensive[3], datetime(2026, 10, 19, 10, tzinfo=timezone.utc), 1, "py", tag="w43")

    second = _cycle(world.app, world.projection, everyone, WEEK2, REF2)

    # Previous week's cohorts are finalized (and only those); this week's are left as they are.
    assert set(second.finalized) == week1_cohorts
    assert second.not_finalized == ()
    assert {world.statuses()[c] for c in week1_cohorts} == {"finalized"}
    week2_cohorts = {world.cohort(l, WEEK2) for l in everyone}
    assert week2_cohorts.isdisjoint(week1_cohorts)
    assert "finalized" not in {world.statuses().get(c) for c in week2_cohorts}
    assert all(world.statuses().get(c) != "sealed" for c in week2_cohorts)  # never sealed by the cycle

    # New cohorts come from the new prior week (W42 lessons): casual 0-1 are now regular, intensive 0-1 casual.
    expected_tier = {l: "regular" for l in casual[:2]}
    expected_tier.update({l: "casual" for l in casual[2:] + intensive[:2]})  # casual 2-3 had no W42 lesson
    expected_tier.update({l: "intensive" for l in intensive[2:]})
    for learner_id, tier in expected_tier.items():
        assert _tier_and_module(world.cohort(learner_id, WEEK2), WEEK2)[0] == tier, learner_id

    # The leaderboard is up to date: this week's cohort ranks and scores exist.
    assert world.store.get_committed_offset("global") == len(read_all_notifications(world.app))
    top = world.store.get_rank("cohort", world.cohort(casual[0], WEEK2), WEEK2, casual[0])
    assert (top.score, top.rank) == (200, 1)  # its two W43 lessons
    assert world.store.get_rank("global", "global", WEEK2, intensive[3]).score == 100
    assert world.store.get_rank("cohort", world.cohort(casual[2], WEEK2), WEEK2, casual[2]) is not None
    assert world.store.get_member_cohort(WEEK2, casual[0]) == world.cohort(casual[0], WEEK2)


# --- running it twice ----------------------------------------------------------------------


def test_running_a_week_twice_changes_nothing(world):
    casual, intensive = _two_week_population(world)
    everyone = casual + intensive
    _cycle(world.app, world.projection, everyone, WEEK1, REF1)
    _play_week_one(world, casual, intensive)
    _cycle(world.app, world.projection, everyone, WEEK2, REF2)

    events = {l: world.events(l) for l in everyone}
    statuses = world.statuses()
    cohorts = {c for l in everyone for c in (world.cohort(l, WEEK1), world.cohort(l, WEEK2))}
    board = _board(world.store, everyone, [WEEK1, WEEK2], cohorts)
    offset = world.store.get_committed_offset("global")

    again = _cycle(world.app, world.projection, everyone, WEEK2, REF2)

    assert again.finalized == ()
    assert again.build.assignments == {l: world.cohort(l, WEEK2) for l in everyone}
    assert {l: world.events(l) for l in everyone} == events
    assert world.statuses() == statuses
    assert _board(world.store, everyone, [WEEK1, WEEK2], cohorts) == board
    assert world.store.get_committed_offset("global") == offset


# --- inactive learners ---------------------------------------------------------------------


def test_a_learner_inactive_for_30_days_is_not_assigned(world):
    casual, intensive = _two_week_population(world)
    gone = world.learner("gone", "sep", datetime(2026, 9, 7, 9, tzinfo=timezone.utc), 3, "math")  # >30 days before REF1
    everyone = casual + intensive + [gone]

    result = _cycle(world.app, world.projection, everyone, WEEK1, REF1)

    assert gone in result.build.inactive
    assert gone not in result.build.assignments
    assert WEEK1 not in world.app.repository.get(UUID(gone)).cohort_memberships
    assert not [e for e in world.events(gone) if type(e).__name__ == "CohortAssigned"]
    assert world.store.get_member_cohort(WEEK1, gone) is None


# --- rebuild from the event log ------------------------------------------------------------


def test_a_rebuild_from_the_event_log_gives_the_same_cohorts_and_ranks(world, tmp_path):
    casual, intensive = _two_week_population(world)
    everyone = casual + intensive
    _cycle(world.app, world.projection, everyone, WEEK1, REF1)
    _play_week_one(world, casual, intensive)
    world.lessons(casual[0], datetime(2026, 10, 19, 10, tzinfo=timezone.utc), 2, "math", tag="w43")
    _cycle(world.app, world.projection, everyone, WEEK2, REF2)
    cohorts = {c for l in everyone for c in (world.cohort(l, WEEK1), world.cohort(l, WEEK2))}
    live_board = _board(world.store, everyone, [WEEK1, WEEK2], cohorts)
    live_cohorts = {(l, e): world.cohort(l, e) for l in everyone for e in (WEEK1, WEEK2)}

    restarted = LearnerApplication(env=world.env)  # a fresh application on the same log
    rebuilt_store = SQLiteLeaderboardStore(str(tmp_path / "rebuilt.db"))
    try:
        rebuilt = LeaderboardProjection(rebuilt_store)
        rebuilt.rebuild(read_all_notifications(restarted))

        replayed = {
            (l, e): restarted.repository.get(UUID(l)).cohort_memberships[e]["cohort_id"]
            for l in everyone for e in (WEEK1, WEEK2)
        }
        assert replayed == live_cohorts
        assert _board(rebuilt_store, everyone, [WEEK1, WEEK2], cohorts) == live_board
    finally:
        rebuilt_store.close()
        restarted.close()


# --- a cohort that cannot be finalized is reported, not forced -----------------------------


def test_a_cohort_that_cannot_be_finalized_is_reported_not_forced(world, monkeypatch):
    casual, intensive = _two_week_population(world)
    everyone = casual + intensive
    _cycle(world.app, world.projection, everyone, WEEK1, REF1)
    week1 = sorted({world.cohort(l, WEEK1) for l in everyone})
    refused = week1[0]

    original = world.projection.transition_cohort

    def strict(cohort_id, target_status, epoch=""):
        if cohort_id == refused:
            raise ValueError("Invalid cohort lifecycle transition (simulated)")
        return original(cohort_id, target_status, epoch=epoch)

    monkeypatch.setattr(world.projection, "transition_cohort", strict)

    result = _cycle(world.app, world.projection, everyone, WEEK2, REF2)

    assert result.not_finalized == ((refused, "forming"),)
    assert result.finalized == (week1[1],)
    assert world.statuses()[refused] != "finalized"  # left as it was
    assert world.statuses()[week1[1]] == "finalized"
    assert set(result.build.assignments) == set(everyone)  # the rest of the cycle still ran


# --- a read model rebuilt from the log is healed by running the cycle again ----------------


def test_a_rebuilt_read_model_is_finalized_again_by_running_the_cycle(world, tmp_path):
    casual, intensive = _two_week_population(world)
    everyone = casual + intensive
    _cycle(world.app, world.projection, everyone, WEEK1, REF1)
    _play_week_one(world, casual, intensive)
    _cycle(world.app, world.projection, everyone, WEEK2, REF2)
    week1 = {world.cohort(l, WEEK1) for l in everyone}
    week2_before = {l: world.cohort(l, WEEK2) for l in everyone}

    restarted = LearnerApplication(env=world.env)
    rebuilt_store = SQLiteLeaderboardStore(str(tmp_path / "healed.db"))
    try:
        rebuilt = LeaderboardProjection(rebuilt_store)
        rebuilt.rebuild(read_all_notifications(restarted))
        assert {s for c, s in rebuilt_store.connection.execute(
            "SELECT cohort_id, status FROM leaderboard_cohort_definitions") if c in week1} != {"finalized"}

        result = _cycle(restarted, rebuilt, everyone, WEEK2, REF2)

        assert set(result.finalized) == week1
        assert {l: restarted.repository.get(UUID(l)).cohort_memberships[WEEK2]["cohort_id"] for l in everyone} == week2_before
        statuses = dict(rebuilt_store.connection.execute(
            "SELECT cohort_id, status FROM leaderboard_cohort_definitions"))
        assert {statuses[c] for c in week1} == {"finalized"}
    finally:
        rebuilt_store.close()
        restarted.close()
