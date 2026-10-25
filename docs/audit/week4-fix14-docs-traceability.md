# Week 4 fix 14: docs and traceability (Week 3 review row 28)

## Step H1

No docs were edited in this step. Only tests and this note were added.

### Grep hits

Command 1: `grep -rniE "redis|postgres|zadd|kafka" docs README.md --include='*.md'`, excluding `docs/adr`, `docs/audit` and `docs/literature-landscape.md`.

| File:line | Text |
|---|---|
| docs/problem-boundaries.md:7 | Cohorts and Leaderboards: ... Leaderboard projections are a future milestone; Redis-backed leaderboards are deferred until measurements justify them. |
| docs/problem-boundaries.md:12 | Synthetic-Population Size: ... Kafka partition concurrency is not part of the current implementation. |
| README.md:3 | Changelog (2026-10-12): ... marked Kafka/Redpanda, Redis, and PostgreSQL as deferred options. |
| README.md:49 | `Kafka["Kafka / Redpanda"]:::deferred` |
| README.md:50 | `Redis["Redis"]:::deferred` |
| README.md:51 | `Postgres["PostgreSQL"]:::deferred` |
| README.md:53 | `EventStore -. deferred scale-out .-> Kafka` |
| README.md:54 | `LeaderboardDB -. deferred cache .-> Redis` |
| README.md:55 | `EventStore -. deferred production store .-> Postgres` |
| README.md:62 | ... Notifications, abuse detection, Kafka/Redpanda, Redis, and PostgreSQL are shown as deferred extension points rather than active runtime dependencies. |
| README.md:76 | ADR-0001: Event Pipeline vs. PostgreSQL Outbox |
| README.md:77 | ADR-0002: Kafka/Redpanda Event Pipeline vs. PostgreSQL |
| README.md:99 | Kafka/Redpanda, Redis, and PostgreSQL setup instructions will be added only if measured scale or reliability needs require those components. |
| docs/metrics-framework.md:77 | Leaderboard Consistency & Latency (M3, M8): The measurable update and query latency for **Redis sorted sets** at both the cohort and global levels, ... |

Only `docs/metrics-framework.md:77` presents Redis as the current design. All the other hits already say deferred or are links to ADRs. No file has a `zadd` hit.

Command 2: `grep -niE "abuse|implemented" docs/problem-boundaries.md README.md`.

| File:line | Text | Stale? |
|---|---|---|
| README.md:5 | ... experiments, and **rule-based abuse detection**. The current implementation runs as a single process ... | Yes: `src/abuse/` is empty and M6 is open. |
| README.md:48 | `Abuse["Abuse detection"]:::deferred` | No |
| README.md:57 | `Projections -. future consumers .-> Abuse` | No |
| README.md:62 | The implemented path runs in one process ... Notifications, abuse detection, Kafka/Redpanda, Redis, and PostgreSQL are shown as deferred extension points ... | Abuse wording is fine. Calling notifications deferred looks stale next to M5 "implemented & passing". Not pinned yet; flagged for H2. |
| README.md:78 | ADR-0003: Rule-Based Abuse Detection | No |
| README.md:116 | `M6 | Abuse detection | ☐` | No |
| docs/problem-boundaries.md:13 | System Exclusions: ... **The current process performs rule-based abuse detection**; an independent consumer and separate event-stream deployment are future extensions. | Yes |
| docs/problem-boundaries.md:14 | Interface Boundaries (Dashboard Scope): ... inspect raw event logs, cohort states, and abuse flags. | Not pinned. |

### Tests

`tests/docs/__init__.py` (empty) and `tests/docs/test_traceability_docs.py` (7 tests, 9 with parameters). It parses the "8.2 Traceability Matrix" table and asserts:

- No `TODO(owner)` remains in the matrix.
- The table has an "Owner" and a "Peer reviewer" column. For M1 to M8 neither is empty and Owner differs from Peer reviewer.
- Every Status cell is non-empty.
- `docs/traceability-matrix.md` has a heading "Reused, configured, new".
- Three stale wordings are gone: "Redis sorted sets" in `docs/metrics-framework.md`, the "rule-based abuse detection" claim in the `README.md` intro, and "The current process performs rule-based abuse detection" in `docs/problem-boundaries.md`.

### Result before any doc change

`python -m pytest tests/docs -q`:

```
FAILED tests/docs/test_traceability_docs.py::test_no_owner_placeholders_remain
FAILED tests/docs/test_traceability_docs.py::test_owner_and_peer_reviewer_columns_are_filled_and_distinct
FAILED tests/docs/test_traceability_docs.py::test_reused_configured_new_heading_exists
FAILED tests/docs/test_traceability_docs.py::test_stale_wording_is_gone[docs/metrics-framework.md-Redis sorted sets-...]
FAILED tests/docs/test_traceability_docs.py::test_stale_wording_is_gone[README.md-streaks, quests, badges, notifications, experiments, and rule-based abuse detection-...]
FAILED tests/docs/test_traceability_docs.py::test_stale_wording_is_gone[docs/problem-boundaries.md-The current process performs rule-based abuse detection-...]
6 failed, 1 passed in 0.03s
```

Key assertion messages:

- `'TODO(owner)' is contained here: ... | M1 ... | TODO(owner) | partial (lesson_completed only) | W2-3 |`
- `assert 'Peer reviewer' in ['Milestone', 'Acceptance tests', 'Component', 'Owner', 'Status', 'Target week']`
- `assert None` for the regex search of the "Reused, configured, new" heading.
- `docs/metrics-framework.md still says 'Redis sorted sets'`
- `README.md still says '... rule-based abuse detection'`
- `docs/problem-boundaries.md still says 'The current process performs rule-based abuse detection'`

The one passing test is the non-empty Status check. It is a guard that should stay green.

## Step H2

Edited only `docs/traceability-matrix.md`. Milestones, acceptance tests, components and target weeks are unchanged.

### Changes

- Added the changelog line dated 2026-10-25 at the top.
- Added a "Peer reviewer" column after "Owner" in 8.2.
- Added a "Reused, configured, new" section after 8.2 (17 rows). Each row cites a file or import found by reading `src/`, `pyproject.toml`, `experiments/exp_007.yaml` and the tests.

### Before and after, per cell

| Milestone | Owner before | Owner after | Peer reviewer (new) | Status before | Status after |
|---|---|---|---|---|---|
| M1 | TODO(owner) | Sampadram | Madhava Asrith | partial (lesson_completed only) | partial (lesson_completed only; ID binding, late window, rule selection tested) |
| M2 | TODO(owner) | Sampadram | Madhava Asrith | slice done | slice done (active readers agree with aggregate and replay) |
| M3 | Madhava Asrith | Madhava Asrith | Sampadram | implemented & passing (recovery & constraints verified) | leaderboard duplicate, restart, offset and time handling tested; known gaps: cohort merge helper not wired in, capacity check not transactional, member count includes inactive |
| M4 | TODO(owner) | Madhava Asrith | Sampadram | not started | not started |
| M5 | Madhava Asrith | Madhava Asrith | Sampadram | implemented & passing | in-app and simulated-push slice implemented; email adapter not implemented |
| M6 | TODO(owner) | Sampadram | Madhava Asrith | not started | not started (src/abuse is empty; point-farming test skipped) |
| M7 | TODO(owner) | Sampadram | Madhava Asrith | protocol in progress | assignment and metric implemented; simulator is a stub; exposure logging not implemented |
| M8 | TODO(owner) | Madhava Asrith | Sampadram | not started | per-learner rebuild and leaderboard replay tested; full-population orchestration not implemented |

### Status wording checked against the code

One status was changed from your wording:

- **M5:** you wrote "in-app and push slice implemented". The push channel is `SimulatedPushAdapter` (`src/notifications/channels/push.py`, wired in `src/notifications/dispatcher.py`), so the matrix says "in-app and simulated-push slice implemented". Email exists only as `ChannelType.EMAIL` in `src/notifications/models.py`, with no adapter, so "email adapter not implemented" stands.

The other statuses held up:

- **M1:** tests exist for ID binding (`test_event_id_binding.py`), late window (`test_late_window*.py`) and rule selection (`test_rule_selection.py`).
- **M2:** `tests/state_machine/test_active_state_path.py` and `test_live_vs_replay.py`.
- **M3:**
  - The merge helper `merge_undersized_cohorts` is exported and tested, but `src/leaderboard/projection.py` never calls it.
  - The capacity check reads `get_cohort_member_count` outside `store.transaction()` (`projection.py` around lines 431 and 449).
  - `get_cohort_member_count` counts distinct subjects in `leaderboard_entries` with no inactivity filter.
- **M6:** `src/abuse/` holds only `__init__.py`, and `tests/abuse/test_point_farming.py` is skipped.
- **M7:** `src/experiments/assignment.py` and `metrics.py` exist, there is no simulator code, `tests/experiments/test_assignment_vs_exposure.py` is skipped, and `DeliveryTrace.exposures` is always empty.
- **M8:** `tests/integration/test_full_state_rebuild.py` is skipped ("Multi-learner full-state orchestration is not implemented"). Per-learner rebuild and leaderboard replay tests exist (`test_projection_rebuild.py`, `test_atomic_rebuild.py`, `test_leaderboard_recovery.py`).

### Evidence notes for the new table

- The eventsourcing notification log is used by `tests/leaderboard/test_leaderboard_e2e.py`, not by `src/` directly. The row cites that test.
- "Cohort bounds" cites `CohortDefinition` defaults (500 and 5000) in `src/leaderboard/models.py`.

### `tests/docs` result

`python -m pytest tests/docs -q`: **3 failed, 4 passed.**

Now passing: no `TODO(owner)`, Owner and Peer reviewer columns filled and distinct, Status non-empty, "Reused, configured, new" heading.

Still failing (expected, the stale wording is not edited yet):

- `docs/metrics-framework.md`: "Redis sorted sets"
- `README.md`: "... and rule-based abuse detection" in the intro
- `docs/problem-boundaries.md`: "The current process performs rule-based abuse detection"

## Step H3

Wording-only fixes of the stale statements from Step H1. No number, threshold, window, cap or acceptance criterion was changed. Acceptance tests AT-1 to AT-16 were not edited. AT-7 has no Redis, Postgres or ZADD wording (it describes pinpoint INSERT, UPDATE and DELETE), so it needed no edit.

### Before and after

| File | Before | After |
|---|---|---|
| docs/metrics-framework.md:77 | ...update and query latency for **Redis sorted sets** at both the cohort and global levels... | ...update and query latency for **the SQLite-backed leaderboard store** at both the cohort and global levels... |
| README.md:5 | ...deterministic XP, streaks, quests, badges, notifications, experiments, **and rule-based abuse detection**. The current implementation runs... | ...deterministic XP, streaks, quests, badges, notifications, **and experiments. Rule-based abuse detection is planned (M6) but not implemented.** The current implementation runs... |
| README.md:62 | ...**Notifications, abuse detection, Kafka/Redpanda, Redis, and PostgreSQL are shown as deferred extension points** rather than active runtime dependencies. | ...**In-app and simulated-push notifications are implemented; abuse detection, Kafka/Redpanda, Redis, and PostgreSQL are deferred extension points** rather than active runtime dependencies. |
| docs/problem-boundaries.md:7 | ...Leaderboard projections **are a future milestone**; Redis-backed leaderboards are deferred... | ...Leaderboard projections **are SQLite-backed (M3)**; Redis-backed leaderboards are deferred... |
| docs/problem-boundaries.md:13 | ...The current process **performs rule-based abuse detection**; an independent consumer... | ...**Rule-based abuse detection is planned (M6) but not implemented**; an independent consumer... |
| docs/traceability-matrix.md (top) | (no line) | New changelog line dated 2026-10-26 naming each change above. |

Hits left unchanged because they were already correct: the "deferred" Kafka, Redis and PostgreSQL mentions in README.md lines 3, 49 to 55, 76, 77 and 99, and in problem-boundaries.md line 12.

### Left for you (not edited)

- **README.md mermaid diagram (lines 47 and 57):** the "Notification engine" node is styled `:::deferred` with a "future consumers" edge, which now disagrees with the prose on line 62 (M5 is implemented for in-app and simulated push). Fixing it changes the diagram, not just wording.
- **docs/problem-boundaries.md:14:** the dashboard is described as inspecting "abuse flags", which cannot exist until M6. Left as a scope statement.
- **docs/problem-boundaries.md:13:** the sentence "handles scripted point farming and impossible timing exploits asynchronously" also describes unimplemented behaviour. Left because rewording it goes beyond the recorded stale hit.

### AT-vs-code comparison

AT numbering is the 8.3 list (AT-1 is "Duplicate Event Dedup", AT-3 is "Out-of-Order Within 72h", AT-7 is "Leaderboard Reconciliation"). Nothing in this list was changed.

| AT | Text says | Code does | Class |
|---|---|---|---|
| AT-1 | Re-ingesting an event with the **same dedup_key** "within the **7-day window**" is duplicate-detected. | There is no 7-day window. Duplicates are detected by the durable event_id binding (`duplicate_event_id`, `src/ingestion/service.py`) and by completion_id (`duplicate_completion_id`), with no time limit. `dedup_key` is only validated: it must equal `<event_id>:<rule_version>`, else `dedup_key_mismatch`. The only time rule is the 72h late window (`DEFAULT_LATE_WINDOW`), which rejects first-time deliveries received more than 72h after `occurred_at` (`outside_late_window`). | **needs my approval** (the 7-day figure and the dedup identity are part of the acceptance criterion) |
| AT-1 | The duplicate is "silently discarded". | A duplicate returns `IngestionResult("duplicate", ...)` and writes a duplicate evidence row (`EvidenceStore.record`). It is not silent, though XP is unchanged. | wording-only |
| AT-3 | "arrives at **ingested_at** = 2026-09-24 (within 72h)". | The envelope's `ingested_at` is never used for the window. Lateness is measured against the trusted receive clock (`IngestConfig.clock`) as `received - occurred_at`. | **needs my approval** (the example inputs and the clock used for the 72h test are part of the criterion) |
| AT-3 | "a **micro-replay** is triggered from 2026-09-21 forward". | There is no micro-replay trigger. Learner state is a fold of the whole event log (`rebuild_learner_state`); `tests/state_machine/test_out_of_order.py` checks that the full fold gives the correct chronological streak and XP. | wording-only |
| AT-7 | "when the **daily** reconciliation job runs". | `LeaderboardReconciler.reconcile_scope` exists, but nothing in `src/leaderboard` schedules it daily. | **needs my approval** (the frequency is part of the criterion) |
| AT-7 | Differential correction by pinpoint INSERT, UPDATE or DELETE without a full table flush. | Matches: `_execute_reconciliation` counts inserted, updated and deleted rows. | matches |
| AT-7 | Tie resolved by normalized UTC `occurred_at` of the event first reaching the score, then `subject_id`. | Matches: ranking is `score DESC, first_reached_at ASC, subject_id ASC`, and `first_reached_at` comes from the applied-event time. | matches |

### Test results

`python -m pytest tests/docs -q`:

```
7 passed in 0.01s
```

`python -m pytest -q --ignore=tests/packaging` (this includes `tests/leaderboard` and `tests/notifications`):

```
369 passed, 4 skipped in 8.31s
```
