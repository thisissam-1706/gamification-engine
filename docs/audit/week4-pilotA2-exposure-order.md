# Week 4 pilot A2: exposure only counts if it precedes the would-be attempt

## Changes
- `simulator/pilot.py` (`simulate`)
  - Reminders go out on days 1..3 after assignment (`day = 1 + k // per_day`), still 09:00 + j hours.
  - The would-be `attempt_at` is computed for every learner from the draws, before and independent of exposure.
  - `before_attempt = exposed and first_delivered <= attempt_at`; this (not `exposed`) feeds p_attempt and the pass
    probability. Every exposure record gets `before_attempt`.
  - Module docstring limit rewritten (the "no check" limit is gone).
- `simulator/pilot_report.py`
  - Funnel: `exposed_before_attempt` = eligible exposure records with `before_attempt` True; new
    `exposed_after_attempt` = exposed - before. The old first_attempt-based count is removed (no cutoff dependence).
  - Ledger: `exposure_before_attempt` comes from the exposure record flag (None if not exposed); attempts and
    completions after `DATA_CUTOFF_ISO` are dropped before building rows (shown as attempted False / None,
    completed False / None). Bucket logic unchanged.
- `simulator/SPEC.md`: first Limits bullet replaced with the requested sentence.
- Tests: 4 new in `test_pilot_streams.py` (two crafted cases, null-vs-positive identity for non-effective learners,
  reminder schedule), 2 new in `test_pilot_report.py` (funnel vs ledger flags, ledger cutoff consistency).
  One existing assertion edited: `FUNNEL_KEYS` in `test_pilot_report.py` now includes `exposed_after_attempt`
  (forced by the new funnel key). `test_run_pilot.py` needed no change.

## Before / after
- Before: first reminder on assignment day; any exposure raised attempt and pass probability, even if it came after
  the attempt time. Seed 7 positive (n=4000) treatment: exposed 1158, exposed_before_attempt 875 of those with an attempt, 3 after.
- After: seed 7 positive, treatment: exposed 1158, exposed_before_attempt 1083, exposed_after_attempt 75
  (n=600: 176 / 165 / 11). Before + after == exposed because the flag is set for every exposure.

## Commands and output
    .venv/bin/python -m pytest tests/simulator -q   (tests first)
    -> 6 failed, 45 passed
       (crafted cases, schedule, null-vs-positive identity, funnel vs ledger flags, ledger cutoff)
    .venv/bin/python -m pytest tests/simulator -q   (after; one existing FUNNEL_KEYS assertion updated)
    -> 51 passed in 1.38s
    .venv/bin/python -m pytest -q   (whole suite)
    -> 421 passed, 4 skipped, 1 deselected in 10.61s

## Assumptions
- `first_delivered <= attempt_at` (equal counts as before).
- Crafted case (a): attempt_day 0.0 / hour 0.0 gives attempt on day 1 at 00:00, first reminder day 1 at 09:00, so
  p_attempt stays 0.45 < 0.47 and there is no attempt; case (b) attempt_day 0.5 gives day 7, p_attempt 0.50 > 0.47.
- The null-vs-positive test compares every learner except those whose exposure is effective.
- Ledger cutoff comparison uses the ISO strings directly (same fixed-width format).
- Funnel `attempted` / `completion_logged` still count only mature learners' records at or before the cutoff.

## Uncertainty
- The evidence under `docs/evidence/pilot/` was generated BEFORE this change and is now stale; it was not
  regenerated (not asked). Results in `week4-pilot5-results.md` do not reflect the new schedule or effective-exposure rule.
- Moving reminders to day 1+ changes cap_stress timing and all delivery counts relative to the earlier runs.
- No git commands that change state were run.
