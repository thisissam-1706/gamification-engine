# Week 4 Audit: Notification Release, Replay & Quiet Hours Precision (M5-N2)

## Problem

A series of state retention, queue race condition, and quiet-window calculation bugs were identified in `src/notifications`:

### Bug 1: Dropped Template, TTL, and Original Timestamp on Release & Replay
- **Location:** `src/notifications/store.py:72-98`, `src/notifications/dispatcher.py:384-405, 626-634`
- **Root Cause:**
  `notification_rescheduled` and `notification_dlq` did not have columns for `template_id` or `ttl_seconds`. Furthermore, `notification_dlq` stored only `failed_at`, losing the original proposal `created_at`.
  When `release_rescheduled()` or `replay_dlq()` reconstructed proposals, both fell back to `template_id="default_template"` and `ttl_seconds=86400`. Custom templates (e.g. `"custom_streak_gold_v2"`) and short or extended TTLs were permanently lost upon queueing or routing to DLQ.

### Bug 2: Missing Quiet Hour Recheck on Release & Missing Timezone Handling
- **Location:** `src/notifications/dispatcher.py:384-440`
- **Root Cause:**
  `release_rescheduled()` previously only checked consent, TTL, and fatigue cap. It never re-evaluated quiet hours with the learner's current timezone. If a learner changed timezones (e.g. traveled from UTC to New York UTC-4), a message released at 07:00 UTC landed at 03:00 local time in New York—violating quiet hours. Furthermore, invalid timezone strings and missing preferences were not handled with explicit suppressions.

### Bug 3: Premature Release Marking Before Delivery / Terminal Fact
- **Location:** `src/notifications/dispatcher.py:388-390`
- **Root Cause:**
  `release_rescheduled()` invoked `self.store.mark_rescheduled_released(proposal_id)` at the start of the loop **before** evaluating guardrails or attempting adapter delivery.
  If the host process crashed mid-release (during guardrail checks or adapter network I/O), `released = 1` had already been committed to SQLite. On restart, `get_due_rescheduled()` filtered for `released = 0`, permanently abandoning the proposal with no dispatch, suppression, or DLQ terminal fact.

### Bug 4: DLQ Replay Flaws (Double-Replay, Lost TTL, Reused Event IDs)
- **Location:** `src/notifications/dispatcher.py:610-770`
- **Root Cause:**
  1. `replay_dlq()` did not check `entry["resolved"]`, allowing already-resolved DLQ entries to be replayed repeatedly and re-sent over the network.
  2. TTL was checked against `failed_at` instead of original `created_at`, so stale proposals whose lifespan expired while in the queue were delivered anyway.
  3. Quiet hours were never evaluated during replay.
  4. Unsupported channel adapters returned an in-memory `TerminalFact` without recording a suppression row in SQLite.
  5. DLQ resolution was performed in a separate transaction from `record_dispatch`.
  6. Failed replays reused `event_id=dlq_id`, colliding with the existing DLQ entry identifier.

### Bug 5: Next-Day Calculation in Non-Midnight-Spanning Quiet Windows
- **Location:** `src/notifications/guardrails.py:84-89`
- **Root Cause:**
  `evaluate_quiet_hours()` calculated the target release date with:
  ```python
  if local_dt.hour >= quiet_start_hour:
      next_date = local_dt.date() + timedelta(days=1)
  else:
      next_date = local_dt.date()
  ```
  For quiet windows that do not span midnight (e.g. 13:00 to 15:00): at 13:30, `13 >= 13` evaluated to True, scheduling release for **tomorrow** at 15:00 (25.5 hours later) instead of today at 15:00 (1.5 hours later).

---

## Changes Made

### 1. Database Schema & Safe Automated Migration
- **File:** `src/notifications/store.py`
  - Added columns `template_id TEXT DEFAULT 'default_template'` and `ttl_seconds INTEGER DEFAULT 86400` to `notification_rescheduled`.
  - Added columns `created_at TEXT DEFAULT ''`, `template_id TEXT DEFAULT 'default_template'`, and `ttl_seconds INTEGER DEFAULT 86400` to `notification_dlq`.
  - Added `_migrate_schema()` called during `_init_db()`: inspects existing tables using `PRAGMA table_info` and executes safe, idempotent `ALTER TABLE ADD COLUMN` statements for pre-existing databases.
  - Updated `record_rescheduled()`, `record_dlq()`, `get_due_rescheduled()`, `get_rescheduled_entry()`, `get_dlq_entry()`, and `get_dlq_entries()` to persist and retrieve all metadata columns.

### 2. Quiet Hours Rechecking & Timezone Handling on Release
- **File:** `src/notifications/dispatcher.py`
  - In `release_rescheduled()`, resolves current preferences from `prefs_provider`.
  - If preferences are missing: records suppression with `reason="missing_preferences"`.
  - Evaluates `evaluate_quiet_hours(now_dt, prefs.timezone, ...)`:
    - If timezone is invalid: records suppression with `reason="invalid_timezone"`.
    - If learner is currently inside quiet hours (e.g. due to travel or shifted window): re-queues via `record_rescheduled()` with the new `rescheduled_for` timestamp and emits `NotificationStatus.QUEUED`.

### 3. Single-Transaction Atomic Release
- **Files:** `src/notifications/store.py`, `src/notifications/dispatcher.py`
  - Removed premature `self.store.mark_rescheduled_released(proposal_id)` from the beginning of `release_rescheduled()`.
  - Added `release_proposal_id: str | None = None` parameter to `record_dispatch()`, `record_suppression()`, and `record_dlq()`.
  - Setting `released = 1` now executes inside the same `with self.connection:` block as the terminal fact insertion and proposal status update.
  - If a crash occurs before delivery/fact commit, `released` remains 0; on recovery, `get_due_rescheduled()` re-processes the candidate without message loss.

### 4. Comprehensive DLQ Replay Safeguards
- **File:** `src/notifications/dispatcher.py`
  - If `entry.get("resolved")`: raises `ValueError(f"DLQ entry '{dlq_id}' has already been resolved.")`.
  - Reconstructs proposal using original `created_at`, `template_id`, and `ttl_seconds`.
  - Checks TTL against original `created_at` (`is_ttl_expired`), suppressing stale entries.
  - Rechecks quiet hours, re-queueing to `notification_rescheduled` if inside.
  - Records SQLite suppression when channel adapter is unsupported.
  - On delivery success: atomically marks DLQ entry resolved in `record_dispatch(..., resolve_dlq_id=dlq_id)`.
  - On delivery failure: generates a distinct terminal event ID `f"replay_fail_{uuid.uuid4().hex[:12]}"` rather than reusing `dlq_id`.

### 5. Fixed Same-Day End Hour for Non-Midnight Quiet Windows
- **File:** `src/notifications/guardrails.py`
  - Corrected `next_date` calculation in `evaluate_quiet_hours()`:
    ```python
    if quiet_start_hour > quiet_end_hour:
        # Spans midnight (e.g. 22:00 to 07:00)
        if local_dt.hour >= quiet_start_hour:
            next_date = local_dt.date() + timedelta(days=1)
        else:
            next_date = local_dt.date()
    else:
        # Does not span midnight (e.g. 13:00 to 15:00)
        next_date = local_dt.date()
    ```

---

## Tests Added

Added 7 targeted integration tests in `tests/notifications/test_release_replay.py`:

1. **`test_short_ttl_message_suppressed_on_release`**
   - Proposal created at 23:00 IST with 8.5-hour TTL (queued for 07:00 IST release).
   - Release sweep runs at 08:00 IST (9 hours after creation).
   - Asserts `release_rescheduled()` suppresses candidate with `reason="ttl_expired"` and 0 delivered messages.
2. **`test_release_during_changed_timezone_quiet_hours_requeued`**
   - Proposal queued for UTC learner at 22:30 UTC for 07:00 UTC release.
   - Learner updates timezone to `America/New_York` (03:00 local time at 07:00 UTC).
   - Asserts candidate is re-queued with `rescheduled_for="2026-10-13T11:00:00Z"` (07:00 NY time) and `released` remains False.
3. **`test_replaying_resolved_dlq_entry_twice_sends_once`**
   - DLQ entry replayed successfully; marked resolved.
   - Second replay raises `ValueError("already been resolved")`.
   - Asserts adapter sent message exactly once across both calls.
4. **`test_stale_dlq_entry_suppressed_on_replay`**
   - Proposal with 1-hour TTL routed to DLQ.
   - Replayed 3 hours later.
   - Asserts suppression with `reason="ttl_expired"` and 0 delivered messages.
5. **`test_template_and_ttl_survive_release_and_replay`**
   - Asserts delivered proposals from `release_rescheduled()` and `replay_dlq()` carry stored `template_id` (`"custom_streak_gold_v2"`, `"quest_complete_celebration_v5"`) and `ttl_seconds` (`72000`, `172800`).
6. **`test_simulated_crash_mid_release_does_not_lose_message`**
   - Adapter crashes during `release_rescheduled()`.
   - Verifies `released` remains False in SQLite.
   - Restarted dispatcher re-processes and successfully delivers proposal.
7. **`test_non_midnight_spanning_quiet_window_computes_correct_next_day`**
   - Quiet window 13:00 to 15:00 UTC evaluated at 13:30 UTC.
   - Asserts `next_allowed` is on the **same day** at 15:00 UTC.

---

## Before / After Comparison

| Scenario | Before | After |
| :--- | :--- | :--- |
| **Custom template/TTL on release & replay** | Rebuilt proposals fell back to `"default_template"` and `ttl_seconds=86400`. | Original `template_id`, `ttl_seconds`, and `created_at` stored in SQLite and preserved on release & replay. |
| **Learner changes timezone while queued** | Released at original time regardless of local quiet hours in new timezone. | Rechecks quiet hours with current timezone; re-reschedules for local morning if inside quiet hours. |
| **Crash during `release_rescheduled`** | `released = 1` committed before processing; crash permanently dropped proposal. | `released = 1` committed atomically with terminal fact; message survives crash and delivers on restart. |
| **Replaying resolved DLQ entry** | Allowed indefinite replays, triggering duplicate external sends. | Checks `entry["resolved"]` and refuses replay with `ValueError`; sends exactly once. |
| **Stale DLQ entry replay** | TTL checked against `failed_at` (or ignored); expired proposals delivered. | Checked against original `created_at`; stale proposals suppressed with `ttl_expired`. |
| **Failed DLQ replay terminal event ID** | Reused original `dlq_id` as the event ID, causing event collisions. | Generates fresh unique event ID `f"replay_fail_{uuid}"`. |
| **Quiet window 13:00 to 15:00 at 13:30** | Scheduled release for tomorrow at 15:00 (25.5 hours delay). | Schedules release for today at 15:00 (1.5 hours delay). |
