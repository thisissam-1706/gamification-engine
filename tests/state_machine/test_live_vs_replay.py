"""Regression tests for the reviewer's P0 findings and live-vs-replay invariants."""

import itertools
import random

import pytest

from src.state_machine.learner import Learner
from src.state_machine.projection import (
    SQLiteLearnerProjection,
    rebuild_learner_state,
)

QUEST = "lesson_starter_v1"


def _events(learner: Learner) -> list:
    return list(learner.collect_events())


def _complete(learner: Learner, cid: str, day: str) -> None:
    learner.complete_lesson(cid, "lesson-1", activity_date=day)


def _lesson_at(learner: Learner, cid: str, occurred_at: str) -> None:
    learner.complete_lesson(
        cid, "lesson-1", occurred_at=occurred_at, timezone="UTC"
    )


# --------------------------------------------------------------------------
# P0 #1: rebuilding one learner must not delete another learner's projection
# --------------------------------------------------------------------------
def test_rebuilding_b_keeps_a_projection():
    a, b = Learner(), Learner()
    _complete(a, "a-1", "2026-10-01")
    _complete(b, "b-1", "2026-10-01")
    projection = SQLiteLearnerProjection(":memory:")
    projection.rebuild("A", _events(a))
    projection.rebuild("B", _events(b))
    assert projection.state("A") is not None
    assert projection.state("B") is not None


# --------------------------------------------------------------------------
# P0 #2: late event for an earlier day must not overwrite the live streak
# --------------------------------------------------------------------------
def test_late_oct1_live_streak_matches_replay():
    learner = Learner()
    _complete(learner, "c1", "2026-10-01")
    _complete(learner, "c2", "2026-10-02")
    _complete(learner, "c3", "2026-10-01")  # late, second completion on Oct 1
    replayed = rebuild_learner_state(_events(learner))
    assert replayed.new_streak == 2
    assert learner.new_streak == replayed.new_streak


# --------------------------------------------------------------------------
# P0 #3a: a freeze acquired after the latest activity must survive replay
# --------------------------------------------------------------------------
def test_freeze_after_latest_activity_survives_rebuild():
    learner = Learner()
    _complete(learner, "c1", "2026-10-01")
    learner.acquire_freeze("freeze-1", activity_date="2026-10-02")
    replayed = rebuild_learner_state(_events(learner))
    assert learner.freezes_remaining == 1
    assert replayed.freezes_remaining == learner.freezes_remaining


# --------------------------------------------------------------------------
# P0 #3b: a reversed seven-day history still earns the week badge
# --------------------------------------------------------------------------
def test_reversed_seven_day_history_awards_badge_live_and_on_replay():
    days = [f"2026-10-0{i}" for i in range(1, 8)]
    learner = Learner()
    for i, day in enumerate(reversed(days)):
        _complete(learner, f"r{i}", day)
    replayed = rebuild_learner_state(_events(learner))
    assert replayed.new_streak == 7
    assert "week_warrior_v1" in learner.badges_issued
    assert "week_warrior_v1" in (replayed.badges_issued or {})


# --------------------------------------------------------------------------
# P0 #4: quest progress is per assignment, not per quest type
# --------------------------------------------------------------------------
def test_two_assignments_of_same_quest_progress_independently():
    learner = Learner()
    learner.assign_quest(QUEST, assignment_id="A1", assigned_at="2026-10-01T00:00:00Z")
    learner.assign_quest(QUEST, assignment_id="A2", assigned_at="2026-10-01T00:00:00Z")
    for i in range(2):
        _lesson_at(learner, f"q{i}", "2026-10-01T10:00:00Z")
    statuses = {k: v["status"] for k, v in learner.quest_instances.items()}
    progress = {k: v["progress"] for k, v in learner.quest_instances.items()}
    assert statuses == {"A1": "assigned", "A2": "assigned"}
    assert progress == {"A1": 2, "A2": 2}


def test_activity_before_assignment_does_not_count():
    learner = Learner()
    learner.assign_quest(QUEST, assignment_id="B1", assigned_at="2026-10-05T00:00:00Z")
    for i in range(3):
        _lesson_at(learner, f"p{i}", "2026-10-01T10:00:00Z")  # occurred before assignment
    assert learner.quest_instances["B1"]["status"] == "assigned"
    assert learner.quest_instances["B1"]["progress"] == 0


def test_assigned_quest_live_state_equals_replay_with_shuffled_lessons():
    learner = Learner()
    learner.assign_quest(
        QUEST,
        assignment_id="shuffled-assignment",
        assigned_at="2026-10-01T00:00:00Z",
        deadline_at="2026-10-10T00:00:00Z",
    )
    for i, day in enumerate(
        ["2026-10-03", "2026-10-01", "2026-10-02"]
    ):
        _lesson_at(learner, f"quest-{i}", f"{day}T10:00:00Z")

    assert _snapshot_live(learner)["completed_quests"] == ["lesson_starter_v1"]
    assert _snapshot_live(learner)["completed_quests"] == _snapshot_replay(
        learner
    )["completed_quests"]


# --------------------------------------------------------------------------
# Invariant: arrival order must not matter, and live state must equal replay
# --------------------------------------------------------------------------
def _snapshot_live(learner: Learner) -> dict:
    return {
        "xp": learner.xp_total,
        "streak": learner.new_streak,
        "freezes": learner.freezes_remaining,
        "badges": sorted(learner.badges_issued),
        "completed_quests": sorted(
            str(v["quest_id"])
            for v in learner.quest_instances.values()
            if v["status"] == "completed"
        ),
    }


def _snapshot_replay(learner: Learner) -> dict:
    state = rebuild_learner_state(_events(learner))
    return {
        "xp": state.xp_total,
        "streak": state.new_streak,
        "freezes": state.freezes_remaining,
        "badges": sorted(state.badges_issued or {}),
        "completed_quests": sorted(state.completed_quests),
    }


def _run(days: list[str], freeze_on: str | None) -> Learner:
    learner = Learner()
    for i, day in enumerate(days):
        _complete(learner, f"c-{day}-{i}", day)
    if freeze_on:
        learner.acquire_freeze("freeze-1", activity_date=freeze_on)
    return learner


HISTORIES = [
    # (days, freeze date): seven in a row, a gap, and a freeze-covered gap
    ([f"2026-10-0{i}" for i in range(1, 8)], None),
    (["2026-10-01", "2026-10-02", "2026-10-04"], None),
    (["2026-10-01", "2026-10-02", "2026-10-04"], "2026-10-02"),
    (["2026-10-01", "2026-10-02", "2026-10-03"], "2026-10-05"),
]


@pytest.mark.parametrize("days,freeze_on", HISTORIES)
def test_live_state_equals_replay_and_ignores_arrival_order(days, freeze_on):
    # Distinct (date, ordinal) pairs so every permutation has unique completion ids.
    rng = random.Random(1234)
    orders = list(itertools.islice(itertools.permutations(days), 200))
    rng.shuffle(orders)
    reference = _snapshot_live(_run(sorted(days), freeze_on))
    for order in orders[:25]:
        learner = _run(list(order), freeze_on)
        assert _snapshot_live(learner) == reference, f"order-dependent live state for {order}"
        assert _snapshot_live(learner) == _snapshot_replay(learner), f"live != replay for {order}"