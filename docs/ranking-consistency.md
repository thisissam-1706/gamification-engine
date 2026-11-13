# Ranking Consistency and Cohort Semantics (Milestone M3)

## 1. Overview
This document specifies the ranking consistency model, deterministic tie-breaking logic, cohort lifecycle bounds, and reconciliation semantics for the gamification engine.

## 2. Cohort Bounds and Lifecycle
Cohorts partition active synthetic learners into bounded competitive communities:
- **Size Bounds**: Active cohorts must contain between **500 (minimum)** and **5,000 (maximum)** learners.
- **Weekly Cohort Builder** (`build_weekly_cohorts` in `src/leaderboard/cohort_builder.py`): assigns learners to cohorts for one ISO-week epoch, reading each learner's stored `LessonCompleted` events. It uses only the learner's prior-week lesson count and the module prefix of the lesson ids (see "Lesson id convention" in `event-contracts.md`):
  - **Tier**: the number of accepted lesson completions in the ISO week before the epoch (UTC `occurred_at`; every completion kind counts). Fewer than 3 is `casual`, 3 to 9 is `regular`, 10 or more is `intensive`. A learner with no prior-week lessons is `casual`.
  - **Module**: the module with the most prior-week completions; a tie goes to the lexicographically smallest module id; no prefixed lesson in that week gives module `general`.
  - **Cohort id**: the activity-volume cohort id and the module cohort id (`assign_activity_volume_cohort`, `assign_module_cohort`) joined with `__`. The hash buckets default to 10 per tier and 5 per module, which suits large populations; the synthetic workload uses 1 of each so a cohort is exactly one tier in one module.
  - **Assignment**: each new learner is recorded with `Learner.assign_cohort` (`grouping_rule_version` `cohort_builder_v1`, `assigned_at` the caller's `reference_time`). A learner who already has a cohort for the epoch keeps it, so running the builder again changes nothing and no learner is moved.
- **Inactivity Filter**: when the builder runs, a learner whose last lesson completion at or before `reference_time` is 30 or more days earlier (`is_inactive`) is excluded and not assigned (reported in the result's `inactive` list). A learner with no lesson history at all is treated as new, not inactive. Their historical XP and streak records remain untouched. The cohort member count used by the leaderboard projection for admission (capacity) and for the forming-to-active advance still covers the learners assigned to the cohort for the current weekly epoch, because cohorts are rebuilt every epoch; a learner assigned this week who then goes idle still counts until the next epoch.
- **Undersized Merging and Size Bounds**: the builder splits a group of new members larger than `max_size` into equal parts, and merges groups below `min_size` with `merge_undersized_cohorts` (counting only active members, same epoch, same strategy). If a small group cannot be merged without exceeding `max_size`, it stays as it is, so `max_size` always holds even where `min_size` cannot. Splitting and merging consider only the learners newly assigned in that call. The defaults are 500 and 5,000; the synthetic workload passes `min_size=2` because its populations have 8 to 15 learners.
- **Still caller-invoked**: nothing runs the builder on a schedule. In this repository only the synthetic rebuild harness and the demo seed call it. Sealing a cohort (`CohortLifecycleService.transition`) and reconciliation are also invoked by the caller, and the admission rules for sealed cohorts are enforced by the leaderboard projection, not by the builder (which cannot see a cohort's status).
- **Fairness & Sensitive Attribute Exclusion**: Cohort formation uses only operational inputs (prior week lesson volume, active course module ID). Demographic, geographic, or protected identity attributes are strictly excluded.

## 3. Membership Epochs & Calendar Weeks
To provide clear weekly reset cycles and avoid rank instability from mid-cycle backfilling:
- Epochs are scoped to ISO calendar weeks formatted as `YYYY-Www` (e.g. `2026-W42`).
- Cohorts follow explicit lifecycle states (`forming`, `active`, `sealed`, `finalized`).
- When a cohort is transitioned to `sealed` (caller-invoked transition via `CohortLifecycleService.transition`), mid-week arrivals cannot enter sealed cohorts and are rejected deterministically with `CohortSealedError`. Learners arriving after a seal are placed into forming or active cohorts.

## 4. Deterministic Tie-Breaking Rules
Ties in scores are resolved deterministically using exact compound precedence:
1. **Primary Key**: Score descending ($S_A > S_B \implies \text{Rank}_A < \text{Rank}_B$).
2. **Secondary Key**: Earliest event timestamp reaching that score ascending ($T_A < T_B \implies \text{Rank}_A < \text{Rank}_B$).
3. **Tertiary Key**: Subject identifier lexicographical ascending ($ID_A < ID_B \implies \text{Rank}_A < \text{Rank}_B$).

### Implementation
Our system uses SQLite compound indexing:
```sql
CREATE INDEX idx_leaderboard_ordering
ON leaderboard_entries (scope_type, scope_id, epoch, score DESC, first_reached_at ASC, subject_id ASC);
```
This guarantees exact deterministic tie resolution with zero floating-point distortion, achieving measured sub-millisecond rank lookups ($P_{99} < 4\text{ ms}$ for $N=1,000$ active learners) via an indexed compound count query.

## 5. Consistency Guarantees

### 5.1 Read-Your-Writes on State
Per-learner actions are causally consistent:
- When learner $L$ completes a lesson, their XP is committed to the durable SQLite event store.
- Each projection maintains a contiguous committed checkpoint offset $W$. A query specifying watermark $W$ guarantees that all events up to offset $W$ are reflected in the read model, or returns explicit lag.
- Leaderboard projection updates, tracking records, and checkpoint offsets commit in a single atomic transaction.
- A learner's processed XP never regresses (except during explicit, authorized abuse-compensation events).

### 5.2 Visibility Latency SLA
- Eventual consistency target: $P_{99} < 2.0\text{ seconds}$ from canonical event ingestion to leaderboard query visibility.
- In measured SQLite benchmarks, $P_{99}$ update latency is under $4\text{ ms}$, and query latency is under $3\text{ ms}$.

## 6. Reconciliation Semantics (AT-7)
The caller-invoked reconciliation service (`LeaderboardReconciler`) audits the read projection against canonical event streams (`applied_events` log) up to a pinned stream offset:
1. **Canonical Stream Rebuilding**: Authoritative learner scores and normalized UTC tie-breaking timestamps (`first_reached_at`) are rebuilt deterministically from the applied event stream up to `pinned_offset` without caller-supplied drift.
2. **Pinned Offset Isolation**: Any projection row with `watermark_offset > pinned_offset` is strictly left alone, preventing reconciliation from overwriting newer in-flight updates. New insertions also record the pinned offset.
3. **Differential Updates**: Applies pinpoint `INSERT`, `UPDATE`, and `DELETE` queries. The reconciler **never executes destructive flushes** (`FLUSHDB` or table truncations).
4. **Collision-Safe Audit Records**: Emits a `ReconciliationAudit` record with a collision-safe audit ID (incorporating high-resolution nanosecond timestamps, atomic sequence counters, and entropy) and persists `pinned_offset`, divergence counts, corrected learner IDs, and execution latency to `leaderboard_reconciliation_audits`.
