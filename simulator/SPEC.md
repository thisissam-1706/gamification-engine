# Simulator Specification

**Status:** partially implemented (pilot v1: population, common random numbers, pipeline streams, analysis)

### Not implemented
- `run_sensitivity_sweep`
- `apply_notification_perturbation`
- `generate_baseline_stream`
- `export_event_stream`

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
