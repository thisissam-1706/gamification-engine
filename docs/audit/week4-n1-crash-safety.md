# Week 4 Audit: Notification Crash Safety, Transaction Atomicity & Clock Determinism (M5-N1)

## Problem

A series of crash-safety, state machine, and nondeterminism defects were identified in `src/notifications`:

### Bug 1: Unhandled Adapter Exceptions and Lost Messages
- **Location:** `src/notifications/queue.py:deliver_with_retry` and `src/notifications/dispatcher.py:270-346`
- **Root Cause:**
  `RetryScheduler.deliver_with_retry` previously only caught `ProviderError`. If an adapter raised any other exception (such as `RuntimeError`, `ConnectionResetError`, `TimeoutError`, or `KeyError`), the exception escaped unhandled out of `dispatch()`.
  Because `register_proposal` was called immediately before delivery without a terminal fact, the proposal ID remained present in SQLite. When a recovery process re-dispatched the proposal, `is_proposal_seen` returned `True` and suppressed it as `duplicate_proposal`. The message was permanently lost, and no DLQ row was ever written.

### Bug 2: Suppressed, Queued, and Invalid-Timezone Proposals Unregistered
- **Location:** `src/notifications/dispatcher.py:80-267` and `src/notifications/store.py:200-250`
- **Root Cause:**
  `register_proposal` was only invoked at line 270 (immediately prior to adapter delivery). Guardrail suppressions (missing preferences, channel opt-out, category opt-out, fatigue cap exceeded, expired TTL, invalid timezone) and quiet-hour reschedules exited before line 270.
  Consequently, `notification_proposals` never tracked suppressed or queued candidates. A replay of a previously suppressed proposal after consent was restored re-evaluated the proposal and delivered it. Furthermore, duplicate suppressions repeatedly inserted redundant rows into `notification_suppressions`.

### Bug 3: Wall-Clock Dependency in Guardrails and Dispatch
- **Location:** `src/notifications/dispatcher.py:59-63, 357-361, 607-611`
- **Root Cause:**
  `dispatch()`, `release_rescheduled()`, and `replay_dlq()` fell back to `datetime.now(timezone.utc)` whenever `now_utc` was omitted. When tests ran between 22:00 UTC and 07:00 UTC (quiet hours for default UTC learner preferences), default dispatches unexpectedly returned `NotificationStatus.QUEUED` instead of `NotificationStatus.DISPATCHED`. Five existing test suites failed non-deterministically based solely on the time of day.

### Bug 4: Rescheduled `ON CONFLICT` Resetting `released` Flag
- **Location:** `src/notifications/store.py:362-365`
- **Root Cause:**
  `record_rescheduled` contained the clause:
  ```sql
  ON CONFLICT(proposal_id) DO UPDATE SET
      rescheduled_for = excluded.rescheduled_for,
      released = 0
  ```
  If a proposal had already been released by `release_rescheduled` (setting `released = 1`), any subsequent re-dispatch or update reset `released = 0`, corrupting queue state and causing duplicate delivery on subsequent release sweeps.

### Bug 5: Multi-Statement Atomicity Gaps
- **Location:** `src/notifications/store.py` (`record_dispatch`, `record_suppression`, `record_dlq`, `record_rescheduled`)
- **Root Cause:**
  Terminal facts and proposal status updates were executed across separate transactions or without transaction context managers. A failure or crash between statements left inconsistent state across `notification_proposals` and terminal tables.

---

## Changes Made

### 0. Deterministic Clock Requirement & Injected Clock
- **File:** `src/notifications/dispatcher.py`
  - Added optional `clock: Callable[[], datetime | str] | None = None` to `NotificationDispatcher.__init__`.
  - Added helper `_resolve_now_dt(now_utc)`:
    - If `now_utc` is provided, normalizes to UTC datetime.
    - Else if `self.clock` is provided, evaluates `self.clock()` and normalizes to UTC datetime.
    - Else raises `ValueError("now_utc or an injected clock is required; wall-clock fallback is disabled.")`.
  - Replaced all 3 occurrences of `datetime.now(timezone.utc)` in `dispatch()`, `release_rescheduled()`, and `replay_dlq()`.
  - Updated all existing tests (`test_duplicate_proposals.py`, `test_opt_out_enforcement.py`, `test_retry_dlq.py`, `test_notification_slice_e2e.py`) to pass explicit deterministic UTC timestamps.

### 1. Proposal Lifecycle State Machine & Duplicate Detection
- **Files:** `src/notifications/models.py`, `src/notifications/store.py`, `src/notifications/dispatcher.py`
  - Added `IN_PROGRESS = "in_progress"` to `NotificationStatus`.
  - In `dispatch()`, deduplication runs first:
    - Checks `get_proposal_status(proposal_id)`.
    - If status is `DISPATCHED`, `SUPPRESSED`, `FAILED`, or `QUEUED`: returns `NotificationStatus.SUPPRESSED` (`reason="duplicate_proposal"`) without writing to SQLite.
    - If status is `None`: registers the proposal immediately with `status=NotificationStatus.IN_PROGRESS`.
    - If status is `IN_PROGRESS`: recognizes a prior crash/retry and allows execution to proceed without error.
  - Removed old line 270 and line 514 `register_proposal` invocations to maintain single-source lifecycle state.
  - In `release_rescheduled()`, advances proposal status to `IN_PROGRESS` upon release.

### 2. General Adapter Exception Handling
- **File:** `src/notifications/queue.py`
  - In `deliver_with_retry()`, expanded error handling beyond `ProviderError`:
    ```python
    except Exception as exc:
        last_error = f"{type(exc).__name__}: {exc}"
        if attempt < self.max_retries:
            time.sleep(self.backoff_delay_sec)
    ```
  - Unknown exceptions are treated as transient errors: retried up to `max_retries`, and if exhausted, routed to the DLQ with the formatted exception type and message.

### 3. Single-Transaction Atomicity
- **File:** `src/notifications/store.py`
  - Wrapped `record_dispatch`, `record_suppression`, `record_dlq`, and `record_rescheduled` in `with self.connection:` blocks.
  - In each method, the terminal row insert and the `UPDATE notification_proposals SET status = ?` (with fallback insert) occur within the same atomic SQLite transaction, rolling back simultaneously on any error.
  - In `record_rescheduled`, removed `released = 0` from `ON CONFLICT` so the released flag is never reset.
  - Wrapped `mark_rescheduled_released` and `resolve_dlq` in `with self.connection:`.
  - Added helper query methods `get_rescheduled_entry(proposal_id)` and `get_suppression_entries(proposal_id)`.

---

## Known Limitation: At-Least-Once Delivery Contract

In a distributed environment with external notification providers (Apple APNs, Google FCM, Web Push, SendGrid), delivery cannot achieve two-phase atomic commit across network boundaries without a distributed coordinator:

1. **Failure Window:**
   - If the dispatcher invokes `adapter.send(proposal)` and the adapter successfully transmits the payload to the external provider, but the local host terminates (e.g. SIGKILL, container crash, host power loss) **before** SQLite commits the transaction containing `notification_dispatches` and `notification_proposals(status='dispatched')`:
2. **Behavior on Restart:**
   - The proposal remains in SQLite with status `in_progress`.
   - When the retry or re-dispatch worker picks up the proposal, it sees `in_progress` and re-invokes `adapter.send()`.
   - The learner may receive a duplicate push notification.
3. **Contract:**
   - The notification subsystem guarantees **at-least-once delivery** in crash scenarios, not strict exactly-once delivery.
   - Idempotency keys should be passed to external upstream gateways (e.g. APNs `apns-collapse-id` or FCM `collapse_key`) where supported to deduplicate at the client device level.

---

## Tests Added

Added comprehensive integration test suite `tests/notifications/test_crash_safety.py`:

1. **`test_adapter_runtime_error_routes_to_dlq_not_stuck`**
   - An adapter raises `RuntimeError("Network socket connection reset by peer")`.
   - Verifies 3 attempts are made, terminal fact is `NotificationStatus.FAILED`, proposal status in store is `FAILED`, and a DLQ row is written with full error details.
2. **`test_simulated_crash_after_registration_redispatch_delivers_once`**
   - Proposal registered with `status=IN_PROGRESS` simulating a process crash before send.
   - Re-dispatch recognizes `IN_PROGRESS`, executes delivery, and commits `DISPATCHED`.
   - Subsequent re-dispatch is deduplicated as `duplicate_proposal`.
3. **`test_suppressed_proposal_replayed_after_consent_restored_is_duplicate`**
   - Proposal suppressed due to channel opt-out.
   - Consent subsequently restored and proposal re-dispatched.
   - Verifies proposal remains suppressed as duplicate and zero additional suppression rows are written.
4. **`test_queued_proposal_redispatched_is_duplicate_and_preserves_release_flag`**
   - Proposal queued during quiet hours (`released=False`).
   - Re-dispatch is suppressed as duplicate.
   - Release sweep delivers message (`released=True`).
   - Re-dispatch post-release is suppressed as duplicate; verifies `released` flag in DB remains `True`.
5. **`test_clock_requirement_and_injected_clock`**
   - Omitting `now_utc` without injected clock raises `ValueError`.
   - Supplying an injected `clock=lambda: ...` executes deterministically with injected timestamp.

---

## Before / After Comparison

| Scenario | Before | After |
| :--- | :--- | :--- |
| **Adapter raises `RuntimeError`** | Unhandled exception crashed dispatcher; proposal stayed seen without terminal row; retry returned `duplicate_proposal`; message lost with 0 DLQ rows. | Catches exception, retries 3x, writes DLQ row with `"RuntimeError: ..."`, updates proposal status to `FAILED`. |
| **Process dies after registration** | Proposal was registered just before delivery; on reboot, retry saw proposal and returned `duplicate_proposal` without delivering. | Proposal registered as `IN_PROGRESS`; retry allows re-dispatch, delivering successfully and updating status to `DISPATCHED`. |
| **Suppressed proposal replayed** | Never registered in `notification_proposals`; replaying after consent restored bypassed suppression and delivered. | Registered as `SUPPRESSED`; replay detects duplicate, returns duplicate suppression, and writes 0 new DB rows. |
| **Re-dispatch of queued proposal** | `record_rescheduled` had `ON CONFLICT ... SET released = 0`, resetting release flag of released messages. | `record_rescheduled` preserves `released` flag; duplicate check suppresses re-dispatch without modifying queue state. |
| **`dispatch()` without `now_utc`** | Fell back to `datetime.now(timezone.utc)`; tests run between 22:00-07:00 UTC queued unexpectedly, failing 5 test suites. | Raises `ValueError` unless injected `clock` is configured; all tests pass deterministically 24/7. |
| **Terminal fact insertion** | Terminal facts and status updates executed in separate non-atomic statements. | Both statements execute within `with self.connection:` single transaction, rolling back together on error. |
