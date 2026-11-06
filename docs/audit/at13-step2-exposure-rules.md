# AT-13 step 2 of 8: exposure on the Learner aggregate

Scope: exposure only. No ingestion, no docs. No git state was changed.

## Changes made
| File | Change |
|---|---|
| `src/experiments/exposure.py` (new) | `EXPOSURE_SURFACES = {"exp_007": {"variant_a": frozenset({"streak_risk_reminder"})}}`; `ExposureRefused(ValueError)` with `.reason`, `str(exc) == reason`. |
| `src/state_machine/learner.py` | State `experiment_exposures` (view_id -> view_id, exp_id, variant, exposure_surface, assignment_ref, exposed_at ISO), `first_exposure_view_ids` (exp_id -> view_id), `exposure_sources` (view_id -> source event_id). Public `record_experiment_exposure(...)`, private `@event("ExperimentExposed") _record_experiment_exposure` (pure fold), `_exposure_order`, public `first_experiment_exposure(exp_id)`. |
| `tests/experiments/test_experiment_exposure.py` (new) | 11 tests. |

## Before / after
- Before: assignment was stored (step 1); nothing recorded exposure.
- After: an assigned learner can record exposures. Decision (checks) lives in the public method; the event method only stores state and updates the earliest view by key `(exposed_at, view_id)`, the same pattern as `first_checkpoint_attempt_ids`. Every distinct `view_id` is recorded; "first" is derived, so arrival order does not matter.
- Check order: naive `exposed_at` -> ValueError; `view_id` already recorded -> False; no assignment -> `no_assignment`; no surfaces for exp/variant -> `no_treatment_surface_for_variant`; surface not allowed -> `not_treatment_surface`; `exposed_at < assigned_at` -> `exposure_before_assignment` (equal allowed).

## Tests added
Surface table; no exposure call -> zero events and `first_experiment_exposure` None; successful exposure (fields incl. `assignment_ref`); four refusals (state unchanged, no pending events, nothing saved); equal-to-assigned_at allowed; naive -> ValueError; duplicate view_id -> False without event; first exposure independent of arrival order plus tie-break on view_id; source_event_id recorded.

## Commands and real output
Red phase (`python -m pytest tests/experiments/test_experiment_exposure.py -q`):
```
E   ModuleNotFoundError: No module named 'src.experiments.exposure'
ERROR tests/experiments/test_experiment_exposure.py
1 error in 0.14s
```
First green attempt: `1 failed, 10 passed`. The failure was a bug in my test (two learners built from the same UUID, `IntegrityError: UNIQUE constraint failed: stored_events.originator_id, ...`). Fixed by letting the test helper return the n-th matching UUID. Implementation was not changed for it.

Final:
```
python -m pytest tests/experiments/test_experiment_exposure.py -v  -> 11 passed in 0.25s
python -m pytest -q                                               -> 787 passed, 2 skipped, 1 deselected in 46.25s
```
No existing test edited.

## Assumptions
- The naive-`exposed_at` check runs before the duplicate `view_id` check (consistent with step 1).
- The stored exposure record includes `view_id` so `first_experiment_exposure` returns a self-describing dict (a copy).
- `assigned_at` is read back from its ISO string with `_as_datetime`.
- The ValueError test for naive times is not an `ExposureRefused`; `ExposureRefused` is a ValueError subclass, so `pytest.raises(ValueError)` alone would not distinguish them.

## Unsure about
- Step 1 said "refusals leave no pending events": tests reload the learner first so pending is empty to begin with.
- No service wrapper for exposure yet (not requested).
- Tie-break on equal `exposed_at` uses string order of `view_id`.
