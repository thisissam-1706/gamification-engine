"""AT-12: Deterministic Experiment Assignment (M7)

Given learner L1 and experiment exp_007 with a fixed hash algorithm v2, when assignment runs twice, then L1 is bucketed into the same variant both times, with the propensity score logged on the event.
"""
import pytest

from src.experiments.assignment import assign


def test_same_learner_is_assigned_deterministically_with_propensity():
    first = assign("L1", "exp_007")
    second = assign("L1", "exp_007")

    assert first == second
    assert first[0] in {"variant_a", "variant_b"}
    assert first[1] == 0.5


def test_assignment_is_within_two_percent_of_fifty_fifty():
    assignments = [
        assign(f"synthetic-{index}", "exp_007")[0] for index in range(10_000)
    ]
    treatment_share = assignments.count("variant_a") / len(assignments)

    assert abs(treatment_share - 0.5) <= 0.02


@pytest.mark.skip(reason="Exposure logging is not implemented yet.")
def test_exposure_is_logged_only_after_treatment_surface_view():
    pass
