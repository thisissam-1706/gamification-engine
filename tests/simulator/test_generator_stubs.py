"""Simulator stubs (Bias-Avoidance Rules 1-6 of simulator/SPEC.md). Written before the implementation.

The four functions in ``simulator/generator.py`` raise NotImplementedError today. These tests pin the
behaviour the SPEC requires. Assumptions the SPEC does not fix (marked A1-A5 below, listed again in
docs/audit/m7-sim.md) are the event and entry shapes:

A1  ``generate_baseline_stream(persona, days, seed)`` -> list of dicts, each with ``learner_id``
    (the persona's), ``day`` (int, 0 <= day < days) and ``return_probability`` (float in [0, 1], the
    next-day return probability). Other keys are allowed but none may mention notifications.
A2  ``apply_notification_perturbation(baseline_stream, policy, effect_cap=0.05)`` -> a NEW list of the
    same length and order, with the same keys, where each ``return_probability`` differs from the
    baseline's by at most ``effect_cap`` (SPEC rule 3). The baseline list is not mutated.
A3  ``run_sensitivity_sweep(population, param_ranges)`` -> one dict per element of the cartesian
    product of ``param_ranges`` (values taken in the order given), with ``params`` (the combination),
    ``effect`` (float, treatment minus control) and ``status`` ("conclusive" or "inconclusive").
    "Small parameter change" = a neighbouring value of one parameter on the grid; a result whose
    effect has the opposite sign to a neighbour's is "inconclusive" (SPEC rule 5). The parameter
    ``effect_cap`` is the perturbation cap; ``response_bias`` is a signed shift of the learners'
    response to the policy before capping, so it can make the effect change sign.
A4  ``export_event_stream(events, seed_version)`` -> list of dicts: every input event's fields plus
    ``seed_version``, in input order, input not mutated (SPEC rule 6).
A5  Draws come only from the given seed (no clock, no global random state, no hash-order dependence).
"""

import copy
import hashlib
import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

from simulator import generator
from simulator.generator import (
    apply_notification_perturbation,
    create_persona_population,
    export_event_stream,
    generate_baseline_stream,
    run_sensitivity_sweep,
)

ROOT = Path(__file__).resolve().parents[2]
DAYS = 14
POPULATION = create_persona_population(30, seed=7)
POLICY = "streak_risk"


def _baseline(seed: int = 11) -> list:
    return [event for persona in POPULATION for event in generate_baseline_stream(persona, DAYS, seed)]


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


# Rule 4/6: same seed, same output ------------------------------------------------------------


def test_same_seed_gives_the_same_baseline_stream():
    persona = POPULATION[0]
    first = generate_baseline_stream(persona, DAYS, 11)
    second = generate_baseline_stream(persona, DAYS, 11)
    assert first == second
    assert first, "a 14-day baseline should not be empty"


def test_a_different_seed_changes_the_baseline_somewhere():
    assert _baseline(seed=11) != _baseline(seed=12)


def test_baseline_events_have_the_documented_shape():
    persona = POPULATION[3]
    stream = generate_baseline_stream(persona, DAYS, 11)
    for event in stream:
        assert event["learner_id"] == persona.learner_id
        assert isinstance(event["day"], int) and 0 <= event["day"] < DAYS
        assert 0.0 <= event["return_probability"] <= 1.0


def test_perturbation_and_sweep_are_deterministic():
    baseline = _baseline()
    assert apply_notification_perturbation(baseline, POLICY) == apply_notification_perturbation(
        baseline, POLICY
    )
    ranges = {"effect_cap": [0.03, 0.05]}
    assert run_sensitivity_sweep(POPULATION, ranges) == run_sensitivity_sweep(POPULATION, ranges)


# Rule 2: baseline-first, no notification knowledge ---------------------------------------------


def test_baseline_signature_has_no_policy_or_notification_input():
    names = set(inspect.signature(generate_baseline_stream).parameters)
    assert names == {"persona", "days", "seed"}


def test_baseline_events_carry_no_notification_information():
    for event in _baseline():
        for key, value in event.items():
            text = f"{key} {value}".lower()
            assert not any(word in text for word in ("notif", "policy", "message", "reminder")), event


def test_generating_a_baseline_does_not_load_the_notification_code():
    code = (
        "import sys\n"
        "from simulator.generator import create_persona_population, generate_baseline_stream\n"
        "p = create_persona_population(3, seed=7)[0]\n"
        "generate_baseline_stream(p, 14, 11)\n"
        "print(sorted(m for m in sys.modules if m.startswith('src.notifications')))\n"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", code], cwd=ROOT, capture_output=True, text=True, check=True,
        env={"PYTHONPATH": str(ROOT)},
    )
    assert out.stdout.strip() == "[]"


def test_applying_a_perturbation_leaves_the_baseline_untouched_and_unchanged_on_regeneration():
    baseline = _baseline()
    snapshot = copy.deepcopy(baseline)
    apply_notification_perturbation(baseline, POLICY)
    assert baseline == snapshot
    assert _baseline() == snapshot  # regenerating the baseline afterwards gives the same draws


# Rule 3: bounded perturbation ------------------------------------------------------------------


def test_default_effect_cap_is_five_percent():
    assert inspect.signature(apply_notification_perturbation).parameters["effect_cap"].default == 0.05


@pytest.mark.parametrize("cap", [None, 0.03, 0.01, 0.0])
def test_the_perturbation_never_exceeds_the_effect_cap(cap):
    baseline = _baseline()
    kwargs = {} if cap is None else {"effect_cap": cap}
    limit = 0.05 if cap is None else cap
    perturbed = apply_notification_perturbation(baseline, POLICY, **kwargs)
    assert len(perturbed) == len(baseline)
    for before, after in zip(baseline, perturbed):
        assert after["learner_id"] == before["learner_id"] and after["day"] == before["day"]
        assert abs(after["return_probability"] - before["return_probability"]) <= limit + 1e-12
        assert 0.0 <= after["return_probability"] <= 1.0


def test_the_default_cap_actually_moves_something():
    baseline = _baseline()
    perturbed = apply_notification_perturbation(baseline, POLICY)
    assert any(a["return_probability"] != b["return_probability"] for a, b in zip(baseline, perturbed))


def test_a_zero_cap_changes_nothing():
    baseline = _baseline()
    assert apply_notification_perturbation(baseline, POLICY, effect_cap=0.0) == baseline


# Rule 5: sensitivity sweeps --------------------------------------------------------------------


def test_sweep_returns_one_entry_per_parameter_combination():
    ranges = {"effect_cap": [0.01, 0.03, 0.05], "response_bias": [0.0, 0.02]}
    results = run_sensitivity_sweep(POPULATION, ranges)
    assert len(results) == 6
    combos = {tuple(sorted(r["params"].items())) for r in results}
    expected = {
        tuple(sorted({"effect_cap": c, "response_bias": b}.items()))
        for c in ranges["effect_cap"]
        for b in ranges["response_bias"]
    }
    assert combos == expected
    for result in results:
        assert isinstance(result["effect"], float)
        assert result["status"] in {"conclusive", "inconclusive"}


def test_sweep_over_a_single_value_has_one_entry():
    (only,) = run_sensitivity_sweep(POPULATION, {"effect_cap": [0.05]})
    assert only["params"] == {"effect_cap": 0.05}


def test_results_that_flip_sign_under_a_small_change_are_marked_inconclusive():
    # response_bias runs from clearly negative to clearly positive; the effect changes sign
    # between -0.01 and +0.01, which are neighbours on the grid.
    ranges = {"effect_cap": [0.05], "response_bias": [-0.04, -0.01, 0.01, 0.04]}
    results = run_sensitivity_sweep(POPULATION, ranges)
    by_bias = {r["params"]["response_bias"]: r for r in results}
    assert by_bias[-0.01]["effect"] < 0 < by_bias[0.01]["effect"]
    assert by_bias[-0.01]["status"] == "inconclusive"
    assert by_bias[0.01]["status"] == "inconclusive"
    assert by_bias[-0.04]["status"] == "conclusive"
    assert by_bias[0.04]["status"] == "conclusive"


def test_results_that_keep_their_sign_are_conclusive():
    results = run_sensitivity_sweep(
        POPULATION, {"effect_cap": [0.05], "response_bias": [0.01, 0.02, 0.04]}
    )
    assert all(r["effect"] > 0 for r in results)
    assert {r["status"] for r in results} == {"conclusive"}


# Rule 6: export, byte-for-byte, seed version -----------------------------------------------------

EVENTS = [
    {"learner_id": "L00001", "day": 0, "return_probability": 0.5},
    {"learner_id": "L00001", "day": 1, "return_probability": 0.25},
    {"learner_id": "L00002", "day": 0, "return_probability": 0.75, "extra": {"b": 2, "a": 1}},
]


def test_export_carries_the_seed_version_on_every_event_and_keeps_order():
    exported = export_event_stream(EVENTS, "sim-v1")
    assert len(exported) == len(EVENTS)
    for original, record in zip(EVENTS, exported):
        assert record["seed_version"] == "sim-v1"
        assert {k: v for k, v in record.items() if k != "seed_version"} == original


def test_export_does_not_mutate_its_input():
    snapshot = copy.deepcopy(EVENTS)
    export_event_stream(EVENTS, "sim-v1")
    assert EVENTS == snapshot


def test_export_is_byte_for_byte_reproducible_in_process():
    assert _canonical(export_event_stream(EVENTS, "sim-v1")) == _canonical(
        export_event_stream(EVENTS, "sim-v1")
    )


def test_a_different_seed_version_changes_the_exported_bytes():
    assert _canonical(export_event_stream(EVENTS, "sim-v1")) != _canonical(
        export_event_stream(EVENTS, "sim-v2")
    )


def test_export_of_a_generated_stream_is_identical_across_interpreters_with_different_hash_seeds():
    code = (
        "import hashlib, json\n"
        "from simulator.generator import (create_persona_population, export_event_stream,\n"
        "    generate_baseline_stream)\n"
        "pop = create_persona_population(10, seed=7)\n"
        "events = [e for p in pop for e in generate_baseline_stream(p, 14, 11)]\n"
        "blob = json.dumps(export_event_stream(events, 'sim-v1'), sort_keys=True,\n"
        "                  separators=(',', ':')).encode()\n"
        "print(hashlib.sha256(blob).hexdigest())\n"
    )
    digests = set()
    for hash_seed in ("0", "1", "12345"):
        out = subprocess.run(
            [sys.executable, "-I", "-c", code], cwd=ROOT, capture_output=True, text=True, check=True,
            env={"PYTHONPATH": str(ROOT), "PYTHONHASHSEED": hash_seed},
        )
        digests.add(out.stdout.strip())
    assert len(digests) == 1 and len(next(iter(digests))) == 64
