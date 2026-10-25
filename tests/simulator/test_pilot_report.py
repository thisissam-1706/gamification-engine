"""Pilot 3: intention-to-treat report over a simulated run."""

import copy
import math
from pathlib import Path

import pytest
from datetime import datetime, timedelta, timezone

from simulator.generator import create_persona_population, generate_common_random_numbers
from simulator.pilot import SETTINGS, PilotRun, simulate
from simulator.pilot_report import analyze, max_in_rolling_24h, srm_check

T0 = datetime(2026, 9, 1, 9, tzinfo=timezone.utc)


def _iso(value):
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_rolling_window_helper():
    assert max_in_rolling_24h([]) == 0
    assert max_in_rolling_24h([_iso(T0 + timedelta(hours=h)) for h in range(6)]) == 6
    # [t, t+24h): the second message sits exactly on the excluded end.
    assert max_in_rolling_24h([_iso(T0), _iso(T0 + timedelta(hours=24))]) == 1


def test_srm_balanced_and_imbalanced():
    balanced = srm_check(500, 500)
    assert balanced["chi2"] == 0 and balanced["p_value"] == 1.0 and balanced["alert"] is False
    skewed = srm_check(600, 400)
    assert math.isclose(skewed["chi2"], 40)
    assert skewed["p_value"] < 0.001 and skewed["alert"] is True


def _fixture_run(assigned_day=1, treatment_pass=50, control_pass=40, excluded=0):
    """100 eligible learners per arm; `*_pass` of them attempt and pass on day 3."""
    assignments, eligibility, decisions, attempts, completions = [], [], [], [], []
    assigned_at = datetime(2026, 9, assigned_day, tzinfo=timezone.utc)
    for arm, variant, passing in (("T", "variant_a", treatment_pass), ("C", "variant_b", control_pass)):
        for i in range(100 + excluded):
            learner_id = f"{arm}{i:03d}"
            assignments.append({
                "learner_id": learner_id, "exp_id": "exp_007", "variant": variant,
                "propensity": 0.5, "assigned_at": _iso(assigned_at),
            })
            is_eligible = i < 100
            eligibility.append({
                "learner_id": learner_id, "eligible": is_eligible,
                "reason": None if is_eligible else "tenure_lt_3_days",
            })
            decisions.append({"learner_id": learner_id, "action": "no_action",
                              "policy": None, "reason": "control"})
            if i < passing:  # excluded learners (i >= 100) also pass: must be ignored
                attempt_at = assigned_at + timedelta(days=2)
                attempts.append({
                    "learner_id": learner_id, "attempt_id": f"A-{learner_id}",
                    "assessment_id": "ckpt_1", "assessment_version": 1,
                    "occurred_at": _iso(attempt_at),
                })
                completions.append({
                    "learner_id": learner_id, "attempt_id": f"A-{learner_id}",
                    "completion_id": f"C-{learner_id}", "assessment_id": "ckpt_1",
                    "assessment_version": 1, "passed": True,
                    "occurred_at": _iso(attempt_at + timedelta(minutes=20)),
                })
    if excluded:
        for arm in ("T", "C"):
            for i in range(100, 100 + excluded):
                learner_id = f"{arm}{i:03d}"
                attempt_at = assigned_at + timedelta(days=2)
                attempts.append({
                    "learner_id": learner_id, "attempt_id": f"A-{learner_id}",
                    "assessment_id": "ckpt_1", "assessment_version": 1,
                    "occurred_at": _iso(attempt_at),
                })
                completions.append({
                    "learner_id": learner_id, "attempt_id": f"A-{learner_id}",
                    "completion_id": f"C-{learner_id}", "assessment_id": "ckpt_1",
                    "assessment_version": 1, "passed": True,
                    "occurred_at": _iso(attempt_at + timedelta(minutes=20)),
                })
    return PilotRun(
        assignments=assignments, eligibility=eligibility, decisions=decisions,
        deliveries=[], exposures=[], attempts=attempts, completions=completions,
        setting_name="fixture", seed=0, population_size=len(assignments),
    )


def test_contrast_math_on_hand_built_run():
    report = analyze(_fixture_run())
    contrast = report["contrast"]
    assert report["arms"]["treatment"]["mastery"]["mastered"] == 50
    assert report["arms"]["control"]["mastery"]["mastered"] == 40
    assert math.isclose(contrast["difference"], 0.1)
    assert math.isclose(contrast["standard_error"], math.sqrt(0.25 / 100 + 0.24 / 100))
    assert math.isclose(contrast["ci95_low"], 0.1 - 1.96 * 0.07)
    assert math.isclose(contrast["ci95_high"], 0.1 + 1.96 * 0.07)
    assert abs(contrast["ci95_high"] - 0.2372) < 1e-3
    assert contrast["conclusive"] is False


def test_recent_assignment_is_immature_at_the_cutoff():
    report = analyze(_fixture_run(assigned_day=20))
    for arm in report["arms"].values():
        mastery = arm["mastery"]
        assert mastery["immature"] == 100 and mastery["denominator"] == 0
        assert mastery["denominator"] + mastery["immature"] == arm["eligible"]
    assert report["contrast"]["difference"] is None
    assert report["contrast"]["conclusive"] is None


def test_excluded_learners_are_not_counted():
    report = analyze(_fixture_run(excluded=10))
    for arm in report["arms"].values():
        assert arm["excluded"] == 10 and arm["assigned"] == 110
        assert arm["eligible"] + arm["excluded"] == arm["assigned"]
        assert arm["mastery"]["assigned"] == arm["eligible"]
        assert arm["mastery"]["denominator"] + arm["mastery"]["immature"] == arm["eligible"]
    assert report["arms"]["treatment"]["mastery"]["mastered"] == 50
    assert report["srm"]["assigned_variant_a"] == 110


def _real_run(setting, n=300):
    population = create_persona_population(n, 7)
    draws = generate_common_random_numbers(population, 7)
    return simulate(population, draws, setting, 7)


def test_null_run_control_untouched_and_analyze_is_pure():
    run = _real_run("null")
    before = copy.deepcopy(run)
    first, second = analyze(run), analyze(run)
    assert first == second and run == before
    control = first["arms"]["control"]
    assert control["exposed"] == 0 and control["messages_sent"] == 0
    assert first["arms"]["treatment"]["messages_sent"] > 0
    assert first["meta"]["setting"] == "null" and first["meta"]["data_cutoff"] == "2026-09-29T00:00:00Z"
    assert len(first["meta"]["notes"]) == 3


def test_cap_stress_breaches_only_in_treatment():
    report = analyze(_real_run("cap_stress"))
    assert report["arms"]["treatment"]["cap_breach_learners"] > 0
    assert report["arms"]["treatment"]["max_sent_in_24h"] > 5
    assert report["arms"]["control"]["cap_breach_learners"] == 0
    assert report["arms"]["control"]["max_sent_in_24h"] == 0


# --- buckets -------------------------------------------------------------

def _bucket_fixture():
    """One arm, all eligible: mastered, failed, no attempt, attempt only, immature."""
    plan = {
        "L1": ("2026-09-01", "pass"),
        "L2": ("2026-09-01", "fail"),
        "L3": ("2026-09-01", None),
        "L4": ("2026-09-01", "attempt_only"),
        "L5": ("2026-09-20", None),
    }
    assignments, eligibility, decisions, attempts, completions = [], [], [], [], []
    for learner_id, (day, outcome) in plan.items():
        assigned_at = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        assignments.append({
            "learner_id": learner_id, "exp_id": "exp_007", "variant": "variant_a",
            "propensity": 0.5, "assigned_at": _iso(assigned_at),
        })
        eligibility.append({"learner_id": learner_id, "eligible": True, "reason": None})
        decisions.append({"learner_id": learner_id, "action": "no_action",
                          "policy": None, "reason": "control"})
        if outcome is None:
            continue
        attempt_at = assigned_at + timedelta(days=2)
        attempts.append({
            "learner_id": learner_id, "attempt_id": f"A-{learner_id}",
            "assessment_id": "ckpt_1", "assessment_version": 1,
            "occurred_at": _iso(attempt_at),
        })
        if outcome != "attempt_only":
            completions.append({
                "learner_id": learner_id, "attempt_id": f"A-{learner_id}",
                "completion_id": f"C-{learner_id}", "assessment_id": "ckpt_1",
                "assessment_version": 1, "passed": outcome == "pass",
                "occurred_at": _iso(attempt_at + timedelta(minutes=20)),
            })
    return PilotRun(
        assignments=assignments, eligibility=eligibility, decisions=decisions,
        deliveries=[], exposures=[], attempts=attempts, completions=completions,
        setting_name="fixture", seed=0, population_size=len(assignments),
    )


def test_buckets_on_hand_built_run():
    report = analyze(_bucket_fixture())
    buckets = report["arms"]["treatment"]["buckets"]
    assert buckets["mature_with_outcome"] == 2
    assert buckets["mature_no_outcome"] == 2
    assert buckets["missing_no_attempt"] == 1
    assert buckets["missing_attempt_no_completion"] == 1
    assert buckets["mature_invalid"] == 0
    assert buckets["immature"] == 1
    assert buckets["headline_denominator"] == 4
    assert buckets["headline_rate"] == 1 / 4
    assert report["arms"]["treatment"]["mastery"]["denominator"] == 4
    assert any("headline" in note for note in report["meta"]["notes"])
    assert "buckets" in report["arms"]["control"]


def test_buckets_sum_to_eligible_on_real_run():
    population = create_persona_population(300, 7)
    draws = generate_common_random_numbers(population, 7)
    report = analyze(simulate(population, draws, "null", 7))
    for arm in report["arms"].values():
        b = arm["buckets"]
        assert (
            b["mature_with_outcome"] + b["mature_no_outcome"]
            + b["mature_invalid"] + b["immature"]
        ) == arm["eligible"]
        assert b["missing_no_attempt"] + b["missing_attempt_no_completion"] == b["mature_no_outcome"]
        assert b["headline_denominator"] == arm["mastery"]["denominator"]
        assert b["headline_rate"] == arm["mastery"]["rate"]


# --- funnel, SRM metadata, cap enforcement ---------------------------------

ROOT = Path(__file__).resolve().parents[2]


def _report(setting, n=300):
    population = create_persona_population(n, 7)
    draws = generate_common_random_numbers(population, 7)
    return analyze(simulate(population, draws, setting, 7))


FUNNEL_KEYS = {
    "assigned", "excluded_tenure", "excluded_consent", "eligible",
    "decided_send_reminders", "decided_no_action", "any_message_sent",
    "any_message_delivered", "exposed", "exposed_before_attempt",
    "exposed_after_attempt", "mature",
    "immature", "attempted", "completion_logged", "mastered",
}


def test_funnel_invariants_on_real_null_run():
    report = _report("null", n=600)
    for name, arm in report["arms"].items():
        f = arm["funnel"]
        assert set(f) == FUNNEL_KEYS
        assert (
            f["assigned"] >= f["eligible"] >= f["decided_send_reminders"]
            >= f["any_message_sent"] >= f["any_message_delivered"] >= f["exposed"]
        )
        assert f["excluded_tenure"] + f["excluded_consent"] + f["eligible"] == f["assigned"]
        assert f["mature"] + f["immature"] == f["eligible"]
        assert f["decided_send_reminders"] + f["decided_no_action"] == f["eligible"]
        assert f["attempted"] >= f["completion_logged"] >= f["mastered"]
        assert f["mastered"] == arm["mastery"]["mastered"]
        assert f["exposed_before_attempt"] <= f["exposed"]
    control = report["arms"]["control"]["funnel"]
    assert control["decided_send_reminders"] == 0 and control["exposed"] == 0
    assert report["arms"]["treatment"]["funnel"]["decided_send_reminders"] > 0


@pytest.mark.parametrize("setting", ["null", "positive", "harmful"])
def test_enforced_settings_never_breach_the_cap(setting):
    arms = _report(setting)["arms"]
    treatment, control = arms["treatment"], arms["control"]
    assert treatment["cap_breach_learners"] == 0
    assert treatment["max_sent_in_24h"] <= 5
    assert treatment["suppressed_cap"] == 0
    for key in ("cap_breach_learners", "max_sent_in_24h", "suppressed_cap"):
        assert control[key] == 0


def test_cap_enforced_stress_suppresses_instead_of_breaching():
    treatment = _report({**SETTINGS["cap_stress"], "enforce_cap": True})["arms"]["treatment"]
    assert treatment["cap_breach_learners"] == 0
    assert treatment["suppressed_cap"] > 0


def test_srm_metadata_and_threshold_in_exp_007_yaml():
    report = _report("null")
    for srm in (report["srm"], report["srm_eligible"]):
        assert srm["test"] == "chi_square_1df"
        assert srm["alpha"] == 0.001
        assert srm["expected_split"] == "50/50"
    assert report["srm_eligible"]["assigned_variant_a"] == report["arms"]["treatment"]["eligible"]
    assert report["srm_eligible"]["assigned_variant_b"] == report["arms"]["control"]["eligible"]
    text = (ROOT / "experiments" / "exp_007.yaml").read_text(encoding="utf-8")
    assert 'alert_threshold: "p < 0.001"' in text


# --- exposure order and cutoff-consistent ledger ----------------------------

def _real(setting="positive", n=600):
    population = create_persona_population(n, 7)
    draws = generate_common_random_numbers(population, 7)
    run = simulate(population, draws, setting, 7)
    return run, analyze(run)


def test_funnel_exposure_order_matches_ledger_flags():
    from simulator.pilot_report import build_ledger

    run, report = _real()
    ledger = build_ledger(run)
    for arm, variant in (("treatment", "variant_a"), ("control", "variant_b")):
        funnel = report["arms"][arm]["funnel"]
        before = sum(1 for r in ledger if r["variant"] == variant and r["exposure_before_attempt"] is True)
        after = sum(1 for r in ledger if r["variant"] == variant and r["exposure_before_attempt"] is False)
        assert funnel["exposed_before_attempt"] == before
        assert funnel["exposed_after_attempt"] == after
        assert funnel["exposed_before_attempt"] + funnel["exposed_after_attempt"] == funnel["exposed"]
    treatment = report["arms"]["treatment"]["funnel"]
    assert treatment["exposed_before_attempt"] > 0 and treatment["exposed_after_attempt"] > 0


def test_ledger_only_shows_records_up_to_the_cutoff_and_matches_funnel():
    from simulator.pilot_report import build_ledger

    run, report = _real()
    ledger = build_ledger(run)
    cutoff = "2026-09-29T00:00:00Z"
    for row in ledger:
        if row["attempt_at"] is not None:
            assert row["attempt_at"] <= cutoff
        if not row["attempted"]:
            assert row["attempt_at"] is None
        if not row["completed"]:
            assert row["passed"] is None
    for arm, variant in (("treatment", "variant_a"), ("control", "variant_b")):
        mature = [
            r for r in ledger
            if r["variant"] == variant and r["eligible"]
            and _parse(r["assigned_at"]) + timedelta(days=14) <= _parse(cutoff)
        ]
        funnel = report["arms"][arm]["funnel"]
        assert sum(1 for r in mature if r["attempted"]) == funnel["attempted"]
        assert sum(1 for r in mature if r["completed"]) == funnel["completion_logged"]


def _parse(value):
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
