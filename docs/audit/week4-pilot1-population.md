# Week 4 pilot 1: deterministic persona population and common random numbers

## Changes
- `simulator/generator.py`
  - `PersonaConfig` gains `learner_id: str = ""` and `base_pass_probability: float = 0.5`.
  - New `_u(seed, learner_id, purpose, index=0)`: sha256 of `f"{seed}|{learner_id}|{purpose}|{index}"`,
    first 8 bytes big-endian, divided by 2**64. Stateless, no `random`.
  - `create_persona_population(size, seed)` implemented as specified (ids `L{i:05d}`, persona thresholds
    0.30/0.55/0.80, base values, +/-0.05 jitter on dropout and base_pass, clamp [0.01, 0.99], `seed` stored).
    Comment notes session_frequency and mastery_growth are unused by the pilot outcome model.
  - `generate_common_random_numbers(population, seed)` implemented: 10 scalar keys plus "deliver" and "optout"
    (21 values each, index k).
  - Unchanged and still `NotImplementedError`: `generate_baseline_stream`, `apply_notification_perturbation`,
    `run_sensitivity_sweep`, `export_event_stream`.
- New `tests/simulator/__init__.py` (empty) and `tests/simulator/test_population.py` (9 tests).
- `simulator/__init__.py` was NOT needed: `from simulator.generator import ...` imports fine from tests
  (namespace package, repo root on sys.path via `tests/__init__.py` rootdir handling).

## Before / after
- Before: `create_persona_population` and `generate_common_random_numbers` raised NotImplementedError.
- After: both deterministic; a population of size N is a prefix of any larger one; CRN for a learner is
  independent of population size.

## Commands and output
    .venv/bin/python -m pytest tests/simulator -q      (tests written first, before implementation)
    -> 9 failed in 0.04s   (all NotImplementedError from the two stubs; none were ImportError)
    .venv/bin/python -m pytest tests/simulator -q      (after implementation)
    -> 9 passed in 0.08s

## Assumptions
- Jitter is `(u - 0.5) * 0.10`, i.e. +/-0.05, as specified.
- Persona thresholds use strict `<`.
- The "still NotImplementedError" test builds a population first, so it also failed in step 1 (stub), not only
  because of the other functions.

## Uncertainty
- Share test uses seed 11, size 4000, tolerance 0.03; it passed. Other seeds not swept.
- No git commands that change state were run.
