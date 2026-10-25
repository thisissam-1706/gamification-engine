# Week 4 Fix 10: Intermittent suite hang (deflake)

Status: **fixed and proven. Not committed.**

- **Cause.** A race in the `delivery_ids` schema migration in
  `src/ingestion/evidence.py`, introduced in Fix 7a. It is **not** a
  fork-related problem.
- **Fix.**
  1. The migration now runs in one `BEGIN IMMEDIATE` transaction with a 30 s
     busy timeout, and a `duplicate column name` error is treated as already
     migrated.
  2. The two concurrent ingestion tests now use `spawn`, open their stores
     inside the worker's `try`, and bound every wait with a timeout.
- **Proof.**
  - New migration regression file: 50 of 50 runs passed.
  - Each concurrent test: 30 of 30 runs passed.
  - Full suite: 10 of 10 runs passed (246 passed, 4 skipped).
  - No hangs.
- No existing assertion was weakened.

## Symptom

- About 2 of 3 plain `pytest -q … | tail -1` runs stalled forever at
  "78 passed, 3 skipped". The 82nd test in collection order is
  `tests/ingestion/test_ingestion_service.py::test_concurrent_different_completions_retry_without_lost_update`.
- That test and
  `test_concurrent_duplicate_completion_is_acknowledged_as_one_duplicate`
  started two workers with `multiprocessing.get_context("fork")`, then called
  `join()` and `results.get()` with no timeouts.

## Final cause: the `delivery_ids` migration race (not fork)

### Hypotheses ruled out

1. **Inherited SQLite connections across fork.** A scratch fork hook logged
   the parent at all 4 forks of a full run. Each time it had exactly one
   thread (`MainThread`) and **no** open file descriptor to any `.db` file.
   Both tests close their parent `LearnerApplication` / `EvidenceStore`
   before forking, and no earlier test leaks a connection.
2. **Fork-with-threads deadlock.** The `DeprecationWarning: This process … is
   multi-threaded, use of fork() may lead to deadlocks` appeared only when
   the diagnostics themselves added a thread (pytest's
   `faulthandler_timeout` watchdog). Plain runs report no warnings and fork
   from a single thread.
3. **A worker stuck inside the child.** A stall was captured with
   `faulthandler.register(SIGUSR1)` stack dumps.
   - Only the pytest parent was alive, waiting in `pipe_read`; both workers
     had already exited.
   - The parent's Python stack was `test_ingestion_service.py:325`
     (`results.get()`) → `multiprocessing/queues.py:101 get`.

   So `join()` had returned, and a worker had exited without putting a
   result.

### Root cause, captured directly

A scratch hook sent each child's stderr to a file and logged exit codes. On
the stalled run, the second test's first worker exited with code 1:

```
File ".../tests/ingestion/test_ingestion_service.py", line 21, in _ingest_in_worker
    evidence = EvidenceStore(db)
File ".../src/ingestion/evidence.py", line 116, in __init__
    self.connection.execute(f"ALTER TABLE delivery_ids ADD COLUMN {column} {column_type}")
sqlite3.OperationalError: duplicate column name: event_type
```

**The race.** `EvidenceStore.__init__` migrated `delivery_ids` by check, then
act, with no lock across the steps:

1. `CREATE TABLE IF NOT EXISTS`;
2. `PRAGMA table_info`, to find the missing columns;
3. `ALTER TABLE … ADD COLUMN` for each of them (Fix 7a's provenance columns,
   Fix 9b's `rule_digest`).

Two processes opening an un-migrated file both read the old column set, and
the slower one's `ALTER` failed.

**Why this test, why a hang:**

- Only `…different_completions…` hit it: its parent creates just the event
  store, so both workers migrate at once. `…duplicate_completion…` opens an
  `EvidenceStore` in the parent first.
- The worker constructed its stores **before** its `try`, so it died without
  reporting. Its traceback went to pytest's captured stderr, and the
  untimed `results.get()` waited forever.

**Why timing changed it.** The stall rate followed timing only. In the
diagnostic hunts:

| Mode | Stalls |
|---|---|
| capture on, output piped | 1 in 7 and 1 in 10 runs |
| `-s` | 0 in 25 runs |
| capture on, output to a file | 0 in 18 runs |
| `-v` with faulthandler | 0 in 4 runs |

This is consistent with a narrow race window.

### Isolated reproduction (no pytest, before the fix)

Two processes opened `EvidenceStore(db)` behind a barrier, on a file with an
event store but no evidence tables:

| Start method | Openers | Failed with `duplicate column name` |
|---|---|---|
| `fork` | 400 | 64 |
| `spawn` | 200 | 3 |
| `fork`, tables already migrated (control) | 400 | 0 |

`spawn` alone only lowers the rate, so a test-only fix would have turned the
hang into an occasional failure and left a production defect: two ingestion
processes starting at once on an un-migrated database could crash one of
them.

The scratch diagnostics (fork hook, SIGUSR1 plugin, stall hunter,
`stress_migrate.py`) and the captured stall logs were in the session
scratchpad. They were **cleared between sessions** and are no longer
available. The new regression test below replaces the reproduction, and was
shown to fail on the unfixed code.

## Fix

### 1. Production: atomic migration (`src/ingestion/evidence.py`)

| Line | Change |
|---|---|
| `:43` | `BUSY_TIMEOUT_SECONDS = 30.0`. |
| `:70` | `sqlite3.connect(database, timeout=BUSY_TIMEOUT_SECONDS)`: a second opener waits on SQLite's busy handler instead of failing. The default was 5 s. |
| `:73` | `BEGIN IMMEDIATE` before any schema work. This takes the database write lock **before** the schema is read, so concurrent openers migrate strictly one after another. |
| `:76-79` | On any error the transaction is rolled back, the connection closed, and the error re-raised. |
| `:82` | The schema work moved, unchanged, into `_create_and_migrate_schema()`: the `CREATE … IF NOT EXISTS` statements, the `delivery_ids` table, the `accepted_deliveries` → `delivery_ids` backfill with `NULL` digests, and the column loop. |
| `:129` | `PRAGMA table_info(delivery_ids)` is now read **inside** the transaction. |
| `:140` | `ALTER TABLE … ADD COLUMN` that fails with `duplicate column name` is treated as already migrated (defensive); any other `OperationalError` is re-raised. |

Existing behaviour is unchanged: old databases still open, missing columns
are added only when missing, old rows keep `NULL` provenance and
`rule_digest`, and the commit happens once at the end. The whole existing
suite, including the 6a/7a/9b migration tests, passes unchanged.

### 2. Tests: the two concurrent ingestion tests (`tests/ingestion/test_ingestion_service.py`)

- **`spawn` context.** `multiprocessing.get_context("fork")` became
  `get_context("spawn")` in both tests (`:300`, `:338`).
- **Top-level worker.** `_ingest_in_worker` (`:21`) stays a module-level
  function, so a `spawn` child can import it.
  - `LearnerApplication(...)` and `EvidenceStore(db)` are now constructed
    **inside** its `try`, so a startup error is reported as
    `("error", "<Type>: <message>")` instead of killing the worker silently.
  - The `finally` closes only the stores that were opened.
- **Timeouts.** New `_collect_worker_results(workers, results)` (`:43`):
  - every `results.get()` uses `timeout=WORKER_TIMEOUT_SECONDS` (60 s,
    `:18`);
  - every `join()` uses the same timeout;
  - on a timeout it calls `pytest.fail` with the results so far and the
    worker exit codes;
  - its `finally` terminates any worker still alive.
- **Assertions.** Each test's
  `assert sorted(results.get() for _ in workers) == [...]` became
  `assert sorted(_collect_worker_results(workers, results)) == [...]`. The
  expected lists, the worker count (2) and every later assertion are
  identical.

### 3. Regression test (`tests/ingestion/test_evidence_migration_concurrency.py`, new)

Two `spawn` workers are started once (top-level `_open_in_rounds`). They then
run **20 rounds**: in each round both wait on a shared `Barrier` and open
`EvidenceStore` on the same fresh file at the same moment. Each outcome is
reported, never raised. Every `get`/`join` is bounded by 60 s, with terminate
and `pytest.fail` on timeout.

| Test | Database per round | Asserts |
|---|---|---|
| `test_two_processes_migrate_a_fresh_database_concurrently` | event store only, no evidence tables (the hanging test's setup) | both openers `"ok"`; `delivery_ids` columns exactly `event_id, learner_id, action_id, digest` + the 11 `DELIVERY_COLUMNS` in order |
| `test_two_processes_migrate_a_pre_provenance_database_concurrently` | an old 4-column `delivery_ids` with one row, and `accepted_deliveries` | both `"ok"`; the same column list; the old row kept as `("event-A", "learner-1", "cmp-A", "digest-A", None × 11)` |

**Shown to catch the bug.** I ran it against a scratch copy of the repo with
the fix removed: no `BEGIN IMMEDIATE`, no duplicate tolerance, and the default
timeout. It **failed in 3 of 3 runs** (5 of 6 test executions), each with
`OperationalError: duplicate column name: …` (`producer`, `rule_version`,
`rule_digest`, `schema_version`).

## Proof runs (fixed code)

All runs used plain `pytest -q -p no:cacheprovider … | tail -1`: capture on,
output piped, which is the mode that hung before.

| Run | Count | Passed | Failed | Hung / timed out |
|---|---|---|---|---|
| `test_evidence_migration_concurrency.py` (2 tests × 20 rounds × 2 openers each) | 50 | **50** | 0 | 0 |
| `test_concurrent_duplicate_completion_is_acknowledged_as_one_duplicate` | 30 | **30** | 0 | 0 |
| `test_concurrent_different_completions_retry_without_lost_update` | 30 | **30** | 0 | 0 |
| full suite excluding `tests/leaderboard` and `tests/notifications`, under `timeout 600` | 10 | **10** (each 246 passed, 4 skipped, 3.2–3.3 s) | 0 | 0 |

The 50 regression runs performed 4,000 simultaneous two-process opens of
un-migrated databases without an error. Before the fix, the same setup
failed in about 16% of opens under `fork`.

Suite count: 246 passed, 4 skipped. That is 244 + the 2 new regression tests.

## Before / after

| | Before | After |
|---|---|---|
| Two processes opening an un-migrated database at once | one may die with `duplicate column name` | both open; the second waits for the first's migration |
| Concurrent worker startup error | the worker dies silently; the parent hangs forever | reported as `("error", …)`; the test fails with that message |
| Lost worker or stuck child | infinite `get()` / `join()` | `pytest.fail` after 60 s, with workers terminated |
| Plain `pytest -q` suite runs | about 2 of 3 hung | 10 of 10 passed |

## Known limits

- **Lock wait.** `BEGIN IMMEDIATE` makes every `EvidenceStore` open take the
  write lock briefly, even when no migration is needed. Opens are
  serialised, which only matters at startup. A process that holds a long
  write transaction on the same file can make an opener wait up to 30 s, and
  then fail with `database is locked`.
- **Untraced first stall.** Fork-with-threads is not the cause here, but the
  first stall in the 8b session was not traced. It is attributed to this
  race because its position (the same test) and its symptom match.
