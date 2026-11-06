# AT-13 step 3 of 8: exposure edge cases

Scope: tests only. `src/` was not changed in this step. No git state was changed.

## Changes made
- New `tests/experiments/test_experiment_exposure_edge_cases.py` (7 test cases from 6 requirements; the tie test is parametrized over both arrival orders, and requirement 4 is asserted inside the replay test plus a small no-source test).
- No edits to `src/` or to existing tests.

## Before / after
- Before: step 2 had implemented exposure; edge cases were only partly covered (one order test, one tie-break check, no replay across applications, no isolation test).
- After: all six requested edge cases are covered and **all passed on the first run against the unchanged step 2 code**, so nothing in `src/` needed fixing.

## Tests added
1. Order independence: views at t+2h then t+1h vs. the reverse on a second learner; both give `VEARLY` with the same record.
2. Tie on equal `exposed_at`: `VA` first in either arrival order.
3. Replay: save, open a fresh `LearnerApplication` on the same SQLite file; `experiment_assignments`, `experiment_exposures`, `first_exposure_view_ids` (and sources) equal the live values.
4. `exposure_sources` filled with `source_event_id` and equal after reload; absent entry when no source id is given.
5. Exposure refused with `no_assignment` leaves no trace; assigning afterwards does not accept it (no `ExperimentExposed` stored, `first_experiment_exposure` None); the same `view_id` can then be recorded normally.
6. Two experiments on one learner: a made-up `exp_test_other` added to `EXPOSURE_SURFACES` via `monkeypatch.setitem` (restored automatically). Each experiment has its own first exposure, variant, `assignment_ref`; surfaces and assigned_at are checked per experiment.

## Commands and real output
```
python -m pytest tests/experiments/test_experiment_exposure_edge_cases.py -v
... 7 passed in 0.18s
python -m pytest -q
794 passed, 2 skipped, 1 deselected in 46.56s
```
No failing run occurred, so there is no "before fix" failure output to show.

## Assumptions
- `monkeypatch.setitem` on the dict works because `learner.py` imports the same dict object (`EXPOSURE_SURFACES`), not a copy.
- The made-up experiment's variant comes from the real `assign()`; the test patches surfaces for whatever variant the learner receives.
- "No trace" is checked by state, pending events and stored events, plus the ability to reuse the `view_id`.

## Unsure about
- Because nothing failed, these tests did not demonstrate that they would catch a regression; I did not run mutation checks (for example breaking the tie-break) to prove that.
- `Learner._create(...)` is still used to choose ids (see step 1).
