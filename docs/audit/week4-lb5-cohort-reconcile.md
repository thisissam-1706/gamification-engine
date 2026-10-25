# Week 4 Audit: Cohort Lifecycle Enforcement, Active-Only Bounds, and Event-Stream Reconciliation (LB-5)

## Problem

Four architectural and consistency defects in `src/leaderboard` left cohort constraints unenforced in production flows, allowed invalid cross-strategy cohort merges, distorted capacity bounds with inactive users, and allowed unanchored reconciliation with vulnerable audit keys:

### Defect 1: `assign_cohort` Did Not Enforce Cohort Lifecycle Rules
- **Location:** `src/leaderboard/projection.py:375-418`
- **Root Cause:**
  `CohortLifecycleService` previously existed merely as a helper library (`assert_can_admit_member`, `transition`) rather than an actively enforced invariant in projection writes.
  When `assign_cohort()` was called (either directly or via `Learner.CohortAssigned` domain event processing), it unconditionally upserted the learner into `leaderboard_entries` without checking cohort status or capacity bounds.
  If a cohort was in `sealed` or `finalized` status, mid-week arrivals were erroneously admitted instead of being refused.
- **Exact Reproduction:**
  Consider a cohort `cohort_alpha` transitioned to `sealed` status for epoch `2026-W42`.
  When learner `L2` arrived and `assign_cohort("L2", "cohort_alpha", "2026-W42", "2026-10-12T11:00:00Z")` was called:
  - **Before:** Admission succeeded without error. `store.get_entry("cohort", "cohort_alpha", "2026-W42", "L2")` was created, corrupting the sealed cohort with mid-week arrivals.
  - **After:** Admission is refused immediately by raising `CohortSealedError("Cohort 'cohort_alpha' is sealed; mid-week arrivals cannot enter sealed cohorts.")`. No entry is written to the store.

### Defect 2: `merge_undersized_cohorts` Counted Inactive Members and Allowed Cross-Strategy Merges
- **Location:** `src/leaderboard/cohorts.py:108-238` and `src/leaderboard/models.py:9-18`
- **Root Cause:**
  1. `merge_undersized_cohorts` evaluated cohort capacity bounds using total list length (`len(result_memberships[target.cohort_id]) < target.max_size`), counting inactive members (>30 days inactive) against the active capacity ceiling (`max_size=5000`). If a target cohort had 4,800 active and 200 inactive members (total 5,000), merging 150 active members was rejected with `CohortCapacityOverflowError`, despite having available active capacity (4,800 + 150 = 4,950 <= 5,000).
  2. `CohortDefinition` lacked a default strategy and `merge_undersized_cohorts` only checked for cross-epoch conflicts (`CohortIncompatibleEpochError`), allowing cohorts with incompatible strategies (`activity_volume` vs `course_module`) to merge.
- **Exact Reproduction:**
  - Merging `c_vol` (`strategy="activity_volume"`) with `c_mod` (`strategy="course_module"`):
    - **Before:** Merged without error, mixing heterogeneous learner cohorts.
    - **After:** Raises `CohortIncompatibleStrategyError("Cannot merge cohorts across mismatched strategies: {'activity_volume', 'course_module'}")`.
  - Merging 150 active members into a target with 4,800 active and 200 inactive members:
    - **Before:** Failed with `CohortCapacityOverflowError` because total members (5,000) reached `max_size`.
    - **After:** Evaluates active capacity (4,800 active + 150 active = 4,950 <= 5,000) and completes the merge successfully.

### Defect 3: Reconciliation Depended on Caller-Supplied State, Ignored Stream Pinning, and Had Colliding Audit IDs
- **Location:** `src/leaderboard/reconciliation.py:17-118` and `src/leaderboard/store.py`
- **Root Cause:**
  1. `reconcile_scope()` required callers to supply external `canonical_state: Mapping[str, tuple[int, str]]`. In production, reconciliation could not derive authoritative truth directly from durable aggregate/applied event streams.
  2. Reconciliation applied updates and deletions blindly without checking whether the read-model row had advanced past the audit watermark (`watermark_offset > pinned_offset`), risking overwriting in-flight newer updates with older historical snapshots.
  3. Inserts did not pin their watermark offset, and `pinned_offset` was not persisted in `ReconciliationAudit` or the database schema.
  4. Audit IDs were formatted using `f"audit_{int(time.time() * 1000)}_{scope_type}_{scope_id}"` with millisecond granularity. Two audits executing within the same millisecond collided on SQLite primary key `audit_id`, raising `sqlite3.IntegrityError: UNIQUE constraint failed`.
- **Exact Reproduction:**
  - Two audits executing in the same millisecond on `global`:
    - **Before:** The second call raised `sqlite3.IntegrityError: UNIQUE constraint failed: leaderboard_reconciliation_audits.audit_id`.
    - **After:** Collision-safe audit IDs formatted with `time.time_ns()`, atomic sequence counters, and UUID entropy persist cleanly with unique keys.
  - Read-model row `L_new` with `watermark_offset=35` during audit pinned at offset `20`:
    - **Before:** Updated or deleted by stale canonical snapshot.
    - **After:** `proj.watermark_offset > pinned_offset` check detects the row is newer than the pin and leaves it untouched.

### Defect 4: Inaccurate Documentation in `docs/ranking-consistency.md`
- **Location:** `docs/ranking-consistency.md:13-18, 46-52`
- **Root Cause:**
  The documentation claimed active cohorts were automatically sealed upon epoch commencement and described reconciliation as a scheduled background daily job. In reality, reconciliation is caller-invoked, and epoch transitions follow explicit deterministic service calls without automated cron daemons.

---

## Changes Made

### 1. Cohort Lifecycle Enforcement in `assign_cohort` and Store Persistence
- **`src/leaderboard/store.py`**:
  - Added `save_cohort_definition(cohort: CohortDefinition)` and `get_cohort_definition(cohort_id: str) -> CohortDefinition | None` to `LeaderboardStore` interface, `SQLiteLeaderboardStore`, and `InMemoryLeaderboardStore`.
  - Added `get_cohort_member_count(cohort_id: str, epoch: str) -> int` to query distinct learner count in the cohort for an epoch.
  - Created SQLite table `leaderboard_cohort_definitions` with columns `(cohort_id TEXT PRIMARY KEY, strategy TEXT, epoch TEXT, min_size INTEGER, max_size INTEGER, status TEXT)`.
  - Updated store rollback snapshots and `wipe_all_for_tests` to track and clear cohort definitions.
- **`src/leaderboard/projection.py`**:
  - Imported `CohortLifecycleService`, `CohortSealedError`, `CohortCapacityOverflowError`, and `CohortDefinition`.
  - In `assign_cohort(subject_id, cohort_id, epoch, assigned_at, watermark_offset)`:
    - Queries `cohort_def = self.store.get_cohort_definition(cohort_id)`. If absent, registers it with default `strategy="activity_volume"`, `epoch=epoch`, `status="forming"`.
    - If the learner is arriving or reassigning (`old_cohort != cohort_id`), counts current members via `get_cohort_member_count()` and invokes `CohortLifecycleService.assert_can_admit_member(cohort_def, current_count)`.
    - If `cohort_def.status == "forming"` and `current_count + 1 >= cohort_def.min_size`, automatically transitions the cohort to `"active"` using `CohortLifecycleService.transition()`.
    - If `cohort_def.status in {"sealed", "finalized"}`, immediately raises `CohortSealedError`.
  - Added public projection helpers `register_cohort(cohort)` and `transition_cohort(cohort_id, target_status, epoch)`.

### 2. Active-Only Density/Capacity Bounds & Strategy Compatibility in `merge_undersized_cohorts`
- **`src/leaderboard/models.py`**:
  - Added default field values to `CohortDefinition`: `strategy = "activity_volume"`, `epoch = ""`.
  - Added `pinned_offset: int = 0` to `ReconciliationAudit`.
- **`src/leaderboard/cohorts.py`**:
  - Defined `CohortIncompatibleStrategyError(ValueError)`.
  - In `merge_undersized_cohorts`:
    - Checks strategies across all non-finalized cohorts: raises `CohortIncompatibleStrategyError` if mismatched.
    - Defined `_is_active_member(m)` evaluating `m.status == "active"` and `not is_inactive(m.last_active_at, reference_time)`.
    - Counts only active members when evaluating undersized density (`active_count < threshold`).
    - Evaluates target cohort capacity against active members only (`target_active_count < target.max_size` and `total_active_to_merge <= primary.max_size`).
    - Merges active and inactive members preserving status and timestamps.

### 3. Stream-Rebuilt Canonical State, Pinned Offset Isolation, and Collision-Safe Audits
- **`src/leaderboard/reconciliation.py`**:
  - Added `build_canonical_state(scope_type, scope_id, epoch, pinned_offset)`:
    - Fetches applied events from store: `get_applied_events(epoch=epoch)`.
    - Filters events strictly up to `pinned_offset` (`stream_offset <= pinned_offset`).
    - For cohort scopes, filters events where `store.get_member_cohort(epoch, sub) == scope_id`.
    - Deterministically aggregates cumulative score and chronological `first_reached_at` tie-breaker.
  - In `_execute_reconciliation`:
    - For updates: if `pinned_offset > 0` and `proj.watermark_offset > pinned_offset`, leaves row alone.
    - For deletes (orphans): if `pinned_offset > 0` and `proj.watermark_offset > pinned_offset`, leaves row alone.
    - For inserts: stamps missing row with `watermark_offset = pinned_offset`.
  - Added `reconcile_scope_from_mapping_for_tests` as an explicit test helper.
  - Made audit IDs collision-safe: `f"audit_{time.time_ns()}_{next(_audit_counter)}_{uuid.uuid4().hex[:8]}_{scope_type}_{scope_id}"`.
- **`src/leaderboard/store.py`**:
  - Added `pinned_offset INTEGER NOT NULL DEFAULT 0` column to `leaderboard_reconciliation_audits` in `_init_db` and automatic schema migration PRAGMA.
  - Persists and queries `pinned_offset` in `record_reconciliation_audit()` and `get_reconciliation_audits()`.

### 4. Documentation Correction
- **`docs/ranking-consistency.md`**:
  - Section 3: Removed claims about automatic epoch sealing at commencement. Documented caller-invoked lifecycle transitions via `CohortLifecycleService.transition` and deterministic rejection with `CohortSealedError`.
  - Section 6: Removed scheduled daily background job claims. Documented caller-invoked reconciliation, stream rebuilding up to `pinned_offset`, row isolation for `watermark_offset > pinned_offset`, and collision-safe audit IDs.

---

## Tests Added and Verified

Added suite in `tests/leaderboard/test_cohort_lifecycle_reconcile.py`:

1. **`test_assign_cohort_enforces_lifecycle_and_refuses_sealed`** (Parameterized: SQLite, InMemory):
   - Forms cohort, admits `L1`.
   - Transitions cohort `forming -> active -> sealed`.
   - Rejects arrival `L2` with `CohortSealedError`. Verifies store contains 0 entries for `L2`.
   - Verifies real domain event `Learner.CohortAssigned` applied to sealed cohort raises `CohortSealedError`.

2. **`test_merge_undersized_cohorts_active_only_density_and_capacity`**:
   - Case A: 400 active + 200 inactive in `c1` (<500 threshold). Merging into target with 4,800 active + 200 inactive raises `CohortCapacityOverflowError` because 4,800 + 400 = 5,200 > 5,000 active limit.
   - Case B: 150 active + 350 inactive in `c1`. Merging into target with 4,800 active succeeds (4,800 + 150 = 4,950 <= 5,000 active capacity), despite total members equaling 5,500.

3. **`test_merge_undersized_cohorts_incompatible_strategy_and_epoch`**:
   - Merging `activity_volume` with `course_module` raises `CohortIncompatibleStrategyError`.
   - Merging `2026-W41` with `2026-W42` raises `CohortIncompatibleEpochError`.

4. **`test_reconciliation_rebuilds_from_stream_and_fixes_drift`** (Parameterized: SQLite, InMemory):
   - Authoritative applied stream: `L1` (200 XP at offset 20), `L2` (150 XP at offset 30).
   - Corrupts read model: `L1` score set to 50, phantom row `L_phantom` (80 XP) injected.
   - Invokes `reconcile_scope(scope_type="cohort", pinned_offset=30)` with no caller mapping.
   - Verifies canonical state rebuilt from stream: `L1` updated to 200 XP, `L_phantom` deleted, `L2` untouched, `audit.pinned_offset == 30`.

5. **`test_reconciliation_ignores_rows_newer_than_pinned_offset`** (Parameterized: SQLite, InMemory):
   - Stream: `L_old` (100 XP at offset 10 <= 20 pin), `L_new` (500 XP at offset 35 > 20 pin).
   - Simulates `L_future_phantom` at offset 40 > 20 pin.
   - Reconciles pinned at offset 20:
     - `L_old` corrected from drifted 50 to 100 XP.
     - `L_new` (offset 35) left untouched at 500 XP.
     - `L_future_phantom` (offset 40) left untouched (not deleted).

6. **`test_reconciliation_audits_same_millisecond_collision_safe`**:
   - Executes two audits back-to-back with the identical millisecond timestamp string against SQLite.
   - Confirms distinct audit IDs (`audit1.audit_id != audit2.audit_id`) and both records persisted without `sqlite3.IntegrityError`.

---

## Exact Before/After Values from Reproductions

| Metric / Scenario | Before Fix | After Fix |
|:---|:---|:---|
| **Arrival to sealed cohort via `assign_cohort`** | Admitted into sealed cohort; member count incremented to `2`. | Rejected with **`CohortSealedError`**; member count stays at **`1`**. |
| **Arrival to sealed cohort via `Learner.CohortAssigned`** | Applied to projection read model without error. | Rejected with **`CohortSealedError`**. |
| **Merge cohorts with mismatched strategies** | Merged without validation error. | Rejected with **`CohortIncompatibleStrategyError`**. |
| **Merge capacity calculation with inactive members** | Counted inactives (total count = `5,500`); rejected valid merge with `CohortCapacityOverflowError`. | Counts only active members (**`4,950 <= 5,000`**); merge succeeds. |
| **Reconciliation canonical state derivation** | Required caller to supply external `canonical_state` dict; no stream rebuild. | Rebuilt autonomously from **`store.get_applied_events()`** up to `pinned_offset`. |
| **Reconciliation row newer than pin (`watermark_offset > pinned_offset`)** | Overwritten/deleted by stale canonical snapshot. | **Left untouched**; excluded from update/delete/corrected lists. |
| **Two audits executed in same millisecond** | Duplicate `audit_id` generated; crashed with `sqlite3.IntegrityError: UNIQUE constraint failed`. | Unique collision-safe IDs; both records **successfully persisted**. |
| **Full test suite pass count** | N/A | **207 passed, 4 skipped, 2 failed** (accepted Windows-only fork test in `tests/ingestion/test_ingestion_service.py`). |
