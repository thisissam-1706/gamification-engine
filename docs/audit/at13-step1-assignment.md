# AT-13 step 1 of 8: experiment assignment as a durable event

Scope: assignment only. Exposure, ingestion and docs are untouched. No git state was changed.

## Changes made

| File | Change |
|---|---|
| `src/experiments/assignment.py` | Added `HASH_VERSION = "hash_v2"`. `assign()` and its return value unchanged. |
| `src/state_machine/learner.py` | Imported `HASH_VERSION, assign`. New state `experiment_assignments: dict[str, dict]`. New public `assign_experiment(exp_id, *, assigned_at, source_event_id=None, correlation_id=None, causation_id=None) -> bool`. New private `@event("ExperimentAssigned") _record_experiment_assignment` that only folds state. |
| `src/experiments/service.py` (new) | `assign_learner_to_experiment(application, learner_id, exp_id, assigned_at) -> bool`: loads, calls `assign_experiment`, saves only on True. |
| `tests/experiments/test_experiment_assignment_events.py` (new) | 6 tests (5 requirements; #2 parametrized over both variants). |

## Before / after

- Before: `assign()` was a pure function. Nothing was stored; a learner had no record of its variant.
- After: `learner.assign_experiment("exp_007", assigned_at=...)` decides the variant with `assign(str(self.id), exp_id)` and emits one `ExperimentAssigned` event. The public method decides; the private `@event` method only writes `{exp_id, variant, hash_version, propensity, assigned_at (ISO)}` into `experiment_assignments`, mirroring `record_checkpoint_attempt`.
- Order of checks: naive `assigned_at` raises `ValueError` first, then the idempotence check on `exp_id`. So a naive timestamp raises even for an already-assigned experiment.

## Tests added

1. one `ExperimentAssigned`, zero `ExperimentExposed` (variant_a learner)
2. stored variant/propensity equal `assign()`; `hash_version == "hash_v2"` (both variants)
3. second call with same `exp_id` and different `assigned_at` returns False, no new event, state unchanged (service and aggregate level)
4. naive `assigned_at` raises `ValueError`, nothing emitted (service and aggregate level)
5. fresh `LearnerApplication` on the same SQLite file shows identical `experiment_assignments`

UUIDs are found by looping `uuid.uuid5(uuid.NAMESPACE_URL, f"learner-{i}")` until `assign(...)` returns the wanted variant; none hardcoded.

## Commands run and real output

Red phase, before implementing (`python -m pytest tests/experiments/test_experiment_assignment_events.py -q`):

```
E   ImportError: cannot import name 'HASH_VERSION' from 'src.experiments.assignment'
ERROR tests/experiments/test_experiment_assignment_events.py
1 error in 0.24s
```

(Collection error, so no individual test ran red. Earlier the same run also exposed that `Learner(id=...)` raises `TypeError: Learner.__init__() got an unexpected keyword argument 'id'`; see assumptions.)

After implementing:

```
python -m pytest tests/experiments/test_experiment_assignment_events.py -v
... 6 passed in 0.24s
```

Full suite (`python -m pytest -q`):

```
776 passed, 2 skipped, 1 deselected in 42.47s
```

`git status --short` afterwards: `M src/experiments/assignment.py`, `M src/state_machine/learner.py`, `?? src/experiments/service.py`, `?? tests/experiments/test_experiment_assignment_events.py` (plus this doc). No existing test edited.

## Assumptions

- `Learner.__init__` takes no `id`, so tests build a learner with a chosen id via `Learner._create(Learner.Created, id=uuid)`, an eventsourcing hook (v9.5.5). That is a private-looking API.
- `ExperimentExposed` does not exist yet, so "zero exposed events" is checked by event class name.
- `assigned_at` is stored as `assigned_at.isoformat()`, so the offset is preserved, not normalised to UTC.
- `source_event_id`, `correlation_id`, `causation_id` are accepted and passed to the event but not folded into state (provenance only, as with `record_checkpoint_attempt`).

## Unsure about

- `src/state_machine/learner.py` now imports from `src.experiments.assignment`. No cycle today (assignment.py imports only stdlib), but state_machine now depends on experiments.
- Test 3's aggregate-level check is loosely written (`pending_events == [] or not any(ExperimentAssigned)`); it could be tightened to `== []`.
- Existing databases are unaffected (new event type, new state defaults to `{}`), but I did not test replay of pre-existing event logs beyond the full suite passing.
