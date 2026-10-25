# Week 4 Fix 11a: Enforce the 72-hour late-event window

Status: **done. Not committed.**

- Week 3 review row 26: the 72-hour late-event policy was only descriptive.
  `ingest_lesson_completed` now rejects a delivery received **strictly more**
  than `late_window` after its `occurred_at`, with reason
  `outside_late_window`. The receive time comes from a trusted clock in
  `IngestConfig`, never from the envelope.
- `tests/ingestion/test_late_window.py` adds 14 tests, and **all 14 pass**.
- Suite excluding `tests/leaderboard` and `tests/notifications`: **260 passed,
  0 failed, 4 skipped**. After Fix 10 it was 246 passed, 4 skipped; the
  difference is the 14 new tests.
- Two existing assertions were extended, with approval, for the new
  `delivery_ids.received_at` column; see "Resolved". No other existing
  assertion was edited and no test was deleted.

## Problem

- Nothing compared a delivery's age with any limit. A completion that
  occurred weeks earlier was accepted and earned XP like a fresh one.
- The only receive-time value was the envelope's own `ingested_at`, which the
  producer controls.
- The evidence store stamped rows with its own `datetime.now()`.

## Change

### 1. `IngestConfig` (`src/ingestion/config.py`)

| Field | Default | Validation |
|---|---|---|
| `rule_version: str` | (required) | key of `RULESETS` (unchanged) |
| `late_window: timedelta \| None` | `DEFAULT_LATE_WINDOW = timedelta(hours=72)` | a positive `timedelta`, or `None` (unbounded). `0`, negative, `int` and `str` values raise `ValueError`. |
| `clock: Callable[[], datetime]` | `utc_now`, which returns `datetime.now(timezone.utc)` | must be callable |

- `DEFAULT_CONFIG = IngestConfig("v1")`, which has the 72 h window and the
  system clock.
- `utc_now` is a module-level function, so `DEFAULT_CONFIG == IngestConfig("v1")`
  still holds (asserted in `test_rule_selection.py`).

### 2. Enforcement (`src/ingestion/service.py`)

- **`:127`**: `received = config.clock()` is read **once**, at the start. A
  naive (timezone-less) result raises `ValueError`. Its ISO string is the
  receive time for everything this call writes.
- **`:220`**: after the `dedup_key` check and after the bound-`event_id`
  check (`duplicate_event_id` / `conflicting_event_id_reuse` return earlier),
  and **before** `repository.get`:

  ```python
  if config.late_window is not None:
      occurred = datetime.fromisoformat(str(envelope["occurred_at"]))
      if received - occurred > config.late_window:     # strictly greater
          evidence.record("rejected", learner_id, "outside_late_window",
                          action_id=action_id, event_id=event_id, received_at=received_at)
          return IngestionResult("rejected", "outside_late_window")
  ```

  The aggregate is not loaded and nothing is bound. Exactly 72 h is accepted.
- **Recognised reasons.** `"outside_late_window"` was added to the set of
  recognised rejection reasons. It is returned directly rather than raised.

Order of checks, unchanged except for the new step 5:

1. Schema, producer, reward-field and payload validation.
2. `dedup_key` mismatch.
3. Bound `event_id`: duplicate or conflicting reuse.
4. *(nothing new here)*
5. **Late window (new).**
6. Load the aggregate, then the crash-gap / completion dedup / accept.

### 3. The clock's time everywhere

- All 13 `evidence.record(...)` calls in `service.py` now pass
  `received_at=received_at`: the 12 existing calls (validation, dedup key,
  `_resolve_bound_event_id`, completion duplicates, the concurrency paths,
  the quest evidence) and the new late-window call.
  - `_resolve_bound_event_id` takes `received_at` as a parameter.
  - `EvidenceStore.record` keeps its own `datetime.now()` fallback for other
    callers.
- **`delivery_ids.received_at`.** New nullable column in a new group,
  `INGRESS_COLUMNS = {"received_at": "TEXT"}` (`evidence.py:41`), included in
  `DELIVERY_COLUMNS` (`:44`).
  - It is added with `ALTER TABLE` only when missing, inside Fix 10's
    `BEGIN IMMEDIATE` migration.
  - The accept-path `bind_delivery` stores the clock's time.
  - Old rows and the crash-gap / concurrency-retry backfills keep `NULL`.
  - `DeliveryBinding.received_at` exposes it.
  - `DeliveryProvenance` in `trace.py` was **not** changed.

## Existing tests: late window switched off

The existing tests use fixed `occurred_at` values in 2026-09. Against the real
clock (2026-10-25) the default window rejects them: 46 tests failed.

They are not about lateness, so they now ingest through one shared helper:

- **New `tests/ingestion/support.py`.** It provides a drop-in
  `ingest_lesson_completed(application, evidence, envelope, *, config=None, **kwargs)`.
  It calls the real service with
  `UNBOUNDED_CONFIG = replace(DEFAULT_CONFIG, late_window=None)` when no
  config is passed. When a config is passed (`IngestConfig("v2")`,
  `DEFAULT_CONFIG`), it uses `replace(config, late_window=None)`, so the rule
  version and clock are kept.
- **One import line per file.** In each file below, only the import changed,
  from `from src.ingestion.service import ingest_lesson_completed` to
  `from tests.ingestion.support import ingest_lesson_completed  # late_window=None`.
  Where `canonical_digest` shared the import line, it stays imported from the
  service.

Files touched for this (import line only; no test body or assertion edited):

| File |
|---|
| `tests/ingestion/support.py` (new helper) |
| `tests/ingestion/test_completed_quest_lesson.py` |
| `tests/ingestion/test_dedup.py` |
| `tests/ingestion/test_dedup_key.py` |
| `tests/ingestion/test_event_id_binding.py` |
| `tests/ingestion/test_ingestion_service.py` (also covers the spawn worker `_ingest_in_worker`) |
| `tests/ingestion/test_provenance.py` |
| `tests/ingestion/test_rule_digest.py` |
| `tests/ingestion/test_rule_selection.py` |
| `tests/ingestion/test_rule_version_change.py` |
| `tests/ingestion/test_trace.py` |
| `tests/state_machine/test_active_state_path.py` |

After the change, 45 of the 46 failures pass with their assertions untouched. The 46th failed for a different reason, the new column; see "Resolved".

## Resolved: column-list assertions extended (approved)

`tests/ingestion/test_provenance.py::test_pre_change_database_opens_keeps_rows_and_stores_new_provenance`
was the one test still failing after the import change. It did not fail
because of the late window. It asserts the exact `delivery_ids` column list,
and requirement 3 adds `received_at` to `delivery_ids`.

With approval, exactly two assertions in that test were extended, and nothing
else in the test was changed:

| Line | Before | After |
|---|---|---|
| `:271-284` (expected column list) | `[…, "rule_version", "rule_digest"]` (15 names) | `[…, "rule_version", "rule_digest", "received_at"]` (16 names) |
| `:323` | `assert len(_columns(db)) == 15` | `assert len(_columns(db)) == 16` |

This is the same kind of extension as in Fix 9b, where `rule_digest` took the
list from 14 to 15. After the edit, the full suite has 0 failures.

## Tests (`tests/ingestion/test_late_window.py`, 14)

They use `FixedClock`, a settable callable, with occurred_at
`2026-09-21T10:00:00+00:00`.

| Test | Clock | Result |
|---|---|---|
| `test_default_config_has_72_hour_window_and_system_clock` | — | `DEFAULT_CONFIG` is `"v1"`, 72 h, `clock is utc_now`; `utc_now()` is aware |
| `test_late_window_must_be_positive_or_none` (×4) | — | `timedelta(0)`, `-1 s`, `72` and `"72h"` raise `ValueError`; `None` is allowed |
| `test_naive_clock_is_refused` | naive datetime | `ValueError` ("timezone-aware") |
| `test_inside_or_on_the_window_is_accepted[71h59m]` | +71 h 59 m | accepted, `xp_total` 100, `delivery_ids.received_at` = the clock time |
| `test_inside_or_on_the_window_is_accepted[exactly-72h]` | +72 h | accepted, `xp_total` 100 |
| `test_one_second_past_the_window_is_rejected_without_state_change` | +72 h 1 s | `rejected` / `outside_late_window`; `xp_total` 0; no new events; exactly one evidence row `("rejected", "outside_late_window", "cmp-late", "…0b11", "2026-09-24T10:00:01+00:00")`; no `delivery_ids` row |
| `test_envelope_ingested_at_inside_window_does_not_rescue_a_late_delivery` | +73 h, `ingested_at` +1 min | rejected, `outside_late_window` |
| `test_envelope_ingested_at_outside_window_does_not_reject_a_timely_delivery` | +1 h, `ingested_at` +30 days | accepted; `received_at` `"2026-09-21T11:00:00+00:00"` |
| `test_every_evidence_row_uses_the_clock` | +2 h, wrong `dedup_key` | the rejected row's `received_at` is `"2026-09-21T12:00:00+00:00"` |
| `test_redelivery_of_accepted_event_after_window_is_still_a_duplicate` | accepted at +1 h, redelivered at +10 days | `duplicate` / `duplicate_event_id`; no new events; `xp_total` 100; the evidence row has the redelivery time `"2026-10-01T10:00:00+00:00"`; the binding keeps the acceptance time `"2026-09-21T11:00:00+00:00"` |
| `test_unbounded_window_accepts_any_delay` | +365 days, `late_window=None` | accepted, `xp_total` 100 |

## Before / after

| Delivery (occurred 2026-09-21T10:00Z) | Before | After (default config) |
|---|---|---|
| received +71 h 59 m | accepted | accepted |
| received +72 h | accepted | accepted |
| received +72 h 1 s | accepted, +100 XP | **rejected**, `outside_late_window`, no state change |
| received +73 h, envelope `ingested_at` +1 min | accepted | rejected (the envelope time is ignored) |
| redelivery of an accepted event at +10 days | `duplicate_event_id` | `duplicate_event_id` |
| evidence `received_at` | the store's wall clock | `IngestConfig.clock()` |
| `delivery_ids` columns | 15 | 16 (`+ received_at`) |
| suite (excluding leaderboard and notifications) | 246 passed, 4 skipped | 260 passed, 4 skipped |

## Known limits

- **Late resends of known completions.** The window applies before the
  aggregate is loaded, as specified. So a *new* `event_id` for an
  already-accepted `completion_id`, arriving after the window, is answered
  `outside_late_window`, not `duplicate_completion_id`. Either way it changes
  no state.
- **Crash-gap redeliveries.** The same applies to a crash-gap redelivery (an
  `event_id` accepted but never bound) after the window.
- **No future-dated check.** Events with `occurred_at` in the future relative
  to the clock pass the check, because the delay is negative. Nothing checks
  for future dates yet.
- **Not wired up.** The window and clock are not configured from outside yet.
  Production uses `DEFAULT_CONFIG`: 72 h and the system clock.
