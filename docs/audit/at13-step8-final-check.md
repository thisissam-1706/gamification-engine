# AT-13 step 8 of 8: final verification

Verification only. Nothing in `src/`, `tests/` or the docs was changed in this step; all checks passed, so nothing needed fixing. No state-changing git command was run. Scratch files (a wheel and one script) were written to the session scratchpad, outside the repo.

## 1. Full suite
```
python -m pytest
================ 812 passed, 1 deselected in 131.06s (0:02:11) =================
```
Passed 812, skipped 0, failed 0, deselected 1 (the `integration` test, excluded by `addopts` in `pyproject.toml:39`; see check 2).
Baselines: `docs/audit/week6-m8-at15-wipe-fix.md` reports `770 passed, 2 skipped, 1 deselected`; the step 5 audit of this work reports 809 passed, 2 skipped (before step 6). Net since the M8 baseline: +42 passed, -2 skipped (the two exposure placeholders now pass; the rest is the new AT-13 tests and tests added by other work between those audits).

## 2. Integration marker and wheel contents
```
python -m pytest -m integration -v
tests/packaging/test_wheel_install.py::test_installed_wheel_ingests_outside_checkout PASSED
1 passed, 812 deselected in 13.09s
```
Built a wheel (`python -m pip wheel . --no-deps --no-build-isolation -w <scratchpad>/wheel`) and listed it with `zipfile`:
```
src/experiments/exposure.py
src/experiments/service.py
src/schemas/events/surface_viewed.schema.json
```
So the new schema and the new modules are in the wheel. (`tests/packaging/test_wheel_contents.py`, which now requires the schema, is in the default run and passed in check 1.)

## 3. Skips
```
grep -rn 'mark.skip\|pytest.skip\|skipif\|importorskip' tests --include='*.py'   -> no matches
grep -rniE 'skip.*exposure|exposure.*skip' tests --include='*.py'                -> no matches
python -m pytest -rs -q | grep -i skip                                         -> no output
```
No skip markers remain in `tests/`, none mention exposure, and the run reports 0 skipped.

## 4. Existing ingestion behaviour unchanged
```
python -m pytest tests/ingestion tests/interventions/test_checkpoint_attempt_ingestion.py tests/interventions/test_learning_remediation.py -q
120 passed in 12.67s
```
(`tests/ingestion` holds the lesson-completion ingestion tests; 117 of the 120 are in `tests/ingestion` plus the checkpoint ingestion file; the other 3 are the AT-17 remediation tests.) None of these files was edited at any point in AT-13.
Code check: `git diff -U0 src/ingestion/service.py` shows exactly two removed lines, the two `accepted = spec.accept(...)` calls, each now wrapped in `try/except ExposureRefused`. Only `ExposureRefused` is caught, and only the surface-view path raises it.

## 5. Rejected view for an unknown learner leaves no row
Script (scratchpad, `unknown_learner.py`): open a fresh SQLite event store plus evidence store, count rows in every table, ingest a valid `surface_viewed` envelope for a random UUID that has no aggregate, count again. Output (run twice, with and without `-I`):
```
raised RuntimeError learner aggregate must exist before ingestion
before {'accepted_deliveries': 0, 'delivery_ids': 0, 'ingestion_evidence': 0, 'stored_events': 0}
after  {'accepted_deliveries': 0, 'delivery_ids': 0, 'ingestion_evidence': 0, 'stored_events': 0}
unchanged: True
```
No new row in `stored_events` or in any evidence or delivery table. Note the caller gets a `RuntimeError`, not a `rejected` result (same as before AT-13; see "Unsure about"). This is also asserted by `test_unknown_learner_creates_and_saves_nothing`.

## 6. Refusal reason names, code vs docs
| Reason | In `src/` | In docs |
|---|---|---|
| `no_assignment` | `src/state_machine/learner.py` (`ExposureRefused("no_assignment")`, line 440) | `docs/event-contracts.md`, `docs/traceability-matrix.md` |
| `no_treatment_surface_for_variant` | `learner.py:444` | both |
| `not_treatment_surface` | `learner.py:446` | both |
| `exposure_before_assignment` | `learner.py:448` | both |
| `duplicate_view_id` (duplicate reason) | `src/ingestion/service.py:143` | `docs/event-contracts.md` |

The four `ExposureRefused` raises in `src/` are exactly these four, and every one appears in both docs. `duplicate_view_id` is in `event-contracts.md` only; the matrix clarification does not discuss duplicates.

## 7. Files changed or created during AT-13 (read-only `git status`, `git diff --stat`)
Modified (11 files, 478 insertions, 15 deletions):
- `README.md`, `docs/event-contracts.md`, `docs/metrics-framework.md`, `docs/traceability-matrix.md`
- `src/experiments/assignment.py`, `src/ingestion/service.py`, `src/ingestion/validation.py`, `src/state_machine/learner.py`
- `tests/experiments/test_assignment_vs_exposure.py`, `tests/experiments/test_deterministic_assignment.py`, `tests/packaging/test_wheel_contents.py`

Created (untracked):
- `src/experiments/exposure.py`, `src/experiments/service.py`, `src/schemas/events/surface_viewed.schema.json`
- `tests/experiments/support.py`, `tests/experiments/test_experiment_assignment_events.py`, `tests/experiments/test_experiment_exposure.py`, `tests/experiments/test_experiment_exposure_edge_cases.py`, `tests/ingestion/test_surface_viewed_ingestion.py`
- `docs/audit/at13-step1-assignment.md` ... `at13-step8-final-check.md` (8 audit files)

Existing tests edited: three. `test_assignment_vs_exposure.py` and `test_deterministic_assignment.py` (the two skipped placeholders, as instructed in step 6) and `test_wheel_contents.py` (one line in `REQUIRED`, as approved in step 5).
The `build/` directory created by `pip wheel` already existed (dated Oct 12) and is git-ignored (`.gitignore:13`); it does not appear in `git status`.

## Assumptions
- "Baseline" is taken from the audit files named in the task plus the counts recorded in this series; counts between the M8 audit and AT-13 also include other work done in between, which I did not itemise.
- Check 5 treats "no new row in the events database" as no new row in any table of the SQLite file, which also holds the evidence tables.

## Unsure about
- An unknown learner raises `RuntimeError` instead of producing a `rejected` result with an evidence row. It leaves no rows, so check 5 passes, but "a rejected surface_viewed" is not literally what the caller sees. It is the long-standing behaviour for all three event types, and I did not change it; it needs your decision.
- Stale docs flagged in step 7 remain: the M1 rows (matrix and README) still list only two ingested event types.
- The two payload schemas `experiment_assigned.schema.json` and `experiment_exposed.schema.json` still do not match the stored events (step 7 report); no code reads them.
- The proposed AT-13 rewording (step 7 audit) is still waiting for your approval.
