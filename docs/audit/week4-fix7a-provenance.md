# Week 4 Fix 7a: Retain source provenance

Status: **done. Not committed.**

- Week 3 review row 26 (third part): source `correlation_id` / `causation_id`
  and the canonical envelope were not retained. They are now kept on the
  `delivery_ids` row and on the stored `LessonCompleted` event.
- `tests/ingestion/test_provenance.py` adds 6 tests, and **all 6 pass**.
- Suite excluding `tests/leaderboard` and `tests/notifications`: **197 passed,
  0 failed, 4 skipped**. After Fix 6b it was 191 passed, 4 skipped; the
  difference is the 6 new tests.
- No existing test assertion changed. One existing test stub needed a
  signature change; see "Existing test touched".
- This builds on the uncommitted Fix 6a and 6b working tree.

## Problem

After an accepted delivery, the system kept only:

- `delivery_ids`: `event_id, learner_id, action_id, digest` (Fix 6a);
- `LessonCompleted`: `completion_id, lesson_id, activity_date, timezone_name,
  occurred_at, source_event_id` (plus `timestamp`).

The envelope's `producer`, `event_type`, `schema_version`, `ingested_at`,
`correlation_id`, `causation_id`, `dedup_key` and `provenance` were dropped
after validation, so nothing could trace an XP award back to the source
request chain. `digest` proves only that two deliveries had the same content;
the content itself cannot be recovered from it.

## Change

### 1. Evidence store (`src/ingestion/evidence.py`)

- `PROVENANCE_COLUMNS` (`evidence.py:24`) lists 10 nullable columns:
  `producer TEXT`, `event_type TEXT`, `schema_version INTEGER`,
  `occurred_at TEXT`, `ingested_at TEXT`, `correlation_id TEXT`,
  `causation_id TEXT`, `dedup_key TEXT`, `provenance_json TEXT`,
  `rule_version TEXT`.
- **Migration** (`evidence.py:106-111`): after
  `CREATE TABLE IF NOT EXISTS delivery_ids`, the store reads
  `PRAGMA table_info(delivery_ids)` and runs
  `ALTER TABLE delivery_ids ADD COLUMN …` only for columns that are missing.
  A fresh database and a pre-change database go through the same path. A
  second open adds nothing.
- **`bind_delivery`** (`evidence.py:193`) takes the 10 values as keyword
  arguments, all defaulting to `None`. It writes them in the **same
  `INSERT OR IGNORE` and the same commit** as the binding.
  - `provenance` is stored as compact, key-sorted JSON (`provenance_json`).
  - `NULL` provenance stays `NULL`; it is not written as the string `"null"`.
- `DeliveryBinding` gains the 10 fields, each defaulting to `None`.
  `delivery_binding()` returns them.
- **Rows that keep `NULL`s**: rows copied from `accepted_deliveries` (Fix 6a
  migration), the crash-gap backfill, and the concurrency-retry backfill. All
  three still call `bind_delivery` with only the four binding values.

### 2. Domain fact (`src/state_machine/learner.py`, +8 lines, nothing else)

- `complete_lesson` (`learner.py:86-87`) takes
  `correlation_id: str | None = None` and `causation_id: str | None = None`.
- It passes them on to `_record_lesson_completion` (`learner.py:114-115`).
- The `@event("LessonCompleted")` handler (`learner.py:216-217`) takes both,
  with default `None`. The handler does **not** read them, so no aggregate
  state, decision, event, XP or streak behaviour changes.

**Why old events still replay.** With eventsourcing 9.5.5:

- The mapper rebuilds an event by setting its `__dict__` to the stored JSON
  (`persistence.py:438-439`).
- `DecoratedFuncCaller.apply` calls the handler with only the stored keys
  that match its parameters (`domain.py:853-857`).

So an old event without the two keys calls the handler with their `None`
defaults. The rebuilt event object simply **lacks** those attributes; they
are not set to `None`. Readers of old events must use
`getattr(event, "correlation_id", None)`. No reader does today.

### 3. Ingestion (`src/ingestion/service.py`)

- Both `learner.complete_lesson(...)` calls pass
  `correlation_id=envelope["correlation_id"]` and
  `causation_id=envelope["causation_id"]`: the first attempt (`service.py:239`)
  and the retry after `IntegrityError` (`service.py:290`).
- The accept-path `bind_delivery` (`service.py:328`) passes all the envelope
  values. `rule_version` is `DEFAULT_INGEST_RULE_VERSION` ("v1"), the rule
  version the `dedup_key` was validated against.
  - `Learner.DEFAULT_RULE_VERSION`, used for the XP amount, is also `"v1"`.
    The two are separate constants today.

Both fields are in the envelope schema's `required` list, so indexing
`envelope[...]` after validation is safe.

## Existing test touched

In `tests/ingestion/test_event_id_binding.py` (Fix 6a),
`test_crash_before_binding_is_backfilled_from_the_aggregate` replaces
`bind_delivery` with a stub that raises `RuntimeError("simulated crash")`.
The stub was `def crash(*args)`. `bind_delivery` is now also called with
keyword arguments, so the stub raised `TypeError` before it could simulate
the crash. That made 1 test fail.

The stub is now `def crash(*args, **kwargs)`. No assertion changed.

## Tests (`tests/ingestion/test_provenance.py`)

| Test | What it proves |
|---|---|
| `test_accepted_delivery_keeps_exact_envelope_provenance` | After one accepted delivery, the `delivery_ids` row equals a literal dict of all 13 fields, including `causation_id: None`, `dedup_key: "…07a1:v1"`, `provenance_json: '{"source":"client"}'` and `rule_version: "v1"`. The stored `LessonCompleted` has the literal `correlation_id` and `causation_id is None`, and the `causation_id` key is present in its stored state. `xp_total` is 100. |
| `test_non_null_causation_id_is_kept` | A non-null `causation_id` reaches both the row and the event. |
| `test_provenance_survives_restart_on_the_same_files` | Close both the application and the evidence store, reopen them on the same `events.db`, and the same literal checks pass. |
| `test_old_shape_lesson_completed_event_still_replays` | A real stored `LessonCompleted` is rewritten in SQLite into the exact pre-change shape: its keys are only `activity_date, completion_id, lesson_id, occurred_at, source_event_id, timestamp, timezone_name`. A new application replays it with exact checks on `completed_lesson_ids`, `completion_sources`, counts, `activity_dates`, `xp_total` 100, `new_streak` 1 and `timezone_history`. The rebuilt event does not have the two keys. |
| `test_pre_change_database_opens_keeps_rows_and_stores_new_provenance` | `delivery_ids` is rebuilt in its 4-column pre-change shape, then the store is reopened. The table then has the 14 expected columns in order, and the old row is kept with its digest and `NULL` provenance. `cmp-B` with the same `event_id` is still rejected. A later delivery is stored with the full literal provenance, and `xp_total` is 200. A third open keeps 14 columns and 2 rows. |
| `test_backfilled_crash_gap_row_keeps_null_provenance` | A crash-gap backfill row has every provenance column `NULL`. The domain event saved before the crash does have `correlation_id`. |

## Before / after

One accepted delivery with `event_id …07a1`, `correlation_id …07c1` and
`causation_id null`:

| Place | Before | After |
|---|---|---|
| `delivery_ids` columns | `event_id, learner_id, action_id, digest` | the same 4, plus `producer, event_type, schema_version, occurred_at, ingested_at, correlation_id, causation_id, dedup_key, provenance_json, rule_version` |
| `delivery_ids.correlation_id` | (no column) | `"00000000-0000-4000-8000-0000000007c1"` |
| `delivery_ids.dedup_key` | (no column) | `"00000000-0000-4000-8000-0000000007a1:v1"` |
| `delivery_ids.provenance_json` | (no column) | `'{"source":"client"}'` |
| `delivery_ids.rule_version` | (no column) | `"v1"` |
| stored `LessonCompleted` keys | `timestamp, completion_id, lesson_id, activity_date, timezone_name, occurred_at, source_event_id` | the same, plus `correlation_id` and `causation_id` (here `"…07c1"` and `null`) |
| `xp_total` / `new_streak` after the delivery | 100 / 1 | 100 / 1 |
| suite (excluding leaderboard and notifications) | 191 passed, 4 skipped | 197 passed, 4 skipped |

The "before" event keys are the ones the old-shape test asserts after
removing the two new keys. Only the handler signature changed, so that is
exactly what was stored before.

## Known limits

- **Partial provenance on the domain event.** It carries only
  `correlation_id` and `causation_id`. The full envelope is kept only in
  `delivery_ids`, which lives in the evidence store and not the event store.
  The two are joined by `source_event_id = delivery_ids.event_id`.
- **Rows that stay `NULL`.** Pre-change, migrated and crash-backfilled rows
  have `NULL` provenance. The aggregate's `LessonCompleted` cannot fill them
  in: it never held most of the fields, and it holds correlation and
  causation only for events stored after this change.
