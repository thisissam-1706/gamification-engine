# AT-13 step 6 of 8: AT-13 as a real acceptance test

No state-changing git command was run. No `src/` file was changed in this step.

## Changes made
| File | Change |
|---|---|
| `tests/experiments/support.py` (new) | Shared helpers: `uuid_for_variant` (uuid5 loop, nothing hardcoded), `open_persisted(tmp_path)` (SQLite `LearnerApplication` + `EvidenceStore`; calling it again opens fresh objects on the same files), `create_learner`, `clocked`, `surface_viewed_envelope`, `lesson_completed_envelope`, `ingest_surface_viewed`, `ingest_lesson_completed` (fixed trusted clock 5 min after `occurred_at`, default 72h window kept), `stored_event_names`, `exposure_state`. |
| `tests/experiments/test_assignment_vs_exposure.py` | Rewritten. Docstring label `(M4)` -> `(M7)`. `pytest.mark.skip` removed. `test_assignment_vs_exposure` (name kept) plus `test_variant_b_learner_viewing_the_surface_is_not_exposed`. |
| `tests/experiments/test_deterministic_assignment.py` | Only the skipped placeholder `test_exposure_is_logged_only_after_treatment_surface_view` was replaced by a real test of the same name. Other tests untouched (`git diff --stat` shows the file changed only in that block). |

## Before / after
- Before: both AT-13 tests were `pass` bodies, skipped. Suite: **809 passed, 2 skipped, 1 deselected**.
- After: **812 passed, 0 skipped, 1 deselected** (+2 formerly skipped now passing, +1 new variant_b test).

## Tests
`test_assignment_vs_exposure` on a persisted app, in order: (1) assign a variant_a learner via `assign_learner_to_experiment`; (2) exactly one `ExperimentAssigned`, zero `ExperimentExposed`, `first_experiment_exposure` None; (3) ingest one `lesson_completed` (accepted), still zero exposures; (4) ingest `surface_viewed` after assignment: accepted, exactly one `ExperimentExposed` with `variant_a`, `streak_risk_reminder`, `assignment_ref == "experiment_assignment:exp_007"`, exposed time >= assigned_at (and == the view's `occurred_at`), no evidence rows; (5) a fresh application on the same files gives identical assignment/exposure state and event counts.
`test_variant_b_learner_viewing_the_surface_is_not_exposed`: variant_b learner, assigned, views the surface: `rejected / no_treatment_surface_for_variant`, one `ExperimentAssigned`, zero `ExperimentExposed`, one matching rejected evidence row.
`test_exposure_is_logged_only_after_treatment_surface_view`: assignment (and a repeated assignment, returns False) leave zero exposures; a surface view then logs exactly one, ordered after `ExperimentAssigned`.

## Commands and real output
```
python -m pytest -q   (before this step)  -> 809 passed, 2 skipped, 1 deselected in 93.42s
python -m pytest tests/experiments/test_assignment_vs_exposure.py tests/experiments/test_deterministic_assignment.py -v
    11 passed in 0.37s
python -m pytest -q   (after)             -> 812 passed, 1 deselected in 92.07s (0:01:32)
```
(`1 deselected` = the `integration`-marked wheel-install test, excluded by `addopts`.) No test failed at any point in this step.

## Assumptions
- Ingestion tests use a fixed clock 5 minutes after the event, so the 72h late window is satisfied by the 2026-10 fixed timestamps.
- The test for the variant_b learner treats `rejected / no_treatment_surface_for_variant` as the specified "rejected".
- `Learner._create(..., id=...)` is used to get a learner id in the wanted variant (see step 1).

## Unsure about
- Tests 1 and 2 in the acceptance file are one long scenario by design (the spec's ordered steps); a failure points to a line, not a separate test.
- The step 5 ingestion test file still has its own copies of the helpers; I did not refactor it onto `support.py` (not requested).
- `tests/experiments/support.py` is imported as `tests.experiments.support`; this works because `tests/` and `tests/experiments/` have `__init__.py`.
