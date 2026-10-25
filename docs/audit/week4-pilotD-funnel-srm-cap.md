# Week 4 pilot D: funnel, SRM metadata, cap checks, decision independence

## Changes
- `simulator/pilot_report.py`: `SRM_ALPHA = 0.001`; `srm_check` now returns `test` ("chi_square_1df"), `alpha`,
  `expected_split` ("50/50") and alerts on `p < SRM_ALPHA`; new `_funnel(...)` and per-arm `funnel` dict (15 keys);
  new top-level `srm_eligible` computed on eligible counts per variant.
- `tests/simulator/test_pilot_report.py`: 6 new test cases (funnel invariants, 3 parametrized enforced-cap settings,
  enforced cap_stress, SRM metadata + exp_007.yaml text check).
- `tests/simulator/test_pilot_streams.py`: 1 new test (decisions identical across settings and independent of
  non-decision draws).
- `experiments/exp_007.yaml` was only read (plain text), already contains `alert_threshold: "p < 0.001"`.

## Before / after
- Before: no funnel, no SRM metadata, no `srm_eligible`.
- After: see funnel printed below.

## Commands and output
    .venv/bin/python -m pytest tests/simulator -q   (tests first)
    -> 2 failed, 39 passed   (funnel KeyError; srm_eligible KeyError)
       The cap-enforcement and decision-independence tests already passed: they describe existing behaviour.
    .venv/bin/python -m pytest tests/simulator -q   (after)
    -> 41 passed in 1.08s
Funnel, null setting, n=600, seed 7:
    treatment: assigned 318, excluded_tenure 26, excluded_consent 31, eligible 261, decided_send_reminders 261,
      decided_no_action 0, any_message_sent 261, any_message_delivered 261, exposed 176, exposed_before_attempt 125,
      mature 225, immature 36, attempted 157, completion_logged 150, mastered 80
    control: assigned 282, excluded_tenure 23, excluded_consent 25, eligible 234, decided_send_reminders 0,
      decided_no_action 234, any_message_sent 0, any_message_delivered 0, exposed 0, exposed_before_attempt 0,
      mature 187, immature 47, attempted 125, completion_logged 118, mastered 65
SRM: assigned 318/282 chi2 2.16 p 0.142; eligible 261/234 chi2 1.47 p 0.225; no alert.

## Assumptions
- exposed_before_attempt: exposed eligible learners with an attempt (at or before cutoff) whose first exposure is
  strictly earlier than their first attempt. Exposed learners with no attempt are not counted.
- attempted = mature eligible learners with an attempt at or before the cutoff; completion_logged = those with a
  completion record at or before the cutoff; mastered = mastery_report.mastered.
- any_message_sent counts delivered or provider_failed; suppressed-only learners do not count.
- Test also asserts decided_send_reminders + decided_no_action == eligible and attempted >= completion_logged >=
  mastered (extra to the spec).

## Uncertainty
- Funnel "mature" uses assigned_at + 14 days <= cutoff, matching the buckets and mastery_report.
- No git commands that change state were run.
