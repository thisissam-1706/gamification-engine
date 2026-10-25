# Week 4 Audit: Leaderboard Identity Collision and Missing Time Fallbacks (LB-1)

## Problem

Two critical correctness bugs were identified in `src/leaderboard/projection.py` and its interaction with `src/leaderboard/store.py`:

### Bug 1: Applied-Event Key Collision on `XPAwarded`
- **Location:** `src/leaderboard/projection.py:114-125` and `src/leaderboard/store.py:186-194`
- **Root Cause:**
  When projecting an `XPAwarded` event, `projection.py` derived the deduplication tracking key solely from `domain_event.reward_id`:
  ```python
  event_id = getattr(domain_event, "reward_id", None)
  if not event_id:
      event_id = f"{subject_id}:{domain_event.originator_version}"
  ```
  In the domain aggregate (`Learner.complete_lesson`), `reward_id` is defined as `f"xp:{completion_id}"`. When two different learners complete a lesson with the same `completion_id` (e.g. `"lesson_101"`), both emit `reward_id="xp:lesson_101"`.
  In `store.py`, the `leaderboard_applied_events` table defined:
  ```sql
  CREATE TABLE IF NOT EXISTS leaderboard_applied_events (
      event_id TEXT PRIMARY KEY,
      subject_id TEXT NOT NULL,
      ...
  )
  ```
  When Learner 1's XP was applied, `event_id="xp:lesson_101"` was stored. When Learner 2's event arrived, `is_event_applied("xp:lesson_101")` returned `True` (or SQLite raised `IntegrityError` on the single-column primary key). Consequently, Learner 2 was incorrectly dropped as a duplicate and received 0 XP on the leaderboard.

### Bug 2: Missing `occurred_at` Fallback to `datetime.now()`
- **Location:** `src/leaderboard/projection.py:15-16, 106-111` and `src/leaderboard/cohorts.py:25-26`
- **Root Cause:**
  Domain event `Learner.XPAwarded` does not carry an `occurred_at` timestamp directly; only the preceding `Learner.LessonCompleted` event does.
  Previously, `projection.py` only cached `completion_id -> occurred_at` in an in-memory dictionary `self._completion_occurred_at`. If a service restarted or a projection was rehydrated between `LessonCompleted` and `XPAwarded`:
  1. `self._completion_occurred_at` was empty.
  2. `raw_occ` evaluated to `None`.
  3. `normalize_to_utc_iso(None)` fell back silently to `datetime.now(timezone.utc)`:
     ```python
     if ts is None:
         dt = datetime.now(timezone.utc)
     ```
  4. Similarly, `get_calendar_week(None)` fell back to `datetime.now(timezone.utc)`.
  As a result, a lesson completed weeks ago in an earlier epoch (or during offline reconciliation) was placed into the *current* calendar week upon restart, polluting leaderboard results. If no timestamp could be found, the system guessed rather than asserting determinism.

---

## Changes Made

### 1. Unique Applied-Event Key Including Learner ID
- **File:** `src/leaderboard/projection.py`
  - In `apply()` for `Learner.XPAwarded`, `event_id` is now explicitly prefixed with `subject_id`:
    ```python
    reward_id = getattr(domain_event, "reward_id", None)
    if reward_id:
        if reward_id.startswith(f"{subject_id}:"):
            event_id = reward_id
        else:
            event_id = f"{subject_id}:{reward_id}"
    else:
        event_id = f"{subject_id}:{domain_event.originator_version}"
    ```
  - In `record_xp()`, deduplication calls `self.store.is_event_applied(event_id, subject_id=subject_id)`.

### 2. Compound Primary Key and Safe Schema Migration in Store
- **File:** `src/leaderboard/store.py`
  - `leaderboard_applied_events` now defines a compound primary key:
    ```sql
    PRIMARY KEY (event_id, subject_id)
    ```
  - Added `_migrate_schema()` to `SQLiteLeaderboardStore.__init__()`:
    Detects legacy tables with single-column `event_id TEXT PRIMARY KEY`, copies data into `leaderboard_applied_events_v2` with `PRIMARY KEY (event_id, subject_id)`, drops the old table, and renames `v2` to `leaderboard_applied_events`. Existing records are safely preserved without data loss.
  - Updated `is_event_applied(event_id, subject_id=None)` in both `SQLiteLeaderboardStore` and `InMemoryLeaderboardStore` to support subject-scoped lookup.

### 3. SQLite Persistence for Completion Timestamps
- **File:** `src/leaderboard/store.py` and `src/leaderboard/projection.py`
  - Added `leaderboard_completion_times` table:
    ```sql
    CREATE TABLE IF NOT EXISTS leaderboard_completion_times (
        completion_id TEXT NOT NULL,
        learner_id TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        PRIMARY KEY (completion_id, learner_id)
    )
    ```
  - Added `record_completion_time(completion_id, learner_id, occurred_at)` and `get_completion_time(completion_id, learner_id)` to `LeaderboardStore`, `SQLiteLeaderboardStore`, and `InMemoryLeaderboardStore`.
  - In `projection.apply(Learner.LessonCompleted)`: writes `completion_id -> (learner_id, occurred_at)` into SQLite durable storage.
  - In `projection.apply(Learner.XPAwarded)`: looks up `raw_occ` from in-memory cache and falls back to `store.get_completion_time(completion_id, learner_id=subject_id)` if restarting.

### 4. Removal of `datetime.now` Fallbacks & Parking as `"pending_time"`
- **File:** `src/leaderboard/projection.py`, `src/leaderboard/cohorts.py`, `src/leaderboard/store.py`
  - Removed `datetime.now()` fallback in `normalize_to_utc_iso()`: raises `ValueError("Timestamp cannot be None; guessing with datetime.now is prohibited")`.
  - Removed `datetime.now()` fallback in `get_calendar_week()`: requires `dt_value: str | datetime`, raises `ValueError` if `None`.
  - Added `leaderboard_pending_awards` table in SQLite and `record_pending_award()`, `get_pending_awards()`, `delete_pending_award()` in store.
  - If `raw_occ` is `None` when `XPAwarded` arrives:
    - The award is persisted in SQLite with `status="pending_time"`.
    - If `raise_on_missing_time=True`: raises `MissingTimestampError`.
    - If `raise_on_missing_time=False`: returns status string `"pending_time"`.
    - The award **never** lands in the current week.
  - When `LessonCompleted` arrives later for a parked award, `projection.apply()` automatically resolves the pending award and records it into the correct historical epoch.
  - In `projection.rehydrate()`: replaces `get_calendar_week()` with `self.store.get_all_epochs()` to load all distinct historical epochs from the database without guessing.
  - In `store.set_committed_offset()`: accepts explicit `updated_at: str = ""` instead of calling `datetime.now()`.

---

## Tests Added

Added 4 targeted integration tests to `tests/leaderboard/test_leaderboard_projection.py`:

1. **`test_two_learners_with_same_completion_id_both_score`**
   - Two distinct learners complete the exact same `completion_id` (`"shared_lesson_101"`).
   - Domain events are replayed through `projection.apply()`.
   - Asserts both learners score 100 XP on global and cohort leaderboards without key collisions or dropped events.

2. **`test_restart_between_lesson_completed_and_xp_awarded_lands_in_right_epoch`**
   - Learner completes a lesson in historical epoch `2026-W42` (`2026-10-12T09:00:00Z`).
   - `projection1.apply(LessonCompleted)` runs, persisting completion time in SQLite.
   - `store1.close()` and process restart simulated (fresh `projection2` with empty in-memory maps).
   - `projection2.apply(XPAwarded)` runs.
   - Asserts the XP lands in `2026-W42`, read back from SQLite, with 0 entries in current week.

3. **`test_missing_time_never_lands_in_current_week`**
   - Learner completes a lesson with no timestamp (`occurred_at=None`).
   - `projection.apply(xp_awarded, raise_on_missing_time=False)` returns `"pending_time"`.
   - `projection.apply(xp_awarded, raise_on_missing_time=True)` raises `MissingTimestampError`.
   - Asserts query for current calendar week returns 0 entries (no XP awarded to current week).
   - Asserts award is parked in SQLite `leaderboard_pending_awards` with `status="pending_time"`.

4. **`test_sqlite_applied_events_safe_schema_migration`**
   - Creates a legacy SQLite database containing `leaderboard_applied_events` with single-column PK `event_id TEXT PRIMARY KEY` and pre-existing rows.
   - Opens the database with `SQLiteLeaderboardStore`.
   - Verifies legacy rows are preserved intact and schema is upgraded to compound `(event_id, subject_id)` PK.
   - Verifies that two distinct learners with the same `event_id` can now be applied without collision.

---

## Before / After Comparison

| Scenario | Before | After |
| :--- | :--- | :--- |
| **Two learners complete same `completion_id`** | Key was `"xp:{completion_id}"`. Second learner rejected as duplicate; XP dropped. | Key is `"{learner_id}:xp:{completion_id}"` with compound PK `(event_id, subject_id)`. Both learners score. |
| **Service restarts between `LessonCompleted` and `XPAwarded`** | In-memory map lost. `normalize_to_utc_iso(None)` fell back to `datetime.now()`, placing past XP in current week. | `completion_id -> (learner_id, occurred_at)` read back from SQLite. XP lands in correct historical epoch. |
| **Orphan `XPAwarded` with missing timestamp** | Silently guessed current week using `datetime.now()`. | Award is parked as `"pending_time"` in SQLite; returns status `"pending_time"` or raises `MissingTimestampError`. Current week untouched. |
| **Rehydrate projection state without specifying epoch** | Called `get_calendar_week()` (current week only), ignoring past active epochs. | Calls `store.get_all_epochs()` to load all distinct historical epochs present in storage. |
| **Existing database schema** | Single-column `event_id PRIMARY KEY`. | Migrated automatically and safely to compound `(event_id, subject_id) PRIMARY KEY` without data loss. |
