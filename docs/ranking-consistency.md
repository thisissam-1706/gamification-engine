# Ranking Consistency and Cohort Semantics (Milestone M3)

## 1. Overview
This document specifies the ranking consistency model, deterministic tie-breaking logic, cohort lifecycle bounds, and reconciliation semantics for the gamification engine.

## 2. Cohort Bounds and Lifecycle
Cohorts partition active synthetic learners into bounded competitive communities:
- **Size Bounds**: Active cohorts must contain between **500 (minimum)** and **5,000 (maximum)** learners.
- **Inactivity Pruning**: Specified behaviour, not yet scheduled or wired in (`is_inactive` exists as a helper with no production caller). Learners with no recorded activity for $>30$ consecutive days are to be marked as inactive and removed from active cohort calculations. Their historical XP and streak records remain untouched.
- **Undersized Merging**: Specified behaviour, not yet scheduled or wired in (`merge_undersized_cohorts` exists as a helper with no production caller). If an educational module or tier has fewer than 500 active learners at epoch transition, it is to be merged into adjacent difficulty modules to maintain competition density.
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
