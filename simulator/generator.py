"""
Synthetic Event Generator

This module provides the synthetic event generator for the simulator.
"""

import hashlib
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


def generate_baseline_stream(persona: PersonaConfig, days: int, seed: int) -> list:
    """
    Generates baseline activity events with no notification knowledge.
    """
    raise NotImplementedError


def apply_notification_perturbation(baseline_stream: list, policy: str, effect_cap: float = 0.05) -> list:
    """
    Applies bounded perturbation to baseline.
    """
    raise NotImplementedError


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


def run_sensitivity_sweep(population: list, param_ranges: dict) -> list:
    """
    Tests policy across parameter ranges.
    """
    raise NotImplementedError


def export_event_stream(events: list, seed_version: str) -> list:
    """
    Exports events with seed versioning metadata.
    """
    raise NotImplementedError
