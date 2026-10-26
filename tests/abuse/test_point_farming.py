"""AT-6: Point Farming / Decay Cap (M6)

Given E = 100, K = 20, learner completes lesson l_492 for the 3rd time (N = 2), when XP is calculated,
then XP_awarded = max(0, 0.5(100) - 20(1)) = 30 and if this pattern pushes a cohort's decayed-XP share
above 15% of weekly total, the policy is flagged.

Decay is ruleset v2 (opt-in); the flag is the flag-only cohort guardrail in src/abuse/guardrail.py.
"""

from datetime import timedelta

import pytest

from src.abuse.guardrail import cohort_gaming_report
from src.abuse.service import AbuseDetectionService
from src.abuse.store import AbuseSignalStore
from src.state_machine.rules import _REGISTRY, load_decay_rulesets
from tests.abuse.harness import EPOCH, Harness


@pytest.fixture
def harness(tmp_path, monkeypatch):
    for version, ruleset in load_decay_rulesets().items():
        monkeypatch.setitem(_REGISTRY, version, ruleset)
    h = Harness(str(tmp_path / "events.db"), rule_version="v2")
    yield h
    h.close()


def _facts(h):
    service = AbuseDetectionService(h.application, h.evidence, AbuseSignalStore(h.db))
    return service.completion_facts()[0]


def test_point_farming(harness):
    farmer = harness.new_learner()
    for n in range(3):
        assert harness.complete(farmer, EPOCH + timedelta(hours=n), lesson_id="l_492").status == "accepted"
    awarded = [(f.repeat_index, f.xp_amount) for f in sorted(_facts(harness), key=lambda f: f.occurred_at)]
    assert awarded == [(0, 100), (1, 50), (2, 30)]  # the 3rd completion (N = 2) earns 30
    assert harness.learner(farmer).xp_total == 180

    report = cohort_gaming_report(_facts(harness), {farmer: "cohort-a"}, "2026-W42", "v1")
    [cohort] = report
    assert (cohort.total_xp, cohort.repeat_xp) == (180, 80)
    assert cohort.share > 0.15 and cohort.breached is True  # the policy is flagged


def test_a_cohort_below_the_threshold_is_not_flagged(harness):
    farmer, others = harness.new_learner(), []
    for n in range(3):
        harness.complete(farmer, EPOCH + timedelta(hours=n), lesson_id="l_492")
    for i in range(10):
        learner = harness.new_learner()
        others.append(learner)
        harness.complete(learner, EPOCH + timedelta(minutes=5 * i), lesson_id=f"fresh-{i}")
    cohort_of = {farmer: "cohort-a", **{l: "cohort-a" for l in others}}
    [cohort] = cohort_gaming_report(_facts(harness), cohort_of, "2026-W42", "v1")
    assert (cohort.total_xp, cohort.repeat_xp) == (1180, 80)
    assert cohort.breached is False


def test_under_v1_repeats_are_not_decayed_but_are_still_visible_to_the_guardrail(tmp_path):
    h = Harness(str(tmp_path / "v1.db"))
    try:
        learner = h.new_learner()
        for n in range(3):
            h.complete(learner, EPOCH + timedelta(hours=n), lesson_id="l_492")
        assert h.learner(learner).xp_total == 300
        [cohort] = cohort_gaming_report(_facts(h), {learner: "c"}, "2026-W42", "v1")
        assert (cohort.total_xp, cohort.repeat_xp, cohort.breached) == (300, 200, True)
    finally:
        h.close()
