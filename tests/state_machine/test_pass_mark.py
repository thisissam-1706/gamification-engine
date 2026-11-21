"""Pass mark: a lesson with more than half of its answers wrong earns no XP and counts for nothing.

The attempt is stored as a single 0 XP award with withheld_reason "below_pass_mark". It does not
count toward the streak or quests, does not use up a decay step, and a resend of the same
completion cannot earn XP later. Exactly half wrong passes; a completion without a score passes.
"""

from datetime import datetime, timezone

import pytest
from jsonschema import ValidationError

from src.ingestion.validation import validate_lesson_completed_payload
from src.state_machine.learner import Learner
from src.state_machine.pass_mark import PASS_MARK_REASON, below_pass_mark
from src.state_machine.projection import rebuild_learner_state
from src.state_machine.rules import _REGISTRY, load_decay_rulesets


@pytest.fixture
def decay(monkeypatch):
    for version, ruleset in load_decay_rulesets().items():
        monkeypatch.setitem(_REGISTRY, version, ruleset)


def _at(minute):
    return datetime(2026, 10, 16, 9, minute, tzinfo=timezone.utc)


def _lesson(learner, cid, minute, correct=None, total=None, lesson="A", **kw):
    return learner.complete_lesson(
        cid, lesson, occurred_at=_at(minute), timezone="UTC",
        questions_correct=correct, questions_total=total, **kw,
    )


@pytest.mark.parametrize("correct, total, fails", [
    (0, 5, True), (1, 5, True), (2, 5, True), (3, 5, False), (5, 5, False),
    (1, 4, True), (2, 4, False),  # exactly half wrong passes
    (None, None, False),          # no score: passes
])
def test_more_than_half_wrong_is_below_the_pass_mark(correct, total, fails):
    assert below_pass_mark(correct, total) is fails


def test_failed_attempt_earns_nothing_and_counts_for_nothing(decay):
    learner = Learner()
    assert _lesson(learner, "c1", 1, 2, 5, rule_version="v3") is True
    assert learner.xp_total == 0
    assert learner.new_streak == 0 and not learner.activity_dates
    assert "c1" not in learner.completed_lesson_ids
    assert learner.eligible_lesson_counts == {}
    events = learner.collect_events()
    assert [type(e).__name__ for e in events if type(e).__name__ != "Created"] == ["XPAwarded"]
    award = events[-1]
    assert (award.xp_amount, award.withheld_reason) == (0, PASS_MARK_REASON)
    assert award.occurred_at == _at(1).isoformat()


def test_a_failed_attempt_does_not_use_a_decay_step(decay):
    learner = Learner()
    _lesson(learner, "c1", 1, 1, 5, rule_version="v3")   # fail: 0
    _lesson(learner, "c2", 2, 4, 5, rule_version="v3")   # first pass: full 100
    _lesson(learner, "c3", 3, 0, 5, rule_version="v3")   # fail: 0, no step used
    _lesson(learner, "c4", 4, 5, 5, rule_version="v3")   # first repeat: 50
    assert learner.xp_total == 150
    assert learner.eligible_lesson_counts == {"A": 2}


def test_a_failed_attempt_does_not_progress_a_quest():
    learner = Learner()
    learner.assign_quest("lesson_starter_v1", assignment_id="q1", assigned_at=_at(0))
    _lesson(learner, "c1", 1, 0, 5)
    progress = [e for e in learner.collect_events() if type(e).__name__ == "QuestProgressed"]
    assert progress == []


def test_resending_a_failed_completion_cannot_earn_xp_later():
    learner = Learner()
    _lesson(learner, "c1", 1, 1, 5)
    assert _lesson(learner, "c1", 2, 5, 5) is False
    assert learner.xp_total == 0


def test_replay_reads_the_stored_zero(decay):
    learner = Learner()
    _lesson(learner, "c1", 1, 2, 5, rule_version="v3")
    _lesson(learner, "c2", 2, 5, 5, rule_version="v3")
    events = learner.collect_events()
    replayed = None
    for event in events:
        replayed = event.mutate(replayed)
    assert replayed.xp_total == learner.xp_total == 100
    assert rebuild_learner_state(events).xp_total == 100


def test_payload_score_must_be_consistent():
    base = {"completion_id": "c", "lesson_id": "A", "timezone": "UTC"}
    validate_lesson_completed_payload({**base, "questions_correct": 3, "questions_total": 5})
    validate_lesson_completed_payload(base)
    for bad in (
        {"questions_correct": 6, "questions_total": 5},   # more correct than asked
        {"questions_correct": 3},                          # one without the other
        {"questions_total": 5},
        {"questions_correct": -1, "questions_total": 5},
        {"questions_correct": 0, "questions_total": 0},
    ):
        with pytest.raises(ValidationError):
            validate_lesson_completed_payload({**base, **bad})
