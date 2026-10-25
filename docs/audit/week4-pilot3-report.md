# Week 4 pilot 3: intention-to-treat report for simulated runs

## Changes
- New `simulator/pilot_report.py`: `analyze(run)` (pure; stdlib + `mastery_report` with
  `as_of=DATA_CUTOFF_ISO`), `max_in_rolling_24h(timestamps)`, and one extra public helper `srm_check(a, b)`.
- New `tests/simulator/test_pilot_report.py` (7 tests).
- No other file edited.

## Before / after
- Before: `ModuleNotFoundError: No module named 'simulator.pilot_report'`.
- After: report dict with meta, arms (treatment=variant_a, control=variant_b, eligible learners only for
  mastery/exposure), contrast, srm.

## Commands and output
    .venv/bin/python -m pytest tests/simulator/test_pilot_report.py -q   (tests first)
    -> 1 error in 0.08s  (ModuleNotFoundError: simulator.pilot_report)
    .venv/bin/python -m pytest tests/simulator -q   (after implementation)
    -> 25 passed in 0.46s

## Assumptions
- `srm_check` was not named in the spec; I factored the SRM math into it so it can be tested with raw counts.
- Rolling window = [t, t+24h) starting at each sent-message timestamp (spec rule); this differs in form from the
  simulator's cap check (t-24h, t] but gives the same maximum.
- Delivery counts (messages_sent, suppressed_*, opt_outs, max_sent_in_24h) cover all assigned learners in the arm;
  exposure counts and mastery cover eligible learners only. In practice only eligible treatment learners get
  deliveries, so the two coincide.
- `conclusive` = CI excludes 0 (low > 0 or high < 0); None when rates are None.
- SRM with zero total gives chi2 0.

## Uncertainty
- Contrast test checks se/CI with math.isclose and ci95_high within 1e-3 of 0.2372.
- Mastery denominator for the contrast is the mature-learner denominator (immature excluded), per spec.
- No git commands that change state were run.
