"""AT-18: Policy Comparison (M7)

Given a synthetic population assigned by deterministic hash to treatment (streak
reminders) or control (no reminders), when outcomes are computed, then the primary
metric is the percent of all assigned eligible learners who pass their first
eligible checkpoint within 14 days, with missing outcomes counted as not mastered,
and fatigue metrics are reported for both groups.

Runs are built in memory with simulate() and analyze(); no files are written.
"""

from datetime import datetime, timedelta, timezone

import pytest

from simulator.generator import create_persona_population, generate_common_random_numbers
from simulator.pilot import CAP, DATA_CUTOFF_ISO, EXP_ID, simulate
from simulator.pilot_report import analyze
from src.experiments.assignment import assign

ARMS = {"treatment": "variant_a", "control": "variant_b"}
CUTOFF = datetime.strptime(DATA_CUTOFF_ISO, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
WINDOW = timedelta(days=14)
SEED = 7


def _ts(value):
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _build(setting="positive", n=1500, seed=SEED):
    population = create_persona_population(n, seed)
    draws = generate_common_random_numbers(population, seed)
    run = simulate(population, draws, setting, seed)
    return run, analyze(run)


@pytest.fixture(scope="module")
def positive():
    return _build("positive", 1500)


def _arm_facts(run, variant):
    """Independent view of one arm: assigned_at of mature eligible learners, and immature count."""
    assigned = {a["learner_id"]: _ts(a["assigned_at"]) for a in run.assignments
                if a["variant"] == variant}
    eligible = {e["learner_id"] for e in run.eligibility
                if e["eligible"] and e["learner_id"] in assigned}
    mature = {l for l in eligible if assigned[l] + WINDOW <= CUTOFF}
    return assigned, eligible, mature


def _independent_mastered(run, assigned, mature):
    """Count mastered learners among `mature` without calling mastery_report."""
    attempts_by_learner = {}
    for a in run.attempts:
        l = a["learner_id"]
        if l not in mature:
            continue
        t = _ts(a["occurred_at"])
        if assigned[l] <= t <= assigned[l] + WINDOW and t <= CUTOFF:
            attempts_by_learner.setdefault(l, []).append((t, a["attempt_id"], a))
    completions_by_attempt = {}
    for c in run.completions:
        completions_by_attempt.setdefault(c["attempt_id"], []).append(c)

    mastered = 0
    for l, rows in attempts_by_learner.items():
        t, _, attempt = min(rows, key=lambda r: (r[0], r[1]))
        for c in completions_by_attempt.get(attempt["attempt_id"], []):
            ct = _ts(c["occurred_at"])
            if (
                c["passed"] is True
                and c["assessment_id"] == attempt["assessment_id"]
                and c["assessment_version"] == attempt["assessment_version"]
                and t <= ct <= assigned[l] + WINDOW
                and ct <= CUTOFF
            ):
                mastered += 1
                break
    return mastered


def test_assignment_is_the_deterministic_hash(positive):
    run, _ = positive
    assert run.assignments
    for row in run.assignments:
        assert row["variant"] == assign(row["learner_id"], EXP_ID)[0]
        assert row["propensity"] == 0.5
    variants = {row["variant"] for row in run.assignments}
    assert variants == {"variant_a", "variant_b"}
    again, _ = _build("positive", 1500)
    assert again.assignments == run.assignments


@pytest.mark.parametrize("arm", ["treatment", "control"])
def test_primary_metric_matches_independent_recomputation(positive, arm):
    run, report = positive
    assigned, eligible, mature = _arm_facts(run, ARMS[arm])
    assert mature
    expected = _independent_mastered(run, assigned, mature) / len(mature)
    assert 0 < expected < 1
    assert report["arms"][arm]["mastery"]["rate"] == pytest.approx(expected, abs=1e-12)


@pytest.mark.parametrize("arm", ["treatment", "control"])
def test_missing_outcomes_count_as_not_mastered(positive, arm):
    run, report = positive
    _, _, mature = _arm_facts(run, ARMS[arm])
    mastery = report["arms"][arm]["mastery"]
    assert mastery["missing_outcome"] > 0
    assert mastery["denominator"] == len(mature)
    assert mastery["rate"] == pytest.approx(mastery["mastered"] / mastery["denominator"])
    dropped = mastery["mastered"] / (mastery["denominator"] - mastery["missing_outcome"])
    assert dropped > mastery["rate"]
    assert dropped != pytest.approx(mastery["rate"])


@pytest.mark.parametrize("arm", ["treatment", "control"])
def test_immature_learners_are_excluded_and_reported(positive, arm):
    run, report = positive
    _, eligible, mature = _arm_facts(run, ARMS[arm])
    mastery = report["arms"][arm]["mastery"]
    assert mastery["immature"] == len(eligible) - len(mature)
    assert mastery["immature"] > 0
    assert mastery["denominator"] == len(mature)
    assert mastery["denominator"] + mastery["immature"] == len(eligible)
    assert any("immature" in note for note in report["meta"]["notes"])


def test_fatigue_metrics_reported_for_both_arms(positive):
    _, report = positive
    keys = ("messages_per_learner_per_day", "opt_outs", "cap_breach_learners", "messages_sent")
    for arm in ARMS:
        for key in keys:
            assert key in report["arms"][arm], f"{arm} arm is missing {key}"
    control, treatment = report["arms"]["control"], report["arms"]["treatment"]
    assert control["messages_sent"] == 0
    assert control["opt_outs"] == 0
    assert control["cap_breach_learners"] == 0
    assert treatment["messages_sent"] > 0
    assert treatment["messages_per_learner_per_day"] == pytest.approx(
        treatment["messages_sent"] / (treatment["eligible"] * 14)
    )


def test_cap_stress_breach_is_reported_for_treatment_only():
    _, stress = _build("cap_stress", 1000)
    assert stress["arms"]["treatment"]["cap_breach_learners"] > 0
    assert stress["arms"]["control"]["cap_breach_learners"] == 0

    _, enforced = _build("positive", 1000)
    for arm in ARMS:
        assert enforced["arms"][arm]["cap_breach_learners"] == 0
        assert enforced["arms"][arm]["max_sent_in_24h"] <= CAP


# --- Step 3: comparison behaviour across seeds -------------------------------

import json
from functools import lru_cache

SEEDS = (1, 2, 3, 4, 5)
N_SEEDS = 4000


@lru_cache(maxsize=None)
def _report(setting, seed, n=N_SEEDS):
    return _build(setting, n, seed)[1]


def test_positive_setting_shows_a_positive_difference():
    for seed in SEEDS:
        assert _report("positive", seed)["contrast"]["difference"] > 0, seed


def test_harmful_setting_shows_a_negative_difference():
    for seed in SEEDS:
        assert _report("harmful", seed)["contrast"]["difference"] < 0, seed


def test_null_setting_shows_no_large_difference():
    diffs = [_report("null", seed)["contrast"]["difference"] for seed in SEEDS]
    assert abs(sum(diffs) / len(diffs)) < 0.02, diffs
    assert all(abs(d) < 0.03 for d in diffs), diffs


def test_sample_ratio_check_does_not_alert_on_hash_assignment():
    for seed in SEEDS:
        srm = _report("positive", seed)["srm"]
        assert srm["alert"] is False, (seed, srm["p_value"])
        assert srm["assigned_variant_a"] + srm["assigned_variant_b"] == N_SEEDS


def test_report_is_deterministic():
    first = json.dumps(_build("positive", N_SEEDS, 3)[1], sort_keys=True)
    second = json.dumps(_build("positive", N_SEEDS, 3)[1], sort_keys=True)
    assert first == second


@pytest.mark.parametrize("arm", ["treatment", "control"])
def test_every_assigned_eligible_learner_is_counted_once(arm):
    for seed in SEEDS:
        arm_report = _report("positive", seed)["arms"][arm]
        m = arm_report["mastery"]
        total = (m["mastered"] + m["failed"] + m["missing_outcome"]
                 + m["invalid_ordering"] + m["conflicting"] + m["immature"])
        assert total == arm_report["eligible"], (seed, arm)
