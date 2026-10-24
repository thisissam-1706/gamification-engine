"""Week 4 fix 3: the quest reader keeps assignment identity, status and deadline.

The first three tests are copied unchanged from test_active_state_path.py.
"""

from datetime import datetime, timedelta, timezone
from itertools import permutations
import random

import pytest

from src.state_machine.learner import Learner
from src.state_machine.projection import (
    LearnerQuestProjection,
    LearnerQuestSummary,
    rebuild_learner_state,
)

QUEST = "lesson_starter_v1"


def _events(learner: Learner) -> list:
    return list(learner.collect_events())


def _lesson_at(learner: Learner, cid: str, occurred_at: str) -> None:
    learner.complete_lesson(cid, "lesson-1", occurred_at=occurred_at, timezone="UTC")


# ---------------------------------------------------------------------------
# Quest reader keeps assignment identity, status and deadline
# ---------------------------------------------------------------------------
def _two_assignments() -> Learner:
    learner = Learner()
    learner.assign_quest(
        QUEST, assignment_id="a1", assigned_at="2026-10-01T00:00:00Z",
        deadline_at="2026-10-08T00:00:00Z",
    )
    for index in range(3):
        _lesson_at(learner, f"first-{index}", f"2026-10-0{index + 2}T10:00:00Z")
    learner.assign_quest(
        QUEST, assignment_id="a2", assigned_at="2026-10-05T00:00:00Z",
        deadline_at="2026-10-12T00:00:00Z",
    )
    _lesson_at(learner, "second-0", "2026-10-06T10:00:00Z")
    return learner


def test_quest_projection_keeps_repeated_assignments_separate():
    learner = _two_assignments()
    assert learner.quest_instances["a1"]["status"] == "completed"
    assert learner.quest_instances["a2"]["status"] == "assigned"

    projection = LearnerQuestProjection()
    for event in _events(learner):
        projection.apply(event)
    summary = projection.get_summary(learner.id)

    assert set(summary.assignments) == {"a1", "a2"}
    assert summary.assignments["a1"].status == "completed"
    assert summary.assignments["a2"].status == "assigned"
    assert summary.assignments["a1"].progress == 3
    assert summary.assignments["a2"].progress == 1
    assert summary.assignments["a2"].deadline_at == datetime(
        2026, 10, 12, tzinfo=timezone.utc
    )
    assert summary.quest_progress == {"a1": 3, "a2": 1}
    # The same quest id assigned twice is not collapsed.
    assert summary.assigned_quests == (QUEST, QUEST)
    assert summary.completed_quests == (QUEST,)


def test_quest_projection_marks_overdue_assignment_expired_without_new_lesson():
    learner = _two_assignments()
    projection = LearnerQuestProjection()
    for event in _events(learner):
        projection.apply(event)

    before_deadline = projection.get_summary(
        learner.id, as_of=datetime(2026, 10, 11, tzinfo=timezone.utc)
    )
    after_deadline = projection.get_summary(
        learner.id, as_of=datetime(2026, 10, 13, tzinfo=timezone.utc)
    )
    assert before_deadline.assignments["a2"].status == "assigned"
    assert after_deadline.assignments["a2"].status == "expired"
    # A completed assignment never becomes expired.
    assert after_deadline.assignments["a1"].status == "completed"


def test_quest_projection_applies_expiry_events():
    learner = Learner()
    learner.assign_quest(
        QUEST, assignment_id="a1", assigned_at="2026-10-01T00:00:00Z",
        deadline_at="2026-10-03T00:00:00Z",
    )
    _lesson_at(learner, "late", "2026-10-09T10:00:00Z")  # past deadline -> expiry event
    projection = LearnerQuestProjection()
    for event in _events(learner):
        projection.apply(event)
    summary = projection.get_summary(learner.id)
    assert learner.quest_instances["a1"]["status"] == "expired"
    assert summary.assignments["a1"].status == "expired"
    assert summary.completed_quests == ()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------
QUEST_EVENTS = (
    Learner.QuestAssigned,
    Learner.QuestProgressed,
    Learner.QuestCompleted,
    Learner.QuestExpired,
)


def _quest_events(learner: Learner) -> list:
    return [event for event in _events(learner) if isinstance(event, QUEST_EVENTS)]


def _project(events) -> LearnerQuestProjection:
    projection = LearnerQuestProjection()
    for event in events:
        projection.apply(event)
    return projection


def _completed_assignment() -> Learner:
    """a1: assigned, three lessons, completed."""
    learner = Learner()
    learner.assign_quest(
        QUEST, assignment_id="a1", assigned_at="2026-10-01T00:00:00Z",
        deadline_at="2026-10-08T00:00:00Z",
    )
    for index in range(3):
        _lesson_at(learner, f"c{index}", f"2026-10-0{index + 2}T10:00:00Z")
    return learner


def _expired_assignment() -> Learner:
    """a1: assigned, one lesson in time, then a late lesson that expires it."""
    learner = Learner()
    learner.assign_quest(
        QUEST, assignment_id="a1", assigned_at="2026-10-01T00:00:00Z",
        deadline_at="2026-10-03T00:00:00Z",
    )
    _lesson_at(learner, "on-time", "2026-10-02T10:00:00Z")
    _lesson_at(learner, "late", "2026-10-09T10:00:00Z")
    return learner


def test_as_of_equal_to_deadline_is_still_assigned():
    learner = _two_assignments()
    projection = _project(_events(learner))
    deadline = datetime(2026, 10, 12, tzinfo=timezone.utc)

    at_deadline = projection.get_summary(learner.id, as_of=deadline)
    just_after = projection.get_summary(
        learner.id, as_of=deadline + timedelta(microseconds=1)
    )
    assert at_deadline.assignments["a2"].status == "assigned"
    assert just_after.assignments["a2"].status == "expired"
    # as_of does not touch the stored record or the default view.
    assert projection.get_summary(learner.id).assignments["a2"].status == "assigned"


def test_progress_arriving_before_its_assignment():
    learner = _completed_assignment()
    assigned, *rest = _quest_events(learner)
    assert isinstance(assigned, Learner.QuestAssigned)
    progressed = [e for e in rest if isinstance(e, Learner.QuestProgressed)]

    projection = _project(progressed)
    early = projection.get_summary(learner.id)
    assert early.assignments["a1"].progress == 3
    assert early.assignments["a1"].status == "assigned"
    assert early.assignments["a1"].deadline_at is None
    # No deadline known yet, so as_of cannot expire it.
    far_future = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert projection.get_summary(learner.id, as_of=far_future).assignments[
        "a1"
    ].status == "assigned"

    projection.apply(assigned)
    late = projection.get_summary(learner.id).assignments["a1"]
    assert late.progress == 3
    assert late.assigned_at == datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert late.deadline_at == datetime(2026, 10, 8, tzinfo=timezone.utc)


def test_same_events_delivered_twice_give_same_summary():
    learner = _two_assignments()
    events = _events(learner)
    once = _project(events)
    twice = _project(events + events)
    for as_of in (None, datetime(2026, 10, 13, tzinfo=timezone.utc)):
        assert twice.get_summary(learner.id, as_of=as_of) == once.get_summary(
            learner.id, as_of=as_of
        )


@pytest.mark.parametrize(
    "build, expected_status, expected_progress",
    [(_completed_assignment, "completed", 3), (_expired_assignment, "expired", 1)],
)
def test_every_arrival_order_of_one_assignment_gives_same_summary(
    build, expected_status, expected_progress
):
    learner = build()
    events = _quest_events(learner)
    reference = _project(events).get_summary(learner.id)
    assert reference.assignments["a1"].status == learner.quest_instances["a1"]["status"]
    assert (reference.assignments["a1"].status, reference.quest_progress["a1"]) == (
        expected_status, expected_progress,
    )

    orders = list(permutations(events))
    assert len(orders) > 1
    for order in orders:
        assert _project(order).get_summary(learner.id) == reference


def test_two_learners_do_not_affect_each_other():
    first = _two_assignments()
    second = _expired_assignment()  # also uses assignment id "a1"
    first_events = _events(first)
    second_events = _events(second)
    interleaved = []
    for index in range(max(len(first_events), len(second_events))):
        interleaved.extend(first_events[index:index + 1])
        interleaved.extend(second_events[index:index + 1])

    shared = _project(interleaved)
    as_of = datetime(2026, 10, 13, tzinfo=timezone.utc)
    for learner, events in ((first, first_events), (second, second_events)):
        solo = _project(events)
        assert shared.get_summary(learner.id) == solo.get_summary(learner.id)
        assert shared.get_summary(learner.id, as_of=as_of) == solo.get_summary(
            learner.id, as_of=as_of
        )
    assert shared.get_summary(first.id).assignments["a1"].status == "completed"
    assert shared.get_summary(second.id).assignments["a1"].status == "expired"


def test_learner_with_no_quests_returns_empty_summary():
    empty = LearnerQuestSummary(
        assigned_quests=(), completed_quests=(), quest_progress={}, assignments={}
    )
    learner = Learner()
    _lesson_at(learner, "c0", "2026-10-01T10:00:00Z")
    projection = _project(_events(learner))
    assert projection.get_summary(learner.id) == empty
    assert projection.get_summary(
        learner.id, as_of=datetime(2026, 10, 13, tzinfo=timezone.utc)
    ) == empty
    assert LearnerQuestProjection().get_summary(learner.id) == empty


# ---------------------------------------------------------------------------
# Cross-check against the aggregate
# ---------------------------------------------------------------------------
SEED = 20261024
SEQUENCES = 300
BASE = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _random_ops(rng: random.Random) -> list:
    """1-3 assignments (rules v1 has one quest id, so repeats share it) and 0-8
    lessons, interleaved. Lessons land before, inside and after deadlines,
    sometimes exactly on one, in no particular time order."""
    assigns = []
    for index in range(rng.randint(1, 3)):
        assigned_at = BASE + timedelta(hours=rng.randint(0, 6 * 24))
        deadline_at = (
            None  # aggregate default: assigned_at + 7 days
            if rng.random() < 0.25
            else assigned_at + timedelta(hours=rng.randint(1, 4 * 24))
        )
        assigns.append(("assign", f"a{index}", assigned_at, deadline_at))
    windows = [
        (assigned, deadline or assigned + timedelta(days=7))
        for _, _, assigned, deadline in assigns
    ]
    lessons = []
    for index in range(rng.randint(0, 8)):
        roll = rng.random()
        assigned_at, deadline_at = rng.choice(windows)
        if roll < 0.1:
            occurred_at = deadline_at
        elif roll < 0.55:  # inside a window, so completions happen too
            occurred_at = assigned_at + (deadline_at - assigned_at) * rng.random()
        else:
            occurred_at = BASE + timedelta(hours=rng.randint(-24, 14 * 24))
        lessons.append(("lesson", f"c{index}", occurred_at))
    ops = assigns + lessons
    rng.shuffle(ops)
    return ops


def _run_ops(ops: list) -> Learner:
    learner = Learner()
    for op in ops:
        if op[0] == "assign":
            _, assignment_id, assigned_at, deadline_at = op
            learner.assign_quest(
                QUEST, assignment_id=assignment_id,
                assigned_at=assigned_at.isoformat(),
                deadline_at=deadline_at.isoformat() if deadline_at else None,
            )
        else:
            _, completion_id, occurred_at = op
            _lesson_at(learner, completion_id, occurred_at.isoformat())
    return learner


def _mismatches(learner: Learner) -> dict:
    summary = _project(_events(learner)).get_summary(learner.id)
    if set(summary.assignments) != set(learner.quest_instances):
        return {"assignment_ids": (set(summary.assignments), set(learner.quest_instances))}
    found = {}
    for assignment_id, instance in learner.quest_instances.items():
        record = summary.assignments[assignment_id]
        projected = (record.status, record.progress, record.completed_at)
        expected = (instance["status"], instance["progress"], instance["completed_at"])
        if projected != expected:
            found[assignment_id] = (projected, expected)
    return found


def test_projection_matches_aggregate_on_random_histories():
    rng = random.Random(SEED)
    failures = []
    statuses = set()
    for _ in range(SEQUENCES):
        ops = _random_ops(rng)
        learner = _run_ops(ops)
        statuses.update(i["status"] for i in learner.quest_instances.values())
        found = _mismatches(learner)
        if found:
            failures.append((ops, found))
    # The generator must reach every status, or the check proves little.
    assert statuses == {"assigned", "completed", "expired"}
    shortest = min(failures, key=lambda f: len(f[0])) if failures else None
    assert not failures, (
        f"{len(failures)}/{SEQUENCES} sequences disagree. Shortest: {shortest}"
    )


def test_rebuild_completed_quests_is_distinct_but_projection_is_per_assignment():
    # Known divergence, out of scope for this fix: rebuild_learner_state keeps a
    # distinct set of quest ids, the projection keeps one entry per assignment.
    learner = Learner()
    learner.assign_quest(QUEST, assignment_id="a1", assigned_at="2026-10-01T00:00:00Z")
    for index in range(3):
        _lesson_at(learner, f"first-{index}", f"2026-10-0{index + 2}T10:00:00Z")
    learner.assign_quest(QUEST, assignment_id="a2", assigned_at="2026-10-05T00:00:00Z")
    for index in range(3):
        _lesson_at(learner, f"second-{index}", f"2026-10-0{index + 5}T12:00:00Z")
    events = _events(learner)
    assert {i["status"] for i in learner.quest_instances.values()} == {"completed"}

    assert rebuild_learner_state(events).completed_quests == (QUEST,)
    assert _project(events).get_summary(learner.id).completed_quests == (QUEST, QUEST)
