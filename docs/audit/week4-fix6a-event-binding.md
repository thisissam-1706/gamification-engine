# Week 4 Fix 6a: Bind each transport event_id to one action

Status: **done. Not committed.**

- Week 3 review row 26 is fixed in `src/ingestion/` only. `src/state_machine/`
  is unchanged.
- `tests/ingestion/test_event_id_binding.py` adds 8 tests, and **all 8 pass**.
  Against the pre-fix code, all 8 fail (see "Tests").
- Suite excluding `tests/leaderboard` and `tests/notifications`: **183 passed,
  0 failed, 4 skipped**. Before the fix it was 175 passed, 4 skipped.

HEAD at planning time: `fec927e small fix`. The working tree was clean.

## Problem

Ingestion deduplicated only on the business identity
`(learner_id, completion_id)`. The transport `event_id` was stored as
`completion_sources[completion_id]` and in `accepted_deliveries`, but nothing
looked it up. So one `event_id` sent with two different `completion_id`s
counted as two new completions.

Reproduced on HEAD with one learner and
`event_id = 00000000-0000-4000-8000-0000000006a1`:

| Delivery | Result |
|---|---|
| `cmp-A` | `accepted` |
| `cmp-B`, same `event_id` | `accepted` (**bug**) |
| `cmp-A` again, same `event_id` | `duplicate`, `duplicate_completion_id` |

The learner ended with `xp_total = 200` and `completed_lesson_count = 2`. The
second delivery left no evidence row.

## Change

### 1. `delivery_ids` table (`src/ingestion/evidence.py`)

```sql
CREATE TABLE IF NOT EXISTS delivery_ids (
    event_id   TEXT PRIMARY KEY,
    learner_id TEXT NOT NULL,
    action_id  TEXT NOT NULL,
    digest     TEXT
)
```

**Migration.** `EvidenceStore.__init__` creates the table with
`IF NOT EXISTS`, then runs
`INSERT OR IGNORE INTO delivery_ids SELECT event_id, learner_id, action_id, NULL FROM accepted_deliveries`.
So an existing database opens without error, and every delivery it already
accepted is bound to its action right away. Running it again does nothing
because of the primary key.

`digest` can be `NULL` only for those migrated rows, because the original
envelope was never stored. The first later delivery of that `event_id` with
the same learner and action fills it in (`fill_delivery_digest`).

New store methods: `bind_delivery` (`INSERT OR IGNORE`), `delivery_binding`
(lookup that returns a `DeliveryBinding` dataclass), and `fill_delivery_digest`.

### 2. `canonical_digest(envelope)` (`src/ingestion/service.py:39`)

This is the SHA-256 hex of
`json.dumps({f: envelope[f] for f in DIGEST_FIELDS}, sort_keys=True, separators=(",", ":"))`.
`DIGEST_FIELDS` holds `event_type`, `schema_version`, `subject_id`,
`producer`, `occurred_at` and `payload`. The digest leaves out `event_id`,
`ingested_at`, `correlation_id`, `causation_id`, `dedup_key` and
`provenance`, so a redelivery that changes only transport metadata keeps the
same digest.

### 3. Lookup in `ingest_lesson_completed`

The lookup runs after all validation and **before** `repository.get`, so a
known `event_id` is answered without loading or changing the aggregate
(`service.py:179`):

| `delivery_ids` row for `event_id` | Result | Evidence row |
|---|---|---|
| none | continue as before; on acceptance `bind_delivery(event_id, learner, action, digest)` runs right after `remember_delivery` (`service.py:307`) | as before |
| same learner, same action, same digest (or digest `NULL`) | `duplicate`, `duplicate_event_id` | `duplicate`, `action_id` = this action, `original_ref` = the bound `event_id` |
| different action, learner or digest | `rejected`, `conflicting_event_id_reuse` | `rejected`, `action_id` = the new action, `original_ref` = **the original `action_id`** |

A duplicate delivery with a new `event_id` does not bind that `event_id`. Only
accepted completions are recorded. Repeated `duplicate_event_id` resends
collapse into one row through the existing unique index on duplicate
evidence.

### 4. Crash gap (`service.py:191`)

If the process dies after `application.save` but before `bind_delivery`, the
aggregate has `completion_sources[action] == event_id` but `delivery_ids` has
no row. When `delivery_ids` has no row, the service loads the learner and
searches `completion_sources` for this `event_id`. If it finds a match:

- it backfills the row, using the current digest when the action matches,
  else `NULL`;
- it answers through the same table as above (`duplicate_event_id` or
  `conflicting_event_id_reuse`). It does not raise.

The concurrency retry path after `IntegrityError` (`service.py:249`) also
checks the reloaded aggregate. If a concurrent writer already accepted this
`event_id` for a different action, the delivery is rejected with
`conflicting_event_id_reuse` instead of being accepted as a second completion.

## Tests (`tests/ingestion/test_event_id_binding.py`)

| Test | What it proves |
|---|---|
| `test_same_event_id_with_different_completion_id_is_rejected` | `cmp-A` gives accepted and `cmp-B` gives rejected, with `xp_total` 100, `completed_lesson_ids == {"cmp-A"}`, no new stored events, and **one** evidence row (`rejected`, `action_id=cmp-B`, `original_ref=cmp-A`). The binding row holds `cmp-A` and the first digest. |
| `test_same_event_id_with_identical_payload_is_a_duplicate` | A resend with new `ingested_at` and `correlation_id` has the same digest. Sent twice, it gives `duplicate_event_id` both times, with one evidence row (`original_ref` = the `event_id`) and `xp_total` 100. |
| `test_same_event_id_and_action_with_changed_payload_is_rejected` | Same `event_id` and action with a different `lesson_id` is rejected; `original_ref=cmp-A`. |
| `test_event_id_binding_survives_restart_on_the_same_files` | Accept, close both the application and the evidence store, and reopen them on the same `events.db`. Then `cmp-B` gives rejected and `cmp-A` gives `duplicate_event_id`, with `xp_total` 100. |
| `test_new_event_id_with_same_completion_id_is_unchanged` | Behaviour from before the fix: `duplicate_completion_id`, `original_ref` = the first `event_id`, and the retry `event_id` is not bound. |
| `test_crash_before_binding_is_backfilled_from_the_aggregate` | `bind_delivery` raises after the save. The `cmp-B` retry is rejected and a row with digest `NULL` is backfilled. The identical `cmp-A` retry then gives `duplicate_event_id` and fills in the digest. `xp_total` stays 100. |
| `test_database_without_delivery_ids_opens_and_is_migrated` | Accept, then `DROP TABLE delivery_ids` to get a pre-fix file, then reopen. The row is migrated from `accepted_deliveries` with digest `NULL`, and `cmp-B` is rejected. A second reopen leaves the row count at 1. |
| `test_canonical_digest_covers_exactly_the_content_fields` | Changing any of the 6 excluded fields leaves the digest unchanged. Reordering keys (top level and payload) leaves it unchanged. Changing any of the 6 included fields changes it. |

**Run against the pre-fix code.** I used a scratch copy with
`src/ingestion/{service,evidence}.py` taken from HEAD; the repo was not
changed. All 8 tests fail:

- 3 fail on behaviour (`accepted` or `duplicate_completion_id` where
  `rejected` was expected);
- the rest fail on the missing `delivery_ids` table, the missing store methods
  or the missing `canonical_digest`.

`test_new_event_id_with_same_completion_id_is_unchanged` fails there only on
the missing `delivery_binding` method. Its behavioural asserts match the old
code, which is the point of that test.

## Before / after

Same scenario as in "Problem": one learner, one `event_id`, deliveries
`cmp-A`, `cmp-B`, `cmp-A`.

| Value | Before | After |
|---|---|---|
| `cmp-A` | `accepted` | `accepted` |
| `cmp-B`, same `event_id` | `accepted` | `rejected`, `conflicting_event_id_reuse` |
| `cmp-A` again, same `event_id` | `duplicate`, `duplicate_completion_id` | `duplicate`, `duplicate_event_id` |
| `xp_total` after the first two deliveries | 200 | 100 |
| `completed_lesson_count` after the first two deliveries | 2 | 1 |
| evidence rows after the first two deliveries | 0 | 1 (`rejected`, `original_ref=cmp-A`) |
| suite (excluding leaderboard and notifications) | 175 passed, 4 skipped | 183 passed, 4 skipped |

## Known limits

- **Rows with `NULL` digest.** For migrated or backfilled rows, the first later
  delivery with the same learner and action is accepted as the reference
  digest. A changed payload in that first delivery cannot be detected. Learner
  state is still safe, because the completion already exists.
- **Crash-gap lookup is per learner.** It searches only the target learner's
  aggregate. If an `event_id` was accepted for learner X and its binding row
  was lost in a crash, a reuse of that `event_id` for learner Y is not caught.
  Once the row exists, a reuse across learners is caught.
- **Concurrent first deliveries.** The `event_id` check is a read before the
  write, not a unique constraint inside the event store. Two concurrent first
  deliveries with the same `event_id` and different actions for one learner
  are caught by the retry-path check after `IntegrityError`. The same race
  across two different learners is not caught.
