# Week 4 Audit: Leaderboard Contiguous Checkpointing and Watermark Lag (LB-2)

## Problem

Prior to this fix, two significant checkpoint and watermark defects existed in `src/leaderboard/store.py` and `src/leaderboard/projection.py`:

### Defect 1: Committed Offset Counted Only Applied XP Awards
- **Location:** `src/leaderboard/store.py:474-515` (SQLite) and `src/leaderboard/store.py:1160-1174` (InMemory)
- **Root Cause:**
  Contiguous committed offset calculation queried `stream_offset FROM leaderboard_applied_events WHERE stream_offset > ?`. Because `leaderboard_applied_events` only stores applied XP awards, all non-XP events (`LessonCompleted`, `CohortAssigned`, `StreakUpdated`, `Created`, duplicate deliveries, and un-timed awards parked as `pending_time`) never recorded an offset.
  Consequently, in any event stream where non-XP events preceded or interleaved with XP awards, a gap was perceived:
  - For example, in a 10-event stream with XP awards at offsets 3, 6, and 9 (where offsets 1, 2, 4, 5, 7, 8, 10 are non-XP events), `leaderboard_applied_events` only contained offsets `[3, 6, 9]`.
  - When evaluating contiguous offsets starting from 1, offset 1 was missing.
  - The loop broke immediately, leaving the committed offset stuck at **`0`**, even though all 10 events had been fully processed.

### Defect 2: Running `max()` Watermark Contaminated Late-Arriving Event Offsets
- **Location:** `src/leaderboard/projection.py:112-115` and `src/leaderboard/projection.py:211`
- **Root Cause:**
  `LeaderboardProjection.apply()` mutated a running watermark attribute using `self._watermark = max(self._watermark, watermark_offset)` and passed `watermark_offset=self._watermark` to `record_xp()`, `assign_cohort()`, and `record_pending_award()`.
  If an out-of-order stream arrived (for example, event at offset 5 arrived before event at offset 3):
  1. Offset 5 set `self._watermark = 5`.
  2. Late offset 3 arrived. `max(5, 3)` evaluated to `5`.
  3. Offset 3 was passed down as `watermark_offset = 5` and permanently recorded in `leaderboard_applied_events` as offset **`5`** instead of its own offset **`3`**.
  4. Additionally, `apply()` previously fell back to `domain_event.originator_version` if no offset was provided, conflating aggregate-local entity versions with global notification-log positions.

---

## Changes Made

### 1. In-Transaction `processed_offsets` Tracking across SQLite and InMemory Stores
- **File:** `src/leaderboard/store.py`
  - Added abstract methods to `LeaderboardStore`:
    - `is_offset_processed(self, offset: int) -> bool`
    - `record_processed_offset(self, offset: int, cohort_id: str | None = None, occurred_at: str = "") -> int`
  - **SQLite Store:**
    - Added table `leaderboard_processed_offsets (stream_offset INTEGER PRIMARY KEY, processed_at TEXT NOT NULL DEFAULT '')`.
    - Added safe schema migration in `_migrate_schema()`: creates `leaderboard_processed_offsets` and idempotently backfills offsets from existing `leaderboard_applied_events` and `leaderboard_checkpoints`.
    - `record_processed_offset()` inserts the offset into `leaderboard_processed_offsets` inside the active transaction, computes the highest contiguous offset starting from 1 via `WHERE stream_offset > current_committed ORDER BY stream_offset ASC`, and updates `leaderboard_checkpoints` for both `global` and `f"cohort:{cohort_id}"`.
    - `clear()` deletes from `leaderboard_processed_offsets`.
  - **InMemory Store:**
    - Added `self._processed_offsets: set[int] = set()`.
    - Added snapshot and rollback of `self._processed_offsets` in `transaction()`.
    - `record_processed_offset()` adds to the set and advances the contiguous checkpoint via `while (current + 1) in self._processed_offsets: current += 1`.
    - `clear()` clears `self._processed_offsets`.

### 2. Removal of `max()` Shortcuts and Authoritative Watermark Lag Reporting
- **Files:** `src/leaderboard/store.py`, `src/leaderboard/service.py`
  - Removed all shortcuts and row-level fallbacks to `MAX(leaderboard_entries.watermark_offset)`.
  - `get_watermark()` strictly queries the contiguous committed checkpoint from `leaderboard_checkpoints` (and in-memory `_checkpoints`).
  - `LeaderboardService.check_watermark_lag()` compares `get_watermark()` against `required_watermark`, reporting accurate explicit `lag = max(0, required - current)` and `is_consistent = current >= required`.

### 3. Required Notification-Log Positions in `apply()` and Deterministic `rebuild()`
- **File:** `src/leaderboard/projection.py`
  - `apply(self, domain_event: Any, offset: int, ...)`:
    - `offset` is a required argument representing each event's own global notification-log position (also accepts `watermark_offset` for backwards compatibility; raises `TypeError` if neither is provided).
    - Removed `self._watermark = max(...)` running watermark mutation.
    - Removed `domain_event.originator_version` offset fallback.
    - Events pass their own exact `real_offset` to all underlying store methods.
  - In `rebuild(self, domain_events: Iterable[Any])`:
    - Replays events with real positions: unwraps `(pos, event)` tuples, `Notification.id`, or 1-indexed stream positions, passing real offsets to `self.apply(event, offset=pos)`.

### 4. Idempotent Duplicate Delivery Handling
- **File:** `src/leaderboard/projection.py`
  - At the entry of `apply()`:
    ```python
    if self.store.is_offset_processed(real_offset):
        return "duplicate"
    ```
  - A duplicate delivery of an already-processed offset is an immediate no-op that returns `"duplicate"` and cannot move the committed checkpoint backwards.

### 5. Unified Single-Transaction Boundary
- **File:** `src/leaderboard/projection.py`
  - Every consumed event (`LessonCompleted`, `CohortAssigned`, `XPAwarded`, `pending_time` parked awards, duplicate events, and ignored types) executes its effect and records its offset in `processed_offsets` inside `with self.store.transaction():`.
  - For un-timed `XPAwarded` with `raise_on_missing_time=True`: commits `record_pending_award` and `record_processed_offset` inside the transaction before raising `MissingTimestampError`.

---

## Tests Added

Comprehensive test suite in `tests/leaderboard/test_checkpoint_offsets.py` (all tests driving real `Learner` aggregates, `LearnerApplication`, and `LeaderboardService` public APIs):

1. **`test_rebuilding_10_event_stream_with_xp_at_3_6_9_ends_with_committed_offset_10[sqlite / in_memory]`**
   - Drives real domain events from a `Learner` completing 3 lessons (producing exactly 10 domain events: Created, LessonCompleted, XPAwarded, StreakUpdated, LessonCompleted, XPAwarded, StreakUpdated, LessonCompleted, XPAwarded, StreakUpdated).
   - Rebuilds the projection from genesis.
   - Verifies the committed offset reaches **`10`** (previously remained stuck at `0`).

2. **`test_out_of_order_arrival_stays_at_checkpoint_until_gaps_fill[sqlite / in_memory]`**
   - Drives real aggregate events with arrival sequence: 1, 2, 5 (missing 3, 4).
   - Verifies checkpoint remains at **`2`**.
   - Redelivers offset 5 (duplicate): verifies returns `"duplicate"` and checkpoint remains at **`2`**.
   - Applies late offset 3: checkpoint advances to **`3`**.
   - Applies late offset 4: contiguous prefix 1..5 is complete; checkpoint jumps to **`5`**.
   - Redelivers offset 2: verifies checkpoint does not move backwards and remains at **`5`**.

3. **`test_late_offset_arriving_after_higher_is_recorded_under_own_offset[sqlite / in_memory]`**
   - Higher offset 5 arrives first, followed by late offset 3.
   - Verifies late event is stored under `stream_offset == 3` (previously contaminated by `max()` and recorded as `5`).

4. **`test_checkpoint_survives_restart`**
   - Applies events 1..3 on SQLite store instance 1. Checkpoint is 3.
   - Closes store 1 to simulate process termination.
   - Instantiates brand new `SQLiteLeaderboardStore` and `LeaderboardProjection` on the same SQLite file.
   - Verifies checkpoint and `_watermark` are restored to **`3`**.
   - Applies event 4 on the restarted projection: checkpoint advances cleanly to **`4`**.

5. **`test_check_watermark_lag_reports_lag_during_gap_and_consistent_after_fills[sqlite / in_memory]`**
   - Drives events with gap: 1, 2, 5.
   - Verifies `check_watermark_lag` reports `current_watermark = 2`, `required_watermark = 5`, `is_consistent = False`, `lag = 3`.
   - Fills gap with events 3 and 4.
   - Verifies `check_watermark_lag` reports `current_watermark = 5`, `required_watermark = 5`, `is_consistent = True`, `lag = 0`.

---

## Exact Before / After Reproduction Values

| Metric / Scenario | Before Fix (Reproduced) | After Fix (Verified) |
| :--- | :--- | :--- |
| **10-event rebuild with XP at offsets 3, 6, 9** | Committed offset: **`0`** (stuck at 0 because non-XP offsets 1, 2 were missing from applied XP events). | Committed offset: **`10`** (all 10 consumed events recorded in `processed_offsets`). |
| **Late offset 3 arriving after offset 5** | Late event recorded with `stream_offset = 5` (polluted by running `max()` watermark). | Late event recorded with `stream_offset = 3` (event's own notification-log position preserved). |
| **Out-of-order arrival (1, 2, 5, then 3, 4)** | Offset 5 advanced running watermark to 5; gap went undetected. | Checkpoint stays at **`2`** until 3 arrives (advances to 3) and 4 arrives (jumps to **`5`**). |
| **Duplicate delivery of offset 5 during gap** | Re-executed event logic or shifted internal state. | Immediate no-op returning `"duplicate"`, checkpoint remains at **`2`** (never moves backwards). |
| **Redelivery of offset 2 after checkpoint reached 5** | Risk of checkpoint regression or state inconsistency. | Immediate no-op returning `"duplicate"`, checkpoint remains at **`5`**. |
| **Watermark lag during gap (required=5, events 1, 2, 5)** | Reported lag 0 or inconsistent fallback. | Explicit lag reported: `current_watermark = 2`, `lag = 3`, `is_consistent = False`. |
| **Watermark lag after gap filled (events 3, 4 arrive)** | Lag stayed stuck or unreported. | `current_watermark = 5`, `lag = 0`, `is_consistent = True`. |
| **Checkpoint restart persistence** | Memory cache lost on restart; relied on partial XP scans. | Checkpoint preserved at **`3`** across store close/reopen; advances to **`4`** on next event. |
