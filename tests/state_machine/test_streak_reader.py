"""Week 4 fix 2: the streak reader must agree with the aggregate and the pure fold.

The first three tests are copied unchanged from test_active_state_path.py.
The remaining tests pin order independence and duplicate-delivery idempotence.
"""

from itertools import permutations
import random

from src.state_machine.learner import Learner
from src.state_machine.projection import (
    LearnerStreakProjection,
    LearnerStreakSummary,
    fold_streak_and_freezes,
    rebuild_learner_state,
)


def _events(learner: Learner) -> list:
    return list(learner.collect_events())


def _complete(learner: Learner, cid: str, day: str) -> None:
    learner.complete_lesson(cid, "lesson-1", activity_date=day)


# ---------------------------------------------------------------------------
# Streak reader == aggregate == pure fold
# ---------------------------------------------------------------------------
def test_streak_projection_matches_aggregate_for_late_oct1():
    learner = Learner()
    _complete(learner, "c1", "2026-10-01")
    _complete(learner, "c2", "2026-10-02")
    _complete(learner, "c3", "2026-10-01")  # new completion on an earlier day
    events = _events(learner)

    replayed = rebuild_learner_state(events)
    projection = LearnerStreakProjection()
    for event in events:
        projection.apply(event)
    summary = projection.streak_summaries[learner.id]

    assert replayed.new_streak == 2
    assert learner.new_streak == 2
    assert summary.new_streak == 2
    assert summary.freezes_remaining == learner.freezes_remaining


def test_streak_projection_counts_freeze_acquired_after_latest_activity():
    learner = Learner()
    _complete(learner, "c1", "2026-10-01")
    _complete(learner, "c2", "2026-10-02")
    learner.acquire_freeze("f1", activity_date="2026-10-03")
    events = _events(learner)

    replayed = rebuild_learner_state(events)
    projection = LearnerStreakProjection()
    for event in events:
        projection.apply(event)
    summary = projection.streak_summaries[learner.id]

    assert learner.freezes_remaining == 1
    assert replayed.freezes_remaining == 1
    assert summary.freezes_remaining == 1
    assert summary.new_streak == replayed.new_streak == 2


def test_streak_projection_is_idempotent_when_events_are_delivered_twice():
    learner = Learner()
    _complete(learner, "c1", "2026-10-01")
    learner.acquire_freeze("f1", activity_date="2026-10-01")
    events = _events(learner)
    projection = LearnerStreakProjection()
    for event in events + events:
        projection.apply(event)
    summary = projection.streak_summaries[learner.id]
    assert (summary.new_streak, summary.freezes_remaining) == (1, 1)


# ---------------------------------------------------------------------------
# Order independence and duplicate delivery
# ---------------------------------------------------------------------------
def _late_oct1_with_freeze() -> Learner:
    learner = Learner()
    _complete(learner, "c1", "2026-10-01")
    _complete(learner, "c2", "2026-10-02")
    _complete(learner, "c3", "2026-10-01")  # revision 2 for 2026-10-01
    learner.acquire_freeze("f1", activity_date="2026-10-03")
    return learner


def test_streak_projection_is_identical_for_every_arrival_order():
    learner = _late_oct1_with_freeze()
    events = _events(learner)
    # 11 events -> 11! orders is too many; the reader only consumes these two
    # types, and every other event is a no-op for it, so permuting them is the
    # full set of distinguishable orders.
    streak_facts = [
        event for event in events
        if isinstance(event, (Learner.StreakUpdated, Learner.FreezeAcquired))
    ]
    assert len(streak_facts) == 4

    results = set()
    for order in permutations(streak_facts):
        projection = LearnerStreakProjection()
        for event in order:
            projection.apply(event)
        results.add(projection.streak_summaries.get(learner.id))

    assert len(results) == 1, results


def test_rebuild_learner_state_ignores_duplicated_events():
    learner = _late_oct1_with_freeze()
    events = _events(learner)

    once = rebuild_learner_state(events)
    twice = rebuild_learner_state(events + events)

    assert once.freezes_remaining == learner.freezes_remaining == 1
    assert twice.freezes_remaining == once.freezes_remaining


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------
def _project(events) -> LearnerStreakProjection:
    projection = LearnerStreakProjection()
    for event in events:
        projection.apply(event)
    return projection


def _three_views(learner: Learner, events: list) -> tuple:
    """(aggregate, pure fold, projection) as (new_streak, freezes_remaining)."""
    replayed = rebuild_learner_state(events)
    summary = _project(events).streak_summaries.get(
        learner.id, LearnerStreakSummary(0, 0)
    )
    return (
        (learner.new_streak, learner.freezes_remaining),
        (replayed.new_streak, replayed.freezes_remaining),
        (summary.new_streak, summary.freezes_remaining),
    )


def test_lower_revision_arriving_after_higher_is_ignored():
    learner = Learner()
    _complete(learner, "c1", "2026-10-01")
    _complete(learner, "c3", "2026-10-03")  # gap -> Oct3 rev 1, streak 1
    _complete(learner, "c2", "2026-10-02")  # fills gap -> Oct3 rev 2, streak 3
    events = _events(learner)
    oct3 = [
        e for e in events
        if isinstance(e, Learner.StreakUpdated) and e.activity_date == "2026-10-03"
    ]
    assert [(e.revision, e.new_streak) for e in oct3] == [(1, 1), (2, 3)]

    stale = oct3[0]
    reordered = [e for e in events if e is not stale] + [stale]
    summary = _project(reordered).streak_summaries[learner.id]

    assert learner.new_streak == 3
    assert (summary.new_streak, summary.freezes_remaining) == (3, 0)
    assert rebuild_learner_state(reordered).new_streak == 3


def test_two_learners_do_not_affect_each_other():
    alice, bob = Learner(), Learner()
    _complete(alice, "a1", "2026-10-01")
    _complete(alice, "a2", "2026-10-02")
    alice.acquire_freeze("shared-id", activity_date="2026-10-02")
    _complete(bob, "b1", "2026-10-05")
    bob.acquire_freeze("shared-id", activity_date="2026-10-05")
    bob.acquire_freeze("b-only", activity_date="2026-10-05")
    alice_events, bob_events = _events(alice), _events(bob)

    interleaved = [
        event
        for pair in zip(alice_events, bob_events)
        for event in pair
    ] + alice_events[len(bob_events):] + bob_events[len(alice_events):]
    together = _project(interleaved).streak_summaries

    assert together[alice.id] == _project(alice_events).streak_summaries[alice.id]
    assert together[bob.id] == _project(bob_events).streak_summaries[bob.id]
    assert (together[alice.id].new_streak, together[alice.id].freezes_remaining) == (2, 1)
    assert (together[bob.id].new_streak, together[bob.id].freezes_remaining) == (1, 2)


def test_freeze_acquired_before_any_activity():
    learner = Learner()
    learner.acquire_freeze("f0", activity_date="2026-09-30")
    only_freeze = _events(learner)
    assert len(set(_three_views(learner, only_freeze))) == 1
    assert _three_views(learner, only_freeze)[0] == (0, 1)

    _complete(learner, "c1", "2026-10-01")
    events = only_freeze + _events(learner)
    views = _three_views(learner, events)
    assert views[0] == (1, 1)
    assert len(set(views)) == 1, views


def test_gap_covered_by_freeze_keeps_streak():
    learner = Learner()
    _complete(learner, "c1", "2026-10-01")
    learner.acquire_freeze("f1", activity_date="2026-10-01")
    _complete(learner, "c3", "2026-10-03")  # Oct2 missing, covered by f1
    views = _three_views(learner, _events(learner))
    assert views[0] == (2, 0)
    assert len(set(views)) == 1, views


def test_gap_not_covered_resets_streak_to_one():
    learner = Learner()
    _complete(learner, "c1", "2026-10-01")
    _complete(learner, "c2", "2026-10-02")
    _complete(learner, "c4", "2026-10-04")  # Oct3 missing, no freeze
    views = _three_views(learner, _events(learner))
    assert views[0] == (1, 0)
    assert len(set(views)) == 1, views


def test_empty_history():
    assert fold_streak_and_freezes([], []) == (0, 0)
    replayed = rebuild_learner_state([])
    assert (replayed.new_streak, replayed.freezes_remaining) == (0, 0)
    assert _project([]).streak_summaries == {}

    learner = Learner()  # only a Created event
    views = _three_views(learner, _events(learner))
    assert views == ((0, 0), (0, 0), (0, 0))


# ---------------------------------------------------------------------------
# Cross-check: aggregate == pure fold == projection on random histories
# ---------------------------------------------------------------------------
SEED = 20261024
SEQUENCES = 300
DAYS = [f"2026-10-{day:02d}" for day in range(1, 11)]


def _random_ops(rng: random.Random) -> list[tuple[str, str]]:
    ops = []
    for _ in range(rng.randint(1, 10)):
        kind = "lesson" if rng.random() < 0.75 else "freeze"
        ops.append((kind, rng.choice(DAYS)))  # any day, so late / out of order
    return ops


def _run_ops(ops) -> tuple[Learner, list]:
    learner = Learner()
    for index, (kind, day) in enumerate(ops):
        if kind == "lesson":
            _complete(learner, f"c{index}", day)
        else:
            learner.acquire_freeze(f"f{index}", activity_date=day)
    return learner, _events(learner)


def _mismatch(ops):
    learner, events = _run_ops(ops)
    views = _three_views(learner, events)
    return None if len(set(views)) == 1 else views


def test_aggregate_fold_and_projection_agree_on_random_histories():
    rng = random.Random(SEED)
    failures = []
    for _ in range(SEQUENCES):
        ops = _random_ops(rng)
        views = _mismatch(ops)
        if views is not None:
            failures.append((ops, views))

    shortest = min(failures, key=lambda f: len(f[0]), default=None)
    assert not failures, (
        f"{len(failures)}/{SEQUENCES} sequences disagree "
        f"(aggregate, fold, projection). Shortest: {shortest}"
    )


def test_minimal_cross_check_failure_freeze_consumed_by_earlier_date_correction():
    """Shrunk from the random cross-check (seed 20261024).

    The late Oct1 lesson makes Oct2 a gap between Oct1 and Oct3, so the Oct3
    outcome is corrected (rev 2) to consume freeze f2. The ground truth from
    the full history is (new_streak=1, freezes_remaining=0).
    """
    ops = [
        ("lesson", "2026-10-05"),
        ("lesson", "2026-10-03"),
        ("freeze", "2026-10-02"),
        ("lesson", "2026-10-01"),
    ]
    learner, events = _run_ops(ops)
    aggregate, fold, projection = _three_views(learner, events)
    assert fold == projection == (1, 0)
    assert aggregate == fold, (aggregate, fold)


def test_aggregate_freezes_remaining_matches_its_stored_outcomes():
    """The aggregate's balance must equal its own acquisitions minus stored consumption."""
    rng = random.Random(SEED)
    failures = []
    for _ in range(SEQUENCES):
        ops = _random_ops(rng)
        learner, _ = _run_ops(ops)
        expected = len(learner.freeze_acquisitions) - sum(
            outcome[2] for outcome in learner.streak_outcomes.values()
        )
        if learner.freezes_remaining != expected:
            failures.append((ops, learner.freezes_remaining, expected))

    shortest = min(failures, key=lambda f: len(f[0]), default=None)
    assert not failures, (
        f"{len(failures)}/{SEQUENCES} sequences have a stale freezes_remaining "
        f"(ops, actual, expected). Shortest: {shortest}"
    )
