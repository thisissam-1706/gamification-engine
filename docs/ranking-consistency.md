# Ranking Consistency and Cohort Semantics (Milestone M3)

## 1. Overview
This document specifies the ranking consistency model, deterministic tie-breaking logic, cohort lifecycle bounds, and reconciliation semantics for the gamification engine.

## 2. Cohort Bounds and Lifecycle
Cohorts partition active synthetic learners into bounded competitive communities:
- **Size Bounds**: Active cohorts must contain between **500 (minimum)** and **5,000 (maximum)** learners.
- **Inactivity Pruning**: Learners with no recorded activity for $>30$ consecutive days are automatically marked as inactive and removed from active cohort calculations. Their historical XP and streak records remain untouched.
- **Undersized Merging**: If an educational module or tier has fewer than 500 active learners at epoch transition, it is merged into adjacent difficulty modules to maintain competition density.
- **Fairness & Sensitive Attribute Exclusion**: Cohort formation uses only operational inputs (prior week lesson volume, active course module ID). Demographic, geographic, or protected identity attributes are strictly excluded.

## 3. Membership Epochs & Calendar Weeks
To provide clear weekly reset cycles and avoid rank instability from mid-cycle backfilling:
- Epochs are scoped to ISO calendar weeks formatted as `YYYY-Www` (e.g. `2026-W42`).
- Active cohorts are **sealed** upon epoch commencement.
- Learners arriving mid-week are placed into provisional forming cohorts or queued for the next epoch, preserving rank stability for established cohorts.

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
This guarantees exact $O(\log N)$ rank lookups and exact deterministic tie resolution with zero floating-point distortion.

## 5. Consistency Guarantees

### 5.1 Read-Your-Writes on State
Per-learner actions are causally consistent:
- When learner $L$ completes a lesson, their XP is committed to the durable SQLite event store.
- Each projection maintains an event watermark offset $W$. A query specifying watermark $W$ guarantees that all events up to offset $W$ are reflected in the read model.
- A learner's processed XP never regresses (except during explicit, authorized abuse-compensation events).

### 5.2 Visibility Latency SLA
- Eventual consistency target: $P_{99} < 2.0\text{ seconds}$ from canonical event ingestion to leaderboard query visibility.
- In measured SQLite benchmarks, $P_{99}$ update latency is under $4\text{ ms}$, and query latency is under $3\text{ ms}$.

## 6. Reconciliation Semantics (AT-7)
The scheduled daily reconciliation job (`LeaderboardReconciler`) audits the read projection against canonical event logs:
1. **Audit**: Extracts authoritative scores and `first_reached_at` timestamps from the event log for all learners in an epoch.
2. **Divergence Detection**: Identifies dropped events (score lag), missing entries, or phantom projection rows.
3. **Differential Updates**: Applies pinpoint `INSERT`, `UPDATE`, and `DELETE` queries. The job **never executes destructive flushes** (`FLUSHDB` or table truncations).
4. **Audit Evidence**: Emits a `ReconciliationAudit` record documenting audited count, mismatch count, corrected learner IDs, and execution latency.
