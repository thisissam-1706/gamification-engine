# Simulator Specification

**Status:** implemented for synthetic data (pilot v1: population, common random numbers, pipeline streams, analysis; generator functions: baseline stream, bounded perturbation, sensitivity sweep, event export). Eligibility, decision and delivery are stand-ins (see Limits).

### Generator functions
The four functions in `simulator/generator.py` that implement the Bias-Avoidance Rules below:

- `generate_baseline_stream(persona, days, seed)` returns a list of event dicts with `learner_id`, `day` (0 to `days` - 1) and `return_probability` (the next-day return probability). Day 0 is always an event; later days are events with probability
  `min(1, session_frequency / 7)`; a daily dropout chance of `dropout_probability / 7` ends the stream. `return_probability` is `0.5 * (1 - dropout_probability) + 0.5 * that session probability`, plus noise between -0.05 and +0.05, kept in [0, 1].
  Every draw is keyed by `(seed, learner_id, purpose, day)`. The function takes no policy and uses no notification code or data.
- `apply_notification_perturbation(baseline_stream, policy, effect_cap=0.05, *, response_bias=0.0)` returns a new list of the same length and order with the same keys; the baseline is not changed. Each event's `return_probability` moves by the learner's response
  to the policy (a draw between 0 and 0.01 keyed by policy, learner and day) plus `response_bias`, limited to between `-effect_cap` and `+effect_cap`, and stays in [0, 1], so no event moves by more than `effect_cap`.
  `response_bias` is a signed shift added to the learners' response before that limit; the learners' average response is about +0.005, so a sweep over `response_bias` values on both sides of that point can change the sign of the effect.
- `run_sensitivity_sweep(population, param_ranges, *, days=14, seed=0, policy="streak_risk")` runs every combination of the given values (the supported parameters are `effect_cap` and `response_bias`; any other name raises `ValueError`) on one shared baseline.
  Each entry is `{"params": {...}, "effect": float, "status": "conclusive" or "inconclusive"}`, where `effect` is the mean change in `return_probability` over all events, perturbed minus baseline. A result is `inconclusive` when its effect has the strictly opposite sign
  to the effect at an adjacent value of one parameter on the grid, with the other parameters held fixed. A zero effect has no sign.
- `export_event_stream(events, seed_version)` returns a new list of dicts in the input order: each event's fields (dict keys sorted at every level) plus `seed_version`. The input is not changed. `seed_version` must be a non-empty string, and an event that already has a
  `seed_version` raises `ValueError`. The key order does not depend on insertion order, so the same events and version always serialise to the same bytes.

### Limits
- Exposure only affects an attempt if it precedes the learner's would-be attempt time (drawn independently of exposure).
- Eligibility, decision and delivery are simulator stand-ins for M4/M5.
- Synthetic data only; results do not demonstrate real-world causality.

## Personas
The four learner personas from Section 2.2.1 are:

- **Consistent**: Regular daily activity pattern
- **Binge-then-lapse**: Intense bursts followed by inactivity periods
- **Weekend-only**: Activity concentrated on weekends
- **At-risk**: Low engagement, high dropout probability

Each persona has independently sampled parameters: session frequency, mastery growth, baseline dropout probability.

## Bias-Avoidance Rules
Bias-avoidance rules from Section 2.2.1:

1. **Independent Persona Sampling**: Parameters sampled before any policy selection
2. **Baseline-First Generation**: Baseline activity stream generated first from fixed random seed with zero knowledge of notifications
3. **Bounded Perturbation**: Notifications apply capped effect (≤ ±3–5%) on next-day return probability
4. **Common Random Numbers**: Treatment and control arms share same underlying persona population and baseline draws
5. **Sensitivity Sweeps**: Policy decisions tested across parameter ranges; results that flip under small adjustments marked inconclusive
6. **Seed Versioning (Reproducibility & Epistemic Limit)**: Parameters and seeds versioned with event log for byte-for-byte re-runs; simulations validate system mechanisms rather than real-world educational causality
