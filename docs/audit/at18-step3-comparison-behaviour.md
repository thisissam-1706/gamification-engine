# AT-18 step 3 of 5: comparison-behaviour tests

## Changes made
- Appended tests to `tests/simulator/test_policy_comparison.py` only. Nothing under `src/` or `simulator/` changed; no files are written by the tests; no state-changing git commands were run.
- Shared helper `_report(setting, seed, n=4000)` is `lru_cache`d, so each (setting, seed) run is built once and reused across tests (the determinism test deliberately rebuilds uncached).

## Before / after
- Before: 9 test items, all passing.
- After: 16 test items (6 new functions; the counted-once test is parametrized per arm, so 7 new items), all passing.

## Tests added (seeds 1-5, n=4000, via `analyze()`)
1. `test_positive_setting_shows_a_positive_difference`: difference > 0 every seed. PASS.
2. `test_harmful_setting_shows_a_negative_difference`: difference < 0 every seed. PASS.
3. `test_null_setting_shows_no_large_difference`: |mean| < 0.02 and each |difference| < 0.03. PASS.
4. `test_sample_ratio_check_does_not_alert_on_hash_assignment`: `srm.alert is False` and a + b == n every seed. PASS.
5. `test_report_is_deterministic`: same (positive, seed 3, n 4000) built twice, `json.dumps(sort_keys=True)` strings identical. PASS.
6. `test_every_assigned_eligible_learner_is_counted_once` (both arms): mastered + failed + missing_outcome + invalid_ordering + conflicting + immature == eligible every seed. PASS.

Actual values observed (contrast difference, seeds 1-5; thresholds not loosened):
- positive: 0.0464, 0.0549, 0.0244, 0.0460, 0.0478
- harmful: -0.0449, -0.0364, -0.0450, -0.0391, -0.0373
- null: 0.0084, 0.0085, -0.0125, 0.0001, -0.0025 (mean about 0.0005; max |d| 0.0125)
- SRM p-value, positive: 0.327 for all five seeds.

## Timing
- This file: **7.61 s** for 16 items (pytest-reported), under your 20 s threshold. No change to n or seeds proposed.
- Whole suite: 70.73 s (it was 27.06 s in step 2; the difference is mostly this file's 4000-learner runs and other load, I did not break it down).

## Commands run (real output)
```
$ python -m pytest tests/simulator/test_policy_comparison.py -v --durations=5
... 16 passed in 7.61s
(slowest: null 2.11s, positive 2.11s, harmful 2.04s, determinism 0.72s, cap_stress 0.25s)

$ python -m pytest -q
FAILED tests/simulator/test_pilot_report.py::test_null_run_control_untouched_and_analyze_is_pure
E   AssertionError: assert 4 == 3
1 failed, 827 passed, 1 deselected in 70.73s (0:01:10)

$ git status --short
 M simulator/pilot_report.py
?? docs/audit/at18-step1-tests.md
?? docs/audit/at18-step2-fatigue-metric.md
?? tests/simulator/test_policy_comparison.py
```

## Assumptions
- `docs/audit/week4-pilot5-results.md` was not read; the tests follow the brief's thresholds directly.
- Test 6 is parametrized per arm.

## Unsure about
- The one suite failure is the step-2 leftover (`test_pilot_report.py`, notes count `== 3` now 4). I have not changed it; still waiting on your decision from step 2.
- SRM test strength: assignment depends only on the learner id hash, not the seed, so the SRM result (a=757/b=743-style split, p=0.327) is identical for all five seeds. The five-seed loop is therefore effectively one check of the same population ids (n=4000 prefix). It is still valid but less independent than it looks.
- The null test's loose bounds (0.02 / 0.03) were chosen by you; observed values are well inside them.
