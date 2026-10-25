# Week 4 Audit: Leaderboard Rehydrate Idempotency, Cohort Single-Row Atomicity, and Derived Rebuilds (LB-4)

## Problem

Three architectural defects in `src/leaderboard/projection.py` and `src/leaderboard/store.py` impaired rehydration determinism, cohort membership integrity across restarts, and durable audit log retention:

### Defect 1: `rehydrate()` Doubled Event History and Distorted `first_reached_at`
- **Location:** `src/leaderboard/projection.py:94-98`
- **Root Cause:**
  When loading applied event facts into memory during `rehydrate()`, the projection used:
  ```python
  self._subject_events[ev_key].append((ev["applied_at"], ev["xp_amount"], ev["event_id"]))
  ```
  Because `_subject_events` was a `defaultdict(list)`, repeated calls to `rehydrate(epoch)` continually appended duplicate tuples to existing keys instead of replacing them.
- **Exact Reproduction:**
  Consider a learner with 3 sequential XP awards:
  - 09:00:00Z: 100 XP (cumulative: 100 XP)
  - 10:00:00Z: 100 XP (cumulative: 200 XP)
  - 11:00:00Z: 50 XP (cumulative: 250 XP) -> target 250 XP first reached at **11:00:00Z**.
  Calling `rehydrate()` once populated 3 events in memory. Calling `rehydrate()` a second time appended the 3 events again (totaling 6 events).
  When deriving `first_reached_at` for 250 XP from the chronological sequence:
  - Event 1 (09:00): 100 XP
  - Event 2 (09:00 duplicate): 100 XP -> cumulative 200 XP
  - Event 3 (10:00): 100 XP -> cumulative **300 XP >= 250 XP**
  The cumulative score threshold was breached prematurely at **10:00:00Z** instead of the authentic **11:00:00Z**.
  Furthermore, `rehydrate()` allowed defaulting or guessing epochs without requiring an explicit epoch parameter.

### Defect 2: `assign_cohort()` Allowed Duplicate / Orphan Cohort Rows After Restart Without Rehydrate
- **Location:** `src/leaderboard/projection.py:326-347`
- **Root Cause:**
  `assign_cohort()` previously relied on `self._cohort_memberships.get(key)` to determine whether the learner had an existing cohort assignment that needed cleanup (`remove_member()`).
  If a process restarted or a new `LeaderboardProjection` was instantiated without calling `rehydrate()`, `self._cohort_memberships` was completely empty.
  When `assign_cohort(subject_id, cohort_beta)` was invoked:
  1. `old_cohort` evaluated to `None`.
  2. `self.store.remove_member()` for `cohort_alpha` was skipped.
  3. A new row for `cohort_beta` was inserted into `leaderboard_entries`.
  4. The primary key of `leaderboard_entries` is `(scope_type, scope_id, epoch, subject_id)`. Because `scope_id` is part of the compound primary key, both `('cohort', 'cohort_alpha', epoch, subject_id)` and `('cohort', 'cohort_beta', epoch, subject_id)` remained in the database simultaneously.
  Additionally, pre-existing orphan duplicate rows caused by partial failures or legacy writes were never cleaned up during reassignment.

### Defect 3: `rebuild()` Cleared Reconciliation Audits Alongside Derived Tables
- **Location:** `src/leaderboard/store.py:997-1005` and `src/leaderboard/projection.py:365-375`
- **Root Cause:**
  `LeaderboardProjection.rebuild()` invoked `self.clear()`, which called `self.store.clear()`.
  In `store.clear()`, if no filters were supplied, it executed:
  ```sql
  DELETE FROM leaderboard_entries;
  DELETE FROM leaderboard_applied_events;
  DELETE FROM leaderboard_checkpoints;
  DELETE FROM leaderboard_processed_offsets;
  DELETE FROM leaderboard_reconciliation_audits;
  DELETE FROM leaderboard_completion_times;
  DELETE FROM leaderboard_pending_awards;
  ```
  `leaderboard_reconciliation_audits` is an authoritative audit trail documenting reconciliation runs, divergence counts, and corrected learner IDs. Replaying domain events to rebuild derived projections inadvertently wiped all historical audit logs.

---

## Changes Made

### 1. Strictly Idempotent `rehydrate(epoch)` with Required Parameter and Safe History Replacement
- **File:** `src/leaderboard/projection.py`
  - Updated signature: `def rehydrate(self, epoch: str | Sequence[str]) -> None`.
  - Prohibits `None` or empty sequence: raises `ValueError("epoch or sequence of epochs must be explicitly passed to rehydrate(); defaulting to get_calendar_week() is prohibited")`.
  - Replaces rather than appends per-key history:
    ```python
    for k in list(self._subject_events.keys()):
        if k[1] == ep:
            self._subject_events[k] = []

    for ev in self.store.get_applied_events(epoch=ep):
        ev_key = (ev["subject_id"], ev["epoch"])
        if ev_key not in self._subject_events:
            self._subject_events[ev_key] = []
        self._subject_events[ev_key].append(
            (ev["applied_at"], ev["xp_amount"], ev["event_id"])
        )
    ```
  - Added `get_subject_events(subject_id, epoch)`: lazily loads applied events from storage on demand if absent, replacing rather than appending.
  - Added `compute_first_reached_at(subject_id, epoch, target_score)`: derives chronological threshold timestamps from stored applied events without history corruption.

### 2. Store-Backed Cohort Membership Lookup and Atomic Orphan Cleanup
- **Files:** `src/leaderboard/projection.py`, `src/leaderboard/store.py`
  - In `LeaderboardStore`:
    - Added abstract `get_member_cohorts(self, epoch: str, subject_id: str) -> list[str]`.
    - Added abstract `remove_cohort_memberships(self, epoch: str, subject_id: str, except_cohort_id: str | None = None) -> int`.
  - In `SQLiteLeaderboardStore` and `InMemoryLeaderboardStore`:
    - Implemented `get_member_cohorts` returning all cohorts assigned to a learner in that epoch.
    - Implemented `remove_cohort_memberships` removing all cohort rows for `(epoch, subject_id)` where `scope_id != except_cohort_id`.
  - In `LeaderboardProjection.assign_cohort()`:
    - Queries `old_cohort = self._cohort_memberships.get(key) or self.store.get_member_cohort(epoch, subject_id)`.
    - Inside a single atomic transaction:
      ```python
      with self.store.transaction():
          self.store.remove_cohort_memberships(
              epoch=epoch,
              subject_id=subject_id,
              except_cohort_id=cohort_id,
          )
          self.store.upsert_score(
              scope_type="cohort",
              scope_id=cohort_id,
              epoch=epoch,
              subject_id=subject_id,
              score=current_score,
              first_reached_at=first_reached,
              updated_at=assigned_at,
              watermark_offset=watermark_offset,
          )
      ```
    - Guarantees that even after a restart without rehydration, and even if orphan duplicates were present, exactly one cohort row remains for the learner.

### 3. Clear-Derived vs. Full Wipe Separation
- **Files:** `src/leaderboard/store.py`, `src/leaderboard/projection.py`
  - In `LeaderboardStore`:
    - Added `clear_derived(self) -> None`: wipes derived tables (`leaderboard_entries`, `leaderboard_applied_events`, `leaderboard_checkpoints`, `leaderboard_processed_offsets`, `leaderboard_completion_times`, `leaderboard_pending_awards`) while strictly preserving `leaderboard_reconciliation_audits`.
    - Added `wipe_all_for_tests(self) -> None`: full storage wipe including audits (strictly named and reserved for testing).
    - Added `get_reconciliation_audits(self) -> list[ReconciliationAudit]`: returns all persistent audit rows.
    - Updated `clear()`: delegates to `clear_derived()` when no scope/epoch filters are provided.
  - In `LeaderboardProjection`:
    - `clear()` calls `self.store.clear_derived()`, ensuring that in-memory state and derived projection tables are cleared while audit records survive.
    - `rebuild()` calls `self.clear()` and replays domain events; reconciliation audit logs survive completely intact.

---

## Tests Added

Comprehensive test suite in `tests/leaderboard/test_rehydrate_cohort.py` driving real `Learner` aggregates, `LearnerApplication`, and domain events:

1. **`test_rehydrate_idempotency_and_first_reached_at`**
   - Applies 3 real domain events (100 XP at 09:00:00Z, 100 XP at 10:00:00Z, 50 XP at 11:00:00Z; total 250 XP).
   - Rehydrates on fresh projection: snapshot taken (`first_reached_at == "2026-10-12T11:00:00Z"`, len=3).
   - Rehydrates a 2nd time: verifies in-memory state is identical to snapshot 1, events count remains 3 (not 6), and `compute_first_reached_at` remains `11:00:00Z` (not `10:00:00Z`).
   - Rehydrates 5 times: verifies state remains strictly identical.
   - Verifies calling `rehydrate(None)` or `rehydrate([])` raises `ValueError`.

2. **`test_reassign_cohort_after_restart_without_rehydrate`**
   - Drives real learner aggregate assigned to `cohort_alpha` with 150 XP.
   - Simulates process restart: instantiates a new `LeaderboardProjection` without calling `rehydrate()`.
   - Reassigns learner to `cohort_beta`.
   - Verifies that `store.get_member_cohorts()` returns exactly `["cohort_beta"]` (len=1), `cohort_alpha` is deleted, and 150 XP carries forward to `cohort_beta`.
   - Applies a subsequent `CohortAssigned` domain event reassigning to `cohort_gamma`; verifies single row `["cohort_gamma"]`.

3. **`test_reassign_cohort_purges_preexisting_orphan_duplicates`**
   - Injects multiple orphan cohort entries (`cohort_orphan_1`, `cohort_orphan_2`, `cohort_orphan_3`) for a learner.
   - Calls `assign_cohort(..., cohort_id="cohort_target", ...)`.
   - Verifies all orphan rows are purged in the single atomic transaction, leaving exactly `["cohort_target"]`.

4. **`test_rebuild_preserves_audits_and_matches_fresh_replay`**
   - Stores 2 durable `ReconciliationAudit` records.
   - Plays a sequence of real domain events across learners and scopes.
   - Calls `proj.rebuild(events)`.
   - Verifies all `ReconciliationAudit` records survived intact with identical values.
   - Verifies derived tables (`leaderboard_entries`, applied events, processed offsets, checkpoints) match fresh replay exactly.

5. **`test_store_clear_derived_vs_wipe_all_for_tests[SQLiteLeaderboardStore / InMemoryLeaderboardStore]`**
   - Verifies `store.clear_derived()` wipes entries but preserves audits.
   - Verifies `store.wipe_all_for_tests()` clears audits.

---

## Exact Before / After Reproduction Values

| Scenario / Metric | Before Fix (Reproduced) | After Fix (Verified) |
| :--- | :--- | :--- |
| **`rehydrate()` called twice (100 XP at 09:00, 100 XP at 10:00, 50 XP at 11:00)** | In-memory events doubled (3 -> 6); `compute_first_reached_at(250)` returned **`10:00:00Z`** (due to 100+100+100=300 >= 250 at 10:00). | In-memory events replaced (len remains **`3`**); `compute_first_reached_at(250)` returns **`11:00:00Z`**. |
| **`rehydrate()` called 5 times** | In-memory events grew to 15 entries (tripling/quintupling memory usage). | In-memory events stay at exactly **`3`** entries; snapshots identical across all 5 runs. |
| **`rehydrate()` without epoch parameter** | Defaulted silently to `get_calendar_week()` or dynamic queries. | Raises `ValueError` requiring explicit epoch parameter; guessing prohibited. |
| **Cohort reassignment after restart without `rehydrate()`** | In-memory map empty; old cohort row not deleted. Store contained **`2`** cohort rows (`cohort_alpha` AND `cohort_beta`). | Store looked up via `get_member_cohort()`; store contains exactly **`1`** cohort row (`cohort_beta`). |
| **Orphan duplicate cohort rows present before reassignment** | Multiple rows persisted indefinitely in `leaderboard_entries`. | All orphan rows atomically purged; store contains exactly **`1`** cohort row (`cohort_target`). |
| **Reconciliation audits after `projection.rebuild()`** | Audits wiped (`count = 0`) because `store.clear()` deleted `leaderboard_reconciliation_audits`. | All audit records survive untouched (`count = 2`, identical audit IDs, metrics, and timestamps). |
| **`store.clear_derived()` vs `store.wipe_all_for_tests()`** | Single `clear()` function wiped both derived models and audit records. | `clear_derived()` leaves audit logs intact; `wipe_all_for_tests()` strictly reserved for test wipes. |
