# AT-18 step 1 of 5: acceptance test written first

## Changes made
- Added `tests/simulator/test_policy_comparison.py` (new file).
- Added this audit file.
- Nothing under `src/` or `simulator/` changed. No git commands that modify anything were run.

## Before / after
- Before: AT-18 ("Policy Comparison (M7)") had no test (`docs/traceability-matrix.md` line 91, "test to be written").
- After: 6 test functions (9 pytest items, because tests 2, 3 and 4 are parametrized over the treatment and control arms). 8 pass, 1 fails as predicted.

## Tests added
All use setting `positive`, seed 7, n=1500 unless stated. Runs are built in memory (`create_persona_population`, `generate_common_random_numbers`, `simulate`, `analyze`); no file I/O, no `run_pilot`.
1. `test_assignment_is_the_deterministic_hash`: every assignment equals `assign(learner_id, "exp_007")[0]`, propensity 0.5, both variants present, rebuilding gives identical assignments. PASS.
2. `test_primary_metric_matches_independent_recomputation` (both arms): rate recomputed in the test with no `mastery_report` call (mature = eligible and assigned_at + 14d <= cutoff; first attempt by (occurred_at, attempt_id) inside the window and <= cutoff; mastered = matching completion with `passed is True`, same assessment id/version, attempt <= completion <= assigned_at + 14d and <= cutoff). Compared with `report["arms"][arm]["mastery"]["rate"]`. PASS.
3. `test_missing_outcomes_count_as_not_mastered` (both arms): `missing_outcome > 0`, denominator == mature eligible count, rate == mastered/denominator, and mastered/(denominator - missing) is strictly higher and differs. PASS.
4. `test_immature_learners_are_excluded_and_reported` (both arms): `immature == eligible - mature` (also asserted > 0), denominator excludes them, `meta.notes` mentions "immature". PASS.
5. `test_fatigue_metrics_reported_for_both_arms`: keys `messages_per_learner_per_day`, `opt_outs`, `cap_breach_learners`, `messages_sent` on both arms; control 0 messages / 0 opt-outs / 0 breaches; treatment > 0 messages; treatment `messages_per_learner_per_day == messages_sent / (eligible * 14)`. **FAIL (expected)**: `messages_per_learner_per_day` is missing from the arm report.
6. `test_cap_stress_breach_is_reported_for_treatment_only`: `cap_stress`, n=1000: treatment breaches > 0, control == 0; `positive` n=1000: 0 breaches in both arms and `max_sent_in_24h <= CAP`. PASS.

## Commands run (real output)
```
$ python -m pytest tests/simulator/test_policy_comparison.py -v
collected 9 items

test_assignment_is_the_deterministic_hash PASSED
test_primary_metric_matches_independent_recomputation[treatment] PASSED
test_primary_metric_matches_independent_recomputation[control] PASSED
test_missing_outcomes_count_as_not_mastered[treatment] PASSED
test_missing_outcomes_count_as_not_mastered[control] PASSED
test_immature_learners_are_excluded_and_reported[treatment] PASSED
test_immature_learners_are_excluded_and_reported[control] PASSED
test_fatigue_metrics_reported_for_both_arms FAILED
test_cap_stress_breach_is_reported_for_treatment_only PASSED

E   AssertionError: treatment arm is missing messages_per_learner_per_day
FAILED tests/simulator/test_policy_comparison.py::test_fatigue_metrics_reported_for_both_arms
========================= 1 failed, 8 passed in 0.59s ==========================
```
(Output trimmed to the relevant lines; Python 3.14.6, pytest 9.1.1.) Only this file was run.

## Assumptions
- The AT-18 text was taken from `docs/traceability-matrix.md` line 91.
- Arm membership comes from `run.assignments` variant (`variant_a` = treatment, `variant_b` = control), matching `ARM_VARIANTS` in `pilot_report.py`.
- "Eligible" comes from `run.eligibility`; the independent recomputation applies the 14-day window and cutoff itself, ignoring conflicting/invalid-ordering cases (assumed absent in simulator output; the match with the report supports that).
- Tests 2-4 compare rates with `pytest.approx(abs=1e-12)` rather than `==` to avoid float-formatting noise.
- I used a module-scoped fixture for the n=1500 run to avoid rebuilding it per test; test 1 rebuilds it once more to check determinism.
- I added `immature > 0` and `0 < expected < 1` sanity asserts (beyond the brief) so the tests cannot pass vacuously.

## Unsure about
- Tests 2-4 are parametrized per arm, so the file reports 9 items, not 6.
- Test 5 requires `messages_per_learner_per_day` on the control arm too (key presence), which the brief asked for; its expected value (0.0) is not asserted. The denominator `eligible * 14` follows the brief literally.
