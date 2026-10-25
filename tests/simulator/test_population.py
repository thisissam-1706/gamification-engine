"""Pilot 1: deterministic persona population and common random numbers."""

from collections import Counter
from dataclasses import asdict

import pytest

from simulator.generator import (
    apply_notification_perturbation,
    create_persona_population,
    export_event_stream,
    generate_baseline_stream,
    generate_common_random_numbers,
    run_sensitivity_sweep,
)

SCALARS = (
    "assign", "tenure", "consent", "attempt", "attempt_day", "attempt_hour",
    "passed", "completion_logged", "completion_lag", "expose",
)


def _as_dicts(population):
    return [asdict(p) for p in population]


def test_same_size_and_seed_give_identical_populations():
    assert _as_dicts(create_persona_population(200, 7)) == _as_dicts(
        create_persona_population(200, 7)
    )


def test_different_seed_gives_different_population():
    assert _as_dicts(create_persona_population(200, 7)) != _as_dicts(
        create_persona_population(200, 8)
    )


def test_population_is_a_prefix_of_a_larger_population():
    small = create_persona_population(100, 7)
    large = create_persona_population(200, 7)
    assert _as_dicts(small) == _as_dicts(large[:100])


def test_learner_ids_are_zero_padded_and_seed_is_recorded():
    population = create_persona_population(10, 42)
    assert [p.learner_id for p in population] == [f"L{i:05d}" for i in range(10)]
    assert {p.seed for p in population} == {42}


def test_crn_draws_for_a_learner_do_not_depend_on_population_size():
    small = generate_common_random_numbers(create_persona_population(50, 3), 3)
    large = generate_common_random_numbers(create_persona_population(500, 3), 3)
    assert small["L00007"] == large["L00007"]


def test_crn_draws_are_in_unit_interval_with_21_day_lists():
    draws = generate_common_random_numbers(create_persona_population(30, 5), 5)
    assert len(draws) == 30
    for per_learner in draws.values():
        for key in SCALARS:
            assert 0.0 <= per_learner[key] < 1.0
        for key in ("deliver", "optout"):
            values = per_learner[key]
            assert len(values) == 21
            assert all(isinstance(v, float) and 0.0 <= v < 1.0 for v in values)


def test_persona_shares_match_target_mix():
    population = create_persona_population(4000, 11)
    counts = Counter(p.persona_type for p in population)
    targets = {
        "consistent": 0.30, "binge_then_lapse": 0.25,
        "weekend_only": 0.25, "at_risk": 0.20,
    }
    assert set(counts) == set(targets)
    for persona, share in targets.items():
        assert abs(counts[persona] / 4000 - share) <= 0.03


def test_probabilities_are_clamped():
    for p in create_persona_population(4000, 11):
        assert 0.01 <= p.dropout_probability <= 0.99
        assert 0.01 <= p.base_pass_probability <= 0.99


def test_other_entry_points_still_not_implemented():
    population = create_persona_population(2, 1)
    with pytest.raises(NotImplementedError):
        run_sensitivity_sweep(population, {})
    with pytest.raises(NotImplementedError):
        apply_notification_perturbation([], "policy")
    with pytest.raises(NotImplementedError):
        export_event_stream([], "v1")
    with pytest.raises(NotImplementedError):
        generate_baseline_stream(population[0], 7, 1)
