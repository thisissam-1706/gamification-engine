"""Pilot 2: simulated event streams for exp_007."""

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from simulator.generator import create_persona_population, generate_common_random_numbers
from simulator.pilot import SETTINGS, simulate
from src.experiments.assignment import assign

N, SEED = 600, 7
SENT = ("delivered", "provider_failed")


def _ts(value):
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def world():
    population = create_persona_population(N, SEED)
    draws = generate_common_random_numbers(population, SEED)
    return population, draws


@pytest.fixture(scope="module")
def runs(world):
    population, draws = world
    return {name: simulate(population, draws, name, SEED) for name in SETTINGS}


def _by_learner(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["learner_id"]].append(row)
    return grouped


def _max_sent_in_24h(deliveries):
    worst = 0
    for learner_rows in _by_learner(deliveries).values():
        times = sorted(_ts(r["scheduled_at"]) for r in learner_rows if r["status"] in SENT)
        for t in times:
            worst = max(worst, sum(1 for s in times if t - timedelta(hours=24) < s <= t))
    return worst


def test_control_has_no_deliveries_or_exposures_and_ineligible_never_sent(runs):
    run = runs["positive"]
    variants = {a["learner_id"]: a["variant"] for a in run.assignments}
    eligible = {e["learner_id"]: e["eligible"] for e in run.eligibility}
    control = {l for l, v in variants.items() if v == "variant_b"}
    assert control and any(v == "variant_a" for v in variants.values())
    assert not [d for d in run.deliveries if d["learner_id"] in control]
    assert not [x for x in run.exposures if x["learner_id"] in control]
    for d in run.decisions:
        if not eligible[d["learner_id"]]:
            assert d["action"] != "send_reminders"
    assert not all(eligible.values())


def test_assignment_matches_assign_and_propensity_is_half(runs):
    for a in runs["null"].assignments:
        assert a["variant"] == assign(a["learner_id"], "exp_007")[0]
        assert a["propensity"] == 0.5


def test_delivery_statuses_and_consent_suppression_follow_opt_out(runs):
    for run in runs.values():
        for learner_rows in _by_learner(run.deliveries).values():
            opted_out = False
            for row in sorted(learner_rows, key=lambda r: r["scheduled_at"]):
                assert row["status"] in (
                    "delivered", "provider_failed", "suppressed_cap", "suppressed_consent"
                )
                if row["status"] == "suppressed_consent":
                    assert opted_out
                if row["opted_out_after"]:
                    opted_out = True
    assert any(d["status"] == "suppressed_consent" for d in runs["cap_stress"].deliveries)


def test_exposures_only_for_learners_with_a_delivered_message(runs):
    for run in runs.values():
        delivered = {d["learner_id"] for d in run.deliveries if d["status"] == "delivered"}
        exposed = [x["learner_id"] for x in run.exposures]
        assert exposed and set(exposed) <= delivered


def test_cap_stress_exceeds_cap_and_enforced_cap_does_not(world, runs):
    population, draws = world
    assert _max_sent_in_24h(runs["cap_stress"].deliveries) > 5
    enforced = simulate(population, draws, dict(SETTINGS["cap_stress"], enforce_cap=True), SEED)
    assert _max_sent_in_24h(enforced.deliveries) <= 5
    assert any(d["status"] == "suppressed_cap" for d in enforced.deliveries)


def test_same_inputs_give_identical_run(world):
    population, draws = world
    assert simulate(population, draws, "positive", SEED) == simulate(
        population, draws, "positive", SEED
    )


def test_decisions_do_not_depend_on_outcome_draws(world, runs):
    population, draws = world
    changed = deepcopy(draws)
    for per_learner in changed.values():
        for key in ("attempt", "passed", "completion_logged"):
            per_learner[key] = 0.5
    assert simulate(population, changed, "positive", SEED).decisions == runs["positive"].decisions


def test_attempt_times_are_common_across_null_and_positive(runs):
    null = {a["learner_id"]: a["occurred_at"] for a in runs["null"].attempts}
    positive = {a["learner_id"]: a["occurred_at"] for a in runs["positive"].attempts}
    both = set(null) & set(positive)
    assert both
    assert all(null[l] == positive[l] for l in both)


def test_attempts_fall_between_day_1_and_day_14_after_assignment(runs):
    for run in runs.values():
        assigned = {a["learner_id"]: _ts(a["assigned_at"]) for a in run.assignments}
        assert run.attempts
        for attempt in run.attempts:
            start = assigned[attempt["learner_id"]]
            assert start + timedelta(days=1) <= _ts(attempt["occurred_at"]) <= start + timedelta(days=14)


def test_decisions_identical_across_settings_and_independent_of_other_draws(world, runs):
    population, draws = world
    reference = runs["null"].decisions
    assert all(runs[name].decisions == reference for name in SETTINGS)

    changed = deepcopy(draws)
    for per_learner in changed.values():
        for key in ("attempt", "attempt_day", "attempt_hour", "passed",
                    "completion_logged", "completion_lag", "expose"):
            per_learner[key] = 0.5
        per_learner["deliver"] = [0.5] * 21
        per_learner["optout"] = [0.5] * 21
    for name in SETTINGS:
        assert simulate(population, changed, name, SEED).decisions == reference


# --- exposure only counts if it precedes the would-be attempt -----------------

def _crafted(attempt_day):
    from simulator.generator import PersonaConfig

    i = 0
    while assign(f"L{i:05d}", "exp_007")[0] != "variant_a":
        i += 1
    learner_id = f"L{i:05d}"
    persona = PersonaConfig(
        persona_type="consistent", session_frequency=7, mastery_growth=0.05,
        dropout_probability=0.55, seed=1, learner_id=learner_id, base_pass_probability=0.5,
    )
    draws = {learner_id: {
        "assign": 0.0, "tenure": 0.9, "consent": 0.9, "attempt": 0.47,
        "attempt_day": attempt_day, "attempt_hour": 0.0, "passed": 0.0,
        "completion_logged": 0.0, "completion_lag": 0.0, "expose": 0.0,
        "deliver": [0.0] * 21, "optout": [1.0] * 21,
    }}
    return simulate([persona], draws, "positive", 1)


def test_exposure_after_the_would_be_attempt_does_not_help():
    run = _crafted(0.0)  # attempt on day 1 at 00:00, first reminder on day 1 at 09:00
    assert [x["before_attempt"] for x in run.exposures] == [False]
    assert run.attempts == []


def test_exposure_before_the_would_be_attempt_helps():
    run = _crafted(0.5)  # attempt on day 7
    assert [x["before_attempt"] for x in run.exposures] == [True]
    assert len(run.attempts) == 1


def test_non_effective_learners_have_identical_outcomes_in_null_and_positive(world):
    population, draws = world
    null = simulate(population, draws, "null", SEED)
    positive = simulate(population, draws, "positive", SEED)
    exposed = {x["learner_id"]: x["before_attempt"] for x in positive.exposures}
    effective = {l for l, before in exposed.items() if before}
    late = {l for l, before in exposed.items() if not before}
    assert late and effective
    skip = effective

    def pick(rows):
        return [r for r in rows if r["learner_id"] not in skip]

    assert pick(null.attempts) == pick(positive.attempts)
    assert pick(null.completions) == pick(positive.completions)


def test_reminders_start_on_day_one_after_assignment(runs):
    assigned = {a["learner_id"]: _ts(a["assigned_at"]) for a in runs["null"].assignments}
    for d in runs["null"].deliveries:
        offset = _ts(d["scheduled_at"]) - assigned[d["learner_id"]]
        assert timedelta(days=1, hours=9) <= offset <= timedelta(days=3, hours=9)
