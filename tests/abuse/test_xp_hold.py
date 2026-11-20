"""XP hold after a scripted-farming flag: accepted completion, 0 XP, reason stored, replay-safe."""

from dataclasses import dataclass
from datetime import datetime, timezone

from src.abuse.xp_hold import FARMING_HOLD_REASON, farming_hold_reason
from src.state_machine.learner import Learner


@dataclass(frozen=True)
class Sig:
    signal_type: str
    window_end: str


def test_hold_only_inside_24h_after_a_farming_window():
    flag = [Sig("scripted_farming", "2026-10-16T18:20:00Z")]
    assert farming_hold_reason(flag, "2026-10-16T18:30:00Z") == FARMING_HOLD_REASON
    assert farming_hold_reason(flag, "2026-10-17T18:20:00Z") == FARMING_HOLD_REASON  # edge: exactly 24 h
    assert farming_hold_reason(flag, "2026-10-17T18:20:01Z") is None
    assert farming_hold_reason(flag, "2026-10-16T18:00:00Z") is None  # before the episode
    assert farming_hold_reason([Sig("impossible_timing", "2026-10-16T18:20:00Z")], "2026-10-16T18:30:00Z") is None


def _complete(learner, n, **kw):
    learner.complete_lesson(
        f"c{n}", "fractions_basics_1",
        occurred_at=datetime(2026, 10, 16, 18, n, tzinfo=timezone.utc), timezone="UTC", **kw,
    )


def test_withheld_completion_is_accepted_with_zero_xp_and_reason():
    learner = Learner()
    _complete(learner, 1)
    _complete(learner, 2, xp_withheld_reason=FARMING_HOLD_REASON)
    assert learner.xp_total == 100
    assert "c2" in learner.completed_lesson_ids  # still a completion: streak and quests unaffected
    awards = [e for e in learner.collect_events() if isinstance(e, Learner.XPAwarded)]
    assert [(a.xp_amount, a.withheld_reason) for a in awards] == [(100, None), (0, FARMING_HOLD_REASON)]


def test_hold_does_not_apply_to_remediation_lessons():
    learner = Learner()
    learner.complete_lesson(
        "r1", "practice", occurred_at=datetime(2026, 10, 16, 18, tzinfo=timezone.utc), timezone="UTC",
        completion_kind="remediation", xp_withheld_reason=FARMING_HOLD_REASON,
    )
    assert learner.xp_total == 100


def test_replay_reads_the_stored_zero():
    learner = Learner()
    _complete(learner, 1)
    _complete(learner, 2, xp_withheld_reason=FARMING_HOLD_REASON)
    events = learner.collect_events()
    replayed = None
    for event in events:
        replayed = event.mutate(replayed)
    assert replayed.xp_total == 100
