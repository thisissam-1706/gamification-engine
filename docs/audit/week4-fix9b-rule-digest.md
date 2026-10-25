# Week 4 Fix 9b: Persist the rule digest; make AT-5 real

Status: **done. Not committed.**

- At acceptance, the content digest of the rule set that decided the delivery
  is stored on its `delivery_ids` row (`rule_digest`) and exposed on the
  trace.
- `test_historical_reward_replay_uses_persisted_amount_after_rule_update` was
  rewritten so it really changes v1's rule (to 999 XP) before replaying. This
  resolves the 9a pending decision.
- `docs/event-contracts.md` gains a "Rule sets" section.
- `tests/ingestion/test_rule_digest.py` adds 4 tests, and **all 4 pass**.
- Suite excluding `tests/leaderboard` and `tests/notifications`: **244 passed,
  0 failed, 4 skipped**. After 9a it was 239 passed, 1 failed, 4 skipped; the
  difference is the 4 new tests plus the rewritten test now passing.
- **Four existing assertions were extended** (none removed or weakened). See
  "Existing assertions extended".
- This builds on the uncommitted Fix 6a–9a working tree.

## Problem

1. `delivery_ids.rule_version` recorded only a version *label*. After Fix 9a
   a label is bound to content by `rulesets.json`, but the provenance row
   itself did not say which content decided it.
2. `test_rule_versioning.py::test_historical_reward_replay_uses_persisted_amount_after_rule_update`
   simulated "the rule changes to 150" by setting `XP_AMOUNTS["v1"] = 150`.
   Nothing reads `XP_AMOUNTS`, so the rule never changed and the test proved
   nothing about rule updates. After 9a, `XP_AMOUNTS` is read-only, so the
   line raised `TypeError`.

## Change

### Rule digest on the provenance row

| File | Change |
|---|---|
| `src/ingestion/evidence.py:37-40` | New `RULE_COLUMNS = {"rule_digest": "TEXT"}` and `DELIVERY_COLUMNS = {**PROVENANCE_COLUMNS, **RULE_COLUMNS}`. The migration loop, the `INSERT` and the `SELECT` use `DELIVERY_COLUMNS`, so `rule_digest` is added with `ALTER TABLE … ADD COLUMN` only when it is missing, just like the 7a columns. |
| `src/ingestion/evidence.py` | `DeliveryBinding.rule_digest: str \| None = None`. `bind_delivery(..., rule_digest=None)` writes it in the same `INSERT` and commit. |
| `src/ingestion/service.py:348` | The accept-path `bind_delivery` passes `rule_digest=get_ruleset(config.rule_version).digest`. |
| `src/ingestion/trace.py:25` | `DeliveryProvenance.rule_digest: str \| None`, filled from the binding. |

Why `rule_digest` is a separate dict and not in `PROVENANCE_COLUMNS`:
`PROVENANCE_COLUMNS` lists values copied from the **source envelope**. The
rule digest is an **engine-side** fact about the decision. The split keeps
that distinction, and it also leaves 7a's literal provenance-row assertions
untouched.

Rows that keep `NULL` are the ones not written by the accept path: rows from
before this change, rows rebuilt from `accepted_deliveries`, and the
crash-gap and concurrency-retry backfills. All of these are tested except the
concurrency-retry backfill.

### Digests

| Version | Content | `rule_digest` |
|---|---|---|
| v1 (packaged) | 100, `{"lesson_starter_v1": 3}`, 7, `week_warrior_v1` | `ba6d0a3821ebdb1358840ec7a0dd2f67136b4eeb3062d79f3369b53cda6b4430` |
| v2 (test-only, monkeypatched) | 150, `{"lesson_starter_v1": 3}`, 7, `week_warrior_v1` | `ced6f893cae47f7abb7fbec4edf98daf5ac953b491f2e5278a282a2a336e1b8b` |

### AT-5 test rewritten (`tests/state_machine/test_rule_versioning.py`)

The test name is kept. The flow is now:

1. On a SQLite file, `complete_lesson("c-v1", "l1", rule_version="v1")`
   gives `xp_total` 100, and the learner is saved.
2. Close the application.
3. `monkeypatch.setitem(_REGISTRY, "v1", RuleSet(xp_amount=999, …v1's other fields…))`.
   The test asserts `xp_amount_for("v1") == 999`, so the premise is checked,
   not assumed.
4. Reopen. Then:
   - `rebuild_learner_state(events).xp_total == 100`;
   - `repository.get(id).xp_total == 100`;
   - exactly one `XPAwarded` exists, with `xp_amount 100`, `reward_id
     "xp:c-v1"` and `rule_version "v1"`.

The test's docstring now describes this flow. The `XP_AMOUNTS` import was
replaced by `xp_amount_for`. The module docstring (the AT-5 acceptance text,
"E = 150") was left as the spec wording.

## Existing assertions extended

Adding a column to `delivery_ids` and a field to `DeliveryProvenance` changed
values that earlier tests compare exactly. Each assertion was extended by the
new value, and nothing was removed:

| File | Assertion | Change |
|---|---|---|
| `tests/ingestion/test_provenance.py` | `test_pre_change_database_opens_keeps_rows_and_stores_new_provenance`: the exact list of `delivery_ids` columns | `"rule_digest"` appended (14 → 15 names) |
| `tests/ingestion/test_provenance.py` | the same test: `len(_columns(db)) == 14` after a third open | `== 15` |
| `tests/ingestion/test_trace.py` | `_assert_scenario_trace` (used by 2 tests): the expected `DeliveryProvenance(...)` | `rule_digest="ba6d0a38…4430"` added (the literal v1 digest) |
| `tests/ingestion/test_trace.py` | `test_delivery_that_predates_provenance_still_traces`: the expected `DeliveryProvenance(...)` | `rule_digest=None` added |

The four literal provenance-row dicts in `test_provenance.py` did **not**
change, because they are built from `PROVENANCE_COLUMNS`.

## Tests (`tests/ingestion/test_rule_digest.py`)

| Test | What it proves |
|---|---|
| `test_accepted_delivery_records_v1_digest` | After a default accepted delivery, `RULESETS["v1"].digest`, the row's `rule_digest` and the trace's `provenance.rule_digest` all equal the literal v1 digest, with `rule_version "v1"`. |
| `test_delivery_under_v2_records_v2_digest` | With v2 monkeypatched into `_REGISTRY`, a delivery under `IngestConfig("v2")` stores the literal v2 digest (≠ v1). The trace shows `("v2", V2_DIGEST)`. An earlier v1 delivery on the same learner keeps the v1 digest. `xp_total` is 100 + 150 = 250. |
| `test_pre_change_database_opens_with_null_rule_digest` | `ALTER TABLE delivery_ids DROP COLUMN rule_digest` gives back the 9a schema. On reopen the column is added at the end, the old row has `rule_version "v1"` and `rule_digest None`, and the trace shows `None`. A later delivery stores the v1 digest. A third open keeps exactly one `rule_digest` column. |
| `test_migrated_and_backfilled_rows_keep_null_rule_digest` | A crash-gap backfill row has `rule_digest None`. A row rebuilt from `accepted_deliveries` after `DROP TABLE delivery_ids` also has `None`. |

## Doc change: `docs/event-contracts.md`, "Rule sets"

This section follows "Effective rule version" and states three points:

- Rule sets live in `rulesets.json` with a verified per-version `sha256`.
- Published versions are immutable; a content change needs a new version and
  digest.
- The accepted rule's digest is recorded on the provenance row
  (`delivery_ids.rule_digest`). Older rows have `NULL`.

## Before / after

| | Before | After |
|---|---|---|
| `delivery_ids` columns | 14 | 15 (`+ rule_digest`) |
| `rule_digest` for a default accepted delivery | (no column) | `ba6d0a38…4430` |
| `rule_digest` for a delivery under `v2` | (no column) | `ced6f893…1b8b` |
| `trace.provenance.rule_digest` | (no field) | the row's value, or `None` |
| AT-5 test | `XP_AMOUNTS["v1"] = 150` (no effect; `TypeError` after 9a) | v1 really replaced by a 999-XP rule; replay still 100 |
| suite (excluding leaderboard and notifications) | 239 passed, 1 failed, 4 skipped | 244 passed, 4 skipped |

## Suite stability note (follow-up to the 8b hang)

- **What happened.** In one batch of two back-to-back full runs, the second
  run printed no result within its 100 s `timeout`. Five further runs, with
  `-X faulthandler` and `faulthandler_timeout=30`, all passed with 244
  passed, 4 skipped, in 3.6–4.2 s. None of them hung, so no stack was
  captured.
- **The lead.** Those runs show a `DeprecationWarning` from
  `multiprocessing/popen_fork.py`: "This process … is multi-threaded, use of
  fork() may lead to deadlocks in the child". It comes from the two
  pre-existing tests
  `tests/ingestion/test_ingestion_service.py::test_concurrent_duplicate_completion_is_acknowledged_as_one_duplicate`
  and `::test_concurrent_different_completions_retry_without_lost_update`.
  Forking a multi-threaded process is a known source of intermittent child
  deadlocks, which fits the symptom.
- **Status.** This is a lead, not a confirmed cause. It is outside this
  step's scope, so those tests were not changed. A likely fix would be a
  `spawn` multiprocessing context in those tests.

**Update, same day, re-verification run.** The hang reproduced:

- **Frequency.** 2 of 3 plain `pytest -q` runs stalled at exactly
  "78 passed, 3 skipped" until a 90 s `SIGINT`.
- **Location.** 81 finished tests means the stuck test is number 82 in
  collection order:
  `tests/ingestion/test_ingestion_service.py::test_concurrent_different_completions_retry_without_lost_update`.
- **Why it hangs forever.** That test forks two workers with
  `multiprocessing.get_context("fork")`, then calls `worker.join()` and
  `results.get()` with **no timeouts**. One stuck child stalls the whole
  suite.
- **Not reproduced under diagnostics.** 4 verbose runs with
  `faulthandler_timeout=40` did not hang, so no child stack was captured.
- **Likely mechanisms.** Fork-with-threads deadlock in the child (the
  `DeprecationWarning` above), or a stall between two SQLite writers on one
  file.
- **Next step.** Unchanged and out of scope: use a `spawn` context and add
  timeouts on `join`/`get`, so a stall fails fast instead of hanging.
