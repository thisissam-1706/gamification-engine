"""
Synthetic Event Generator

This module provides the synthetic event generator for the simulator.
"""

import hashlib
import math
from typing import TypedDict, List, Dict
from dataclasses import dataclass

@dataclass
class PersonaConfig:
    persona_type: str
    session_frequency: float
    mastery_growth: float
    dropout_probability: float
    seed: int
    learner_id: str = ""
    base_pass_probability: float = 0.5


def _u(seed: int, learner_id: str, purpose: str, index: int = 0) -> float:
    """Stateless uniform draw in [0, 1) keyed by (seed, learner, purpose, index)."""
    key = f"{seed}|{learner_id}|{purpose}|{index}".encode()
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "big") / 2**64


# (dropout, base_pass, session_frequency, mastery_growth) per persona.
# session_frequency and mastery_growth are stored but not used by the pilot
# outcome model.
_PERSONA_BASES = {
    "consistent": (0.10, 0.70, 7, 0.05),
    "binge_then_lapse": (0.30, 0.55, 4, 0.03),
    "weekend_only": (0.25, 0.60, 2, 0.03),
    "at_risk": (0.50, 0.40, 1, 0.01),
}


def _clamp(value: float) -> float:
    return min(0.99, max(0.01, value))


def create_persona_population(size: int, seed: int) -> list:
    """
    Creates diverse population by sampling persona types and parameters.

    Each learner is derived only from (seed, learner_id), so a smaller
    population is always a prefix of a larger one.
    """
    population = []
    for i in range(size):
        learner_id = f"L{i:05d}"
        roll = _u(seed, learner_id, "persona")
        if roll < 0.30:
            persona = "consistent"
        elif roll < 0.55:
            persona = "binge_then_lapse"
        elif roll < 0.80:
            persona = "weekend_only"
        else:
            persona = "at_risk"
        dropout, base_pass, frequency, growth = _PERSONA_BASES[persona]
        dropout += (_u(seed, learner_id, "jit_drop") - 0.5) * 0.10
        base_pass += (_u(seed, learner_id, "jit_pass") - 0.5) * 0.10
        population.append(
            PersonaConfig(
                persona_type=persona,
                session_frequency=frequency,
                mastery_growth=growth,
                dropout_probability=_clamp(dropout),
                seed=seed,
                learner_id=learner_id,
                base_pass_probability=_clamp(base_pass),
            )
        )
    return population


# --- baseline stream, bounded perturbation, sweeps and export (SPEC Bias-Avoidance Rules 2, 3, 5, 6) ---

# Probabilities in the streams are real probabilities, unlike the persona parameters, which _clamp keeps
# inside [0.01, 0.99].
def _unit(value: float) -> float:
    return min(1.0, max(0.0, value))


# The most a learner's own response to a notification can move the next-day return probability before the
# cap applies. Draws are keyed by (policy, learner, day) with a fixed key, never by the baseline seed.
RESPONSE_GAIN_MAX = 0.01


def generate_baseline_stream(persona: PersonaConfig, days: int, seed: int) -> list:
    """
    Generates baseline activity events with no notification knowledge.

    One event per active day (day 0 is always active), as dicts with ``learner_id``, ``day`` and
    ``return_probability`` (the next-day return probability). Everything is drawn from
    ``(seed, learner_id, purpose, day)`` only; this function takes no policy and imports no notification code.
    A learner who drops out stays inactive for the rest of the window.
    """
    if days < 0:
        raise ValueError("days must not be negative")
    learner_id = persona.learner_id
    session_probability = min(1.0, persona.session_frequency / 7)
    base_return = 0.5 * (1.0 - persona.dropout_probability) + 0.5 * session_probability
    daily_dropout = persona.dropout_probability / 7
    events: list = []
    for day in range(days):
        if day > 0:
            if _u(seed, learner_id, "dropout", day) < daily_dropout:
                break
            if _u(seed, learner_id, "session", day) >= session_probability:
                continue
        noise = (_u(seed, learner_id, "return", day) - 0.5) * 0.10
        events.append(
            {
                "learner_id": learner_id,
                "day": day,
                "return_probability": _unit(base_return + noise),
            }
        )
    return events


def apply_notification_perturbation(
    baseline_stream: list,
    policy: str,
    effect_cap: float = 0.05,
    *,
    response_bias: float = 0.0,
) -> list:
    """
    Applies bounded perturbation to baseline.

    Returns a NEW list, same length and order, with the same keys; the baseline is not changed. Each event's
    ``return_probability`` moves by the learner's response to ``policy`` (a draw in [0, RESPONSE_GAIN_MAX)
    keyed by policy, learner and day, plus ``response_bias``), clamped to ``[-effect_cap, +effect_cap]``, and the
    result stays a probability. So ``|new - old| <= effect_cap`` always (SPEC rule 3).
    """
    if effect_cap < 0:
        raise ValueError("effect_cap must not be negative")
    perturbed = []
    for event in baseline_stream:
        gain = RESPONSE_GAIN_MAX * _u(0, event["learner_id"], f"response:{policy}", event["day"])
        shift = max(-effect_cap, min(effect_cap, gain + response_bias))
        changed = dict(event)
        changed["return_probability"] = _unit(event["return_probability"] + shift)
        perturbed.append(changed)
    return perturbed


def generate_common_random_numbers(population: list, seed: int) -> dict:
    """
    Generates shared random draws for treatment/control.

    Draws depend only on (seed, learner_id), never on population size.
    """
    scalars = (
        "assign", "tenure", "consent", "attempt", "attempt_day", "attempt_hour",
        "passed", "completion_logged", "completion_lag", "expose",
    )
    draws = {}
    for persona in population:
        learner_id = persona.learner_id
        entry = {key: _u(seed, learner_id, key) for key in scalars}
        for key in ("deliver", "optout"):
            entry[key] = [_u(seed, learner_id, key, k) for k in range(21)]
        draws[learner_id] = entry
    return draws


SWEEP_PARAMETERS = ("effect_cap", "response_bias")


def _sign(value: float) -> int:
    return (value > 0) - (value < 0)


def run_sensitivity_sweep(
    population: list,
    param_ranges: dict,
    *,
    days: int = 14,
    seed: int = 0,
    policy: str = "streak_risk",
) -> list:
    """
    Tests policy across parameter ranges.

    One entry per element of the cartesian product of ``param_ranges`` (values in the order given; the supported
    parameters are ``effect_cap`` and ``response_bias``, anything else raises ValueError). Each entry is
    ``{"params": {...}, "effect": float, "status": "conclusive" | "inconclusive"}`` where ``effect`` is the mean
    change of the next-day return probability, perturbed minus baseline (the arms share one baseline: common
    random numbers). A result is "inconclusive" when its effect has the opposite sign to the effect at an ADJACENT
    value of any one parameter on the grid (the others fixed); a zero effect is not a sign (SPEC rule 5).
    """
    unknown = [name for name in param_ranges if name not in SWEEP_PARAMETERS]
    if unknown:
        raise ValueError(f"unsupported sweep parameter(s) {unknown}; supported: {list(SWEEP_PARAMETERS)}")
    names = list(param_ranges)
    axes = [list(param_ranges[name]) for name in names]
    if any(not axis for axis in axes):
        raise ValueError("every parameter range needs at least one value")

    baseline = [event for persona in population for event in generate_baseline_stream(persona, days, seed)]
    before = math.fsum(event["return_probability"] for event in baseline)

    shape = [len(axis) for axis in axes]
    total = 1
    for size in shape:
        total *= size

    def position(flat: int) -> tuple:
        index = []
        for size in reversed(shape):
            index.append(flat % size)
            flat //= size
        return tuple(reversed(index))

    def flat_index(index: tuple) -> int:
        flat = 0
        for i, size in zip(index, shape):
            flat = flat * size + i
        return flat

    effects = []
    for flat in range(total):
        params = {name: axes[k][i] for k, (name, i) in enumerate(zip(names, position(flat)))}
        perturbed = apply_notification_perturbation(
            baseline,
            policy,
            effect_cap=params.get("effect_cap", 0.05),
            response_bias=params.get("response_bias", 0.0),
        )
        after = math.fsum(event["return_probability"] for event in perturbed)
        effects.append(float((after - before) / len(baseline)) if baseline else 0.0)

    results = []
    for flat in range(total):
        index = position(flat)
        flips = False
        for axis, i in enumerate(index):
            for step in (-1, 1):
                j = i + step
                if 0 <= j < shape[axis]:
                    neighbour = index[:axis] + (j,) + index[axis + 1:]
                    if _sign(effects[flat]) * _sign(effects[flat_index(neighbour)]) < 0:
                        flips = True
        results.append(
            {
                "params": {name: axes[k][i] for k, (name, i) in enumerate(zip(names, index))},
                "effect": effects[flat],
                "status": "inconclusive" if flips else "conclusive",
            }
        )
    return results


def _canonical(value):
    """A copy with every dict's keys in sorted order, so the serialised form never depends on insertion order."""
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def export_event_stream(events: list, seed_version: str) -> list:
    """
    Exports events with seed versioning metadata.

    Returns a new list of dicts in input order: every field of each event (keys sorted at every level) plus
    ``seed_version``. The input is not changed. The same events and version always serialise to the same bytes.
    """
    if not isinstance(seed_version, str) or not seed_version:
        raise ValueError("seed_version must be a non-empty string")
    exported = []
    for event in events:
        if "seed_version" in event:
            raise ValueError("event already carries a seed_version")
        record = _canonical(event)
        record["seed_version"] = seed_version
        exported.append(_canonical(record))
    return exported
