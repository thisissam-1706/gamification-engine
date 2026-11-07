# AT-18 step 6: notes-count assertion

## Changes made
- `tests/simulator/test_pilot_report.py` line 139, in `test_null_run_control_untouched_and_analyze_is_pure`: expected meta notes count changed from 3 to 4. Nothing else in that file or any other file changed (apart from creating this audit). No state-changing git commands were run.

## Before / after
```diff
-    assert len(first["meta"]["notes"]) == 3
+    assert len(first["meta"]["notes"]) == 4
```
Reason: step 2 added one meta note (messages per learner per day), as requested.

## Commands run (real output)
```
$ python -m pytest tests/simulator/test_pilot_report.py::test_null_run_control_untouched_and_analyze_is_pure -q
1 passed in 0.07s

$ python -m pytest tests/simulator -q
67 passed in 7.80s

$ python -m pytest -q
828 passed, 1 deselected in 85.77s (0:01:25)

$ git status --short
 M README.md
 M docs/metrics-framework.md
 M docs/traceability-matrix.md
 M simulator/pilot_report.py
 M tests/simulator/test_pilot_report.py
?? docs/audit/at18-step1-tests.md ... at18-step5-final-check.md
?? tests/simulator/test_policy_comparison.py
```
Result: 828 passed, 0 failed, 0 skipped (none reported), 1 deselected (the `integration` marker from `pyproject.toml`). Matches your expectation.

## Assumptions
- Edited with a line-139-only `sed` substitution; the diff above confirms a one-line change.

## Unsure about
- Nothing outstanding. `docs/audit/at18-step5-final-check.md` still describes the failure that this step resolves; I did not edit it (history).
