# Week 4 Audit: Atomic Leaderboard Transactions and State Management (LB-3)

## Problem

Two severe data integrity defects were identified in `src/leaderboard/store.py` and `src/leaderboard/projection.py`:

### Defect 1: No Rollback in `apply_award_atomic` Leading to Corrupt Partial State
- **Location:** `src/leaderboard/store.py:334-438`
- **Root Cause:**
  `apply_award_atomic` executed sequential writes to `leaderboard_applied_events`, `leaderboard_entries` (global scope), `leaderboard_entries` (cohort scope), and `leaderboard_checkpoints` without a `try/except` rollback block.
  If an exception was raised after the global write (e.g. during cohort write or checkpoint advance):
  1. The inserted row in `leaderboard_applied_events` and the updated global leaderboard row remained dirty/uncommitted on the SQLite connection.
  2. The cohort score was left untouched (staying at 0 or None).
  3. The next commit on that connection (such as an unrelated operation or application transaction) permanently persisted this partial state to disk.
  4. Any subsequent retry of the award was refused with `False` because `INSERT INTO leaderboard_applied_events` hit `sqlite3.IntegrityError` (or deduplication check), permanently locking the learner's cohort score at 0.

### Defect 2: Lost Updates in Multi-Projection / Multi-Process Environments
- **Location:** `src/leaderboard/projection.py:251-286` and `src/leaderboard/store.py:368-375`
- **Root Cause:**
  `LeaderboardProjection.record_xp` computed new scores in Python process memory from `self._epoch_scores`:
  ```python
  old_score = self._epoch_scores.get(key, 0)
  candidate_score = old_score + xp_amount
  ...
  applied = self.store.apply_award_atomic(..., global_score=candidate_score, ...)
  ```
  In `store.py`, SQLite upserted this score with `score = excluded.score`.
  When two projection instances (or horizontal workers) operated on the same underlying store:
  - Instance A had an in-memory cache of 50.
  - Instance B processed an award of 60 XP and wrote 110 to SQLite.
  - Instance A (with stale in-memory cache 50) received an award of 60 XP, computed `50 + 60 = 110`, and wrote `score = 110` to SQLite, completely obliterating Instance B's 60 XP update.
  - In reproduction: after 4 alternating awards summing to 220 XP (50 + 60 + 60 + 50), the stored score was only 170 XP. 50 XP was silently lost.

### Defect 3: Non-Deterministic Tie-Breaking for Same-Instant Events
- **Location:** `src/leaderboard/store.py:559` and `src/leaderboard/store.py:1065`
- **Root Cause:**
  `get_applied_events()` sorted applied events solely by `ORDER BY applied_at ASC` (and `key=lambda e: e["applied_at"]`). When two events shared the exact same instant, their ordering was non-deterministic and depended on internal SQLite row storage or insertion sequence. Consequently, recomputing `first_reached_at` yielded non-reproducible outcomes depending on event arrival order.

---

## Changes Made

### 1. Reusable Transaction Context Manager with Explicit Rollback
- **File:** `src/leaderboard/store.py`
  - Added `@abstractmethod def transaction(self):` to `LeaderboardStore`.
  - Implemented `SQLiteLeaderboardStore.transaction()`:
    - Tracks `self._transaction_depth` to support nested/reentrant transaction scopes.
    - Yields cursor for caller.
    - Commits automatically at outermost scope (`_transaction_depth == 1`).
    - Catches all exceptions and triggers `self.connection.rollback()` before re-raising.
  - Implemented `InMemoryLeaderboardStore.transaction()`:
    - Creates deep snapshot of `_entries`, `_applied_events`, `_applied_events_list`, `_checkpoints`, `_reconciliation_audits`, `_completion_times`, and `_pending_awards` at depth 1.
    - Restores the exact pre-transaction state on any exception.
  - Updated all mutating store methods (`set_committed_offset`, `record_reconciliation_audit`, `upsert_score`, `remove_member`, `record_completion_time`, `record_pending_award`, `delete_pending_award`, `clear`) to commit only when `self._transaction_depth == 0`.

### 2. SQL Increments and In-Transaction `first_reached_at` Derivation
- **Files:** `src/leaderboard/store.py`, `src/leaderboard/projection.py`
  - In `SQLiteLeaderboardStore.apply_award_atomic`:
    - Wrapped the entire sequence (`leaderboard_applied_events`, global score, cohort score, checkpoints) in `with self.transaction():`.
    - Scores are **never** computed from process memory. Global and cohort scores are incremented directly in SQL:
      ```sql
      INSERT INTO leaderboard_entries (
          scope_type, scope_id, epoch, subject_id, score, first_reached_at, last_updated_at, watermark_offset
      ) VALUES ('global', 'global', ?, ?, ?, ?, ?, ?)
      ON CONFLICT(scope_type, scope_id, epoch, subject_id) DO UPDATE SET
          score = leaderboard_entries.score + ?,
          first_reached_at = ?,
          last_updated_at = ?,
          watermark_offset = MAX(leaderboard_entries.watermark_offset, ?)
      ```
    - `first_reached_at` is recomputed inside the transaction from store rows:
      ```sql
      SELECT applied_at, xp_amount, stream_offset, event_id
      FROM leaderboard_applied_events
      WHERE subject_id = ? AND epoch = ?
      ORDER BY applied_at ASC, stream_offset ASC, event_id ASC
      ```
      Accumulates running sum against total store score to determine the exact earliest timestamp when the final score was attained.
  - In `LeaderboardProjection.record_xp`:
    - Ceased calculating candidate score from `self._epoch_scores`.
    - Delegates award application and increment directly to `store.apply_award_atomic()`.
    - In-memory projection structures (`self._epoch_scores`, `self._first_reached`, `self._subject_events`) are strictly maintained as a read cache refreshed from the store after transaction commit.
  - In `LeaderboardProjection.assign_cohort`:
    - Encapsulated `remove_member` and `upsert_score` inside `with self.store.transaction():`.
    - Global score is loaded from `self.store.get_entry()` rather than process memory.

### 3. Deterministic Same-Instant Ordering
- **File:** `src/leaderboard/store.py`
  - In `SQLiteLeaderboardStore`:
    - Added index `idx_applied_events_ordering ON leaderboard_applied_events (subject_id, epoch, applied_at ASC, stream_offset ASC, event_id ASC)`.
    - `get_applied_events()` orders by `applied_at ASC, stream_offset ASC, event_id ASC`.
  - In `InMemoryLeaderboardStore`:
    - `get_applied_events()` sorts by `(e["applied_at"], e.get("stream_offset", 0), e.get("event_id", ""))`.
  - Ensures identical chronological ordering and identical `first_reached_at` derivations regardless of event arrival order.

### 4. Authoritative Duplicate Check
- **File:** `src/leaderboard/store.py`
  - `(event_id, subject_id)` primary key in `leaderboard_applied_events` remains the authoritative boundary.
  - In `apply_award_atomic`, `sqlite3.IntegrityError` rolls back and returns `False`.
  - In-memory pre-check in `projection.py` remains purely as a fast-path optimization.

---

## Tests Added

Targeted test suite in `tests/leaderboard/test_atomic_awards.py`:

1. **`test_atomic_rollback_on_cohort_write_failure[sqlite / in_memory]`**
   - Injects a failure between global and cohort writes using `_fail_after_global_write`.
   - Executes an unrelated commit on the same connection.
   - Verifies global score remains 0 and applied-event row is not present.
   - Retries the award: succeeds cleanly (`True`).
   - Verifies global score is 100, cohort score is 100, and a second retry returns `False` (duplicate).

2. **`test_two_projections_stale_cache_ordering_preserves_all_updates`**
   - Two separate `LeaderboardProjection` instances on a shared SQLite file.
   - Executes alternating awards for the same learner: A applies 50 XP, B applies 60 XP, A applies 60 XP (with stale cache), B applies 50 XP (with stale cache).
   - Verifies stored score equals the exact sum of applied events: **220 XP**.
   - Verifies `first_reached_at` accurately matches chronological milestone **`2026-10-12T10:30:00Z`**.

3. **`test_same_instant_events_produce_identical_results_regardless_of_arrival_order[sqlite / in_memory]`**
   - Two events at the same instant (`2026-10-12T11:00:00Z`): 50 XP (offset 1, id `ev_alpha`) and 30 XP (offset 2, id `ev_beta`).
   - Trial 1 processes Event 1 then Event 2; Trial 2 processes Event 2 then Event 1.
   - Asserts identical scores (80), identical `first_reached_at`, identical applied events ordering `['ev_alpha', 'ev_beta']`, and identical rank (1).

4. **`test_assign_cohort_atomic_reassignment[sqlite / in_memory]`**
   - Verifies `assign_cohort` removes old cohort entry and upserts new cohort entry in a single atomic transaction.

5. **`test_atomic_awards_with_real_domain_events[sqlite / in_memory]`**
   - Drives real domain events through `LearnerApplication` and `Learner` aggregate (`Learner.LessonCompleted`, `Learner.CohortAssigned`, `Learner.XPAwarded`).
   - Validates end-to-end integration with aggregate event sourcing.

---

## Exact Before / After Reproduction Values

| Metric / Scenario | Before Fix (Reproduced) | After Fix (Verified) |
| :--- | :--- | :--- |
| **Cohort write failure rollback** | Global score: `100`, Cohort score: `0` (partial leak persisted). Retry returned `False` (refused duplicate). | Global score: `0`, Cohort score: `0` (clean rollback). Retry returned `True`, final Global: `100`, final Cohort: `100`. |
| **Alternating awards across 2 projections (50, 60, 60, 50)** | Stored score: **`170`** (applied events sum: **`220`**, lost 50 XP). | Stored score: **`220`** (applied events sum: **`220`**, 0 XP lost). |
| **`first_reached_at` derivation after alternating awards** | Evaluated from local process cache (`2026-10-12T09:30:00Z` or incorrect). | Evaluated from store events: **`2026-10-12T10:30:00Z`** (exact match). |
| **Same-instant events arrival (1 then 2 vs 2 then 1)** | Ordering dependent on rowid/insertion; potential divergence in derived tie-breaks. | Ordering deterministically `(applied_at, stream_offset, event_id)`: both arrival orders yield identical state (score 80, `ev_alpha` then `ev_beta`). |
| **Transaction helper** | None; partial failures left connection dirty. | Reentrant `with store.transaction():` for both SQLite and InMemory stores. |
