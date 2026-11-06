# AT-13 step 5 of 8: `learning.surface_viewed` ingestion

Implements the design in `docs/audit/at13-step4-ingestion-design.md`. No docs outside `docs/audit/` changed. No state-changing git command was run (the design diff was applied with plain `patch -p1`, which is not git).

## Changes made
| File | Change |
|---|---|
| `src/schemas/events/surface_viewed.schema.json` (new) | Draft-07; required `view_id`, `exp_id`, `exposure_surface`, `timezone`, all `minLength 1`; `additionalProperties: false` (so `variant` / `assignment_ref` are rejected). |
| `src/ingestion/validation.py` | `"learning.surface_viewed": {1}` in `SUPPORTED_SCHEMA_VERSIONS`; added to `ALLOWED_EVENT_TYPES["learning-svc"]`; cached `surface_viewed_validator()` and `validate_surface_viewed_payload()` (checkpoint pattern). |
| `src/ingestion/service.py` | `_accept_surface_viewed`, `SURFACE_VIEWED` `_EventSpec` (`id_field="view_id"`, `action_prefix="surface_view:"`, `duplicate_reason="duplicate_view_id"`, `sources=learner.exposure_sources`, `is_accepted` = `view_id in experiment_exposures`), `ingest_surface_viewed(...)`, `_record_refusal` helper, and `except ExposureRefused` around both `spec.accept` call sites (first call and the post-`IntegrityError` retry). Imports `ExposureRefused`. |
| `tests/ingestion/test_surface_viewed_ingestion.py` (new) | 15 test cases. |
| `tests/packaging/test_wheel_contents.py` | **Only existing test changed**: added `src/schemas/events/surface_viewed.schema.json` to `REQUIRED`. |

Existing-test search (step 4, Q5): no other test enumerates schema files or event types, so no other test was touched.

## Before / after
- Before: `learning.surface_viewed` from `learning-svc` was rejected `untrusted_producer`; exposure could only be recorded by calling the aggregate directly.
- After: `ingest_surface_viewed` validates, then calls `Learner.record_experiment_exposure(exp_id, surface, view_id=..., exposed_at=occurred_at, source_event_id=...)`. `variant` and `assignment_ref` are derived by the aggregate. A rule refusal becomes `rejected` with `reason = exc.reason` and one evidence row; nothing is saved and the `event_id` is left unbound. Accepted/duplicate behaviour reuses the shared pipeline.
- `lesson_completed` and `checkpoint_attempted`: unchanged code path; neither can raise `ExposureRefused`, and only that class is caught (not `ValueError`).

## Tests added (test_surface_viewed_ingestion.py)
1. valid view accepted; one `ExperimentExposed`; variant `variant_a`, `assignment_ref` `experiment_assignment:exp_007`; `exposure_sources` holds the event id; no evidence rows.
2. same envelope again -> `duplicate`; same `view_id` under a new `event_id` -> `duplicate / duplicate_view_id`; still one event.
3. learner with no assignment -> `rejected / no_assignment`; event count and aggregate version unchanged.
3b. unknown learner (valid UUID, no aggregate) -> `RuntimeError` (existing behaviour, step 4 Q3); `repository.get` still raises `AggregateNotFoundError`; no events.
4. `variant_b` -> `no_treatment_surface_for_variant`. 5. `home_banner` -> `not_treatment_surface`.
6. payload with `variant` or with `assignment_ref` -> `schema_invalid` (2 cases).
7. producer `assessment-svc`, producer `gamification-engine`, event_type `learning.lesson_completed`, event_type `learning.unknown_event` -> `untrusted_producer` (4 cases); plus spoofed authenticated producer.
8. 72h + 1 min old -> `outside_late_window`. 9. `occurred_at` before `assigned_at` -> `exposure_before_assignment`.
Every rejection asserts exactly one `rejected` evidence row with that reason and no `ExperimentExposed` event. The clock is a fixed trusted clock 5 minutes after `occurred_at` (default 72h window kept, so the late window is only the subject of test 8).

## Commands and real output
Red phase (`python -m pytest tests/ingestion/test_surface_viewed_ingestion.py -q`):
```
E   ImportError: cannot import name 'ingest_surface_viewed' from 'src.ingestion.service'
15 failed in 0.88s
```
Implementation: `patch -p1 < <design diff>` printed `patching file src/ingestion/validation.py` / `patching file src/ingestion/service.py`; schema file written.
```
python -m pytest tests/ingestion/test_surface_viewed_ingestion.py -v   -> 15 passed in 0.86s
python -m pytest tests/packaging -q                                    -> 1 passed, 1 deselected in 1.33s
python -m pytest tests/packaging/test_wheel_install.py -m integration -q -> 1 passed in 7.21s
python -m pytest -q                                                    -> 809 passed, 2 skipped, 1 deselected in 110.62s (0:01:50)
```
(`1 deselected` is the `integration`-marked wheel-install test, excluded by `addopts` in `pyproject.toml:39`; I ran it explicitly above to confirm the wheel with the new modules installs and ingests.)

## Assumptions
- The design's `view_id` is unique per learner across experiments (aggregate keys by `view_id` alone).
- `exposed_at` = envelope `occurred_at`.
- Test 3's "no learner aggregate created or saved" is read as: the existing learner gets no new event/version, and (3b) an unknown learner is not created. For the unknown learner the caller sees `RuntimeError`, not `rejected` (design Q3, not changed).
- Test 2's literal "same envelope again" returns `duplicate / duplicate_event_id` (event_id already bound); the `duplicate_view_id` reason needs a new `event_id`, so both are asserted.

## Unsure about
- Unknown-learner behaviour is still an exception, awaiting your decision.
- A refused event is not bound, so redelivery re-evaluates it and adds another `rejected` evidence row each time.
- The shared validator still requires `timezone`, which the aggregate ignores.
- Late exposures (>72h) are dropped rather than recorded.
