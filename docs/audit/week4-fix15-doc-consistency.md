# Week 4 fix 15: document consistency

## Step D1

**Change.** One line added to `src/ingestion/evidence.py`, as the first import after the module docstring (a blank line follows it). Nothing else in the file changed.

```diff
 """Durable records for deliveries that must not change learner state."""
 
+from __future__ import annotations
+
 from dataclasses import dataclass
```

**Commands and output.**

| Command | Output |
|---|---|
| `.venv/bin/python -c "import src.ingestion.evidence"` (Python 3.14.6) | no output, exit 0 |
| `which python3.11` | not found: Python 3.11 is not installed on this machine |
| `/usr/bin/python3.12 --version` | `Python 3.12.3` |
| `/usr/bin/python3.12 -c "import src.ingestion.evidence; print('import ok')"` | `import ok`, exit 0 |
| `.venv/bin/python -m pytest -q --ignore=tests/packaging` | `362 passed, 4 skipped in 8.30s` |

The count dropped from 369 to 362 because the 7 tests in `tests/docs/test_traceability_docs.py` were deleted earlier. This change added no tests.

**Not covered.** Python 3.11 was not tested because it is not installed. The pre-change failure was not reproduced here. Only the post-change import was run on 3.12.

## Step D2

Wording-only. No number, threshold, window, cap or acceptance criterion changed. Line numbers were confirmed by grep before editing: README.md lines 42, 62 and 120; `docs/ranking-consistency.md` lines 9 and 10; `docs/problem-boundaries.md` lines 5, 13 and 14 of the file as listed; the M3 row of 8.2.

### Before and after

| File | Before | After |
|---|---|---|
| docs/ranking-consistency.md §2 (line 9) | **Inactivity Pruning**: Learners with no recorded activity for $>30$ consecutive days are automatically marked as inactive and removed from active cohort calculations. ... | **Inactivity Pruning**: Specified behaviour, not yet scheduled or wired in (`is_inactive` exists as a helper with no production caller). Learners with no recorded activity for $>30$ consecutive days are to be marked as inactive and removed from active cohort calculations. ... |
| docs/ranking-consistency.md §2 (line 10) | **Undersized Merging**: If an educational module or tier has fewer than 500 active learners at epoch transition, it is merged into adjacent difficulty modules... | **Undersized Merging**: Specified behaviour, not yet scheduled or wired in (`merge_undersized_cohorts` exists as a helper with no production caller). If an educational module or tier has fewer than 500 active learners at epoch transition, it is to be merged into adjacent difficulty modules... |
| README.md:120 | ...bounded cohorts (500–5,000 members, 30-day inactivity pruning) and ranked... | ...bounded cohorts (500–5,000 members; 30-day inactivity pruning is specified but not yet scheduled or wired in) and ranked... |
| docs/problem-boundaries.md:5 | ...Experience Points (XP), bounded by an algebraic decay model that diminishes returns on repeated lessons to prevent reward farming. | ...Experience Points (XP), a fixed per-ruleset amount (`xp_amount` in `rulesets.json`). A decay model that diminishes returns on repeated lessons is a planned part of M6 and is not implemented. |
| docs/problem-boundaries.md:13 | **System Exclusions:** The platform handles scripted point farming and impossible timing exploits asynchronously. Rule-based abuse detection is planned (M6) but not implemented; ... | **System Exclusions:** Rule-based abuse detection is planned (M6) but not implemented; ... (first sentence deleted) |
| docs/problem-boundaries.md:14 | ...cohort states, and abuse flags. ... | ...cohort states, and (planned, M6) abuse flags. ... |
| README.md:42 (diagram edge) | `Aggregate -. canonical state .-> Reconciler` | `LeaderboardDB -. applied-event log to pinned offset .-> Reconciler` |
| README.md:62 | ...the reconciliation service repairs divergence against canonical aggregate state. | ...the reconciliation service repairs divergence against the projection's own applied-event log up to a pinned offset (see [ranking-consistency.md](docs/ranking-consistency.md) section 6). |
| docs/traceability-matrix.md M3 Status | ...capacity check not transactional, member count includes inactive | ...capacity check not transactional, member count includes inactive; no production notification-log poller; reconciliation rebuilds from the projection's applied-event log |
| docs/traceability-matrix.md (top) | (latest changelog was 2026-10-25) | New changelog line dated 2026-10-26 naming these corrections. |

### Notes

- **Diagram edge:** the edge label changed, and so did its source node, from `Aggregate` to `LeaderboardDB`. The old source was the wrong claim; reconciliation reads the projection's own log. `LeaderboardDB` already has the `Reconciler <--> LeaderboardDB` edge. I did not render the mermaid diagram.
- **Helper callers:** `is_inactive` is called only inside `merge_undersized_cohorts`, and `merge_undersized_cohorts` is called only from tests. Both statements in §2 hold for production code.
- **`xp_amount`:** the key exists in `src/state_machine/rulesets.json`.
- **Unchanged:** `docs/ranking-consistency.md` §6 already described the applied-event log and pinned offset correctly.

### Test result

`python -m pytest -q --ignore=tests/packaging`:

```
362 passed, 4 skipped in 8.51s
```

## Step D3

Edited only `README.md`. The wording matches the Status column of `docs/traceability-matrix.md`.

### Milestone Status table

| Row | Before | After |
|---|---|---|
| M1 | ☐ (Updated schemas for all the events) | ☐ (partial: lesson_completed only; ID binding, late window, rule selection tested) |
| M3 | ☑ (SQLite projections, deterministic ties, differential reconciliation AT-7, latency benchmarks) | ☐ (leaderboard duplicate, restart, offset and time handling tested; known gaps: cohort merge helper not wired in, capacity check not transactional, member count includes inactive; no production notification-log poller; reconciliation rebuilds from the projection's applied-event log) |
| M5 | ☐ | ☐ (in-app and simulated-push slice implemented; email adapter not implemented) |
| M7 | ☐ | ☐ (assignment and metric implemented; simulator is a stub; exposure logging not implemented) |

M2 (☑, slice done), M4, M6 and M8 are unchanged. M2 keeps ☑ because the table row already says "slice done". The matrix status is "slice done (...)", and I did not treat that as a reason to change it. Say if you want M2 shown as ☐.

### Mermaid diagram

| Before | After |
|---|---|
| `Notifications["Notification engine"]:::deferred` | `Notifications["Notification engine"]` |
| `Projections -. future consumers .-> Notifications` | `Projections --> Notifications` |

`Abuse` stays `:::deferred` with its dashed "future consumers" edge. The diagram was not rendered.

### Planned Placeholder Tests

`tests/notifications/test_duplicate_proposals.py` is not a placeholder: it has one real test, `test_duplicate_proposals`, with no skip marker. The row `| test_duplicate_proposals | M5 | planned placeholder |` was removed.

Other rows I did not touch but that look stale: `test_notification_fatigue` (3 real tests), `test_opt_out_enforcement` (1), `test_quiet_hour_delay` (2) and `test_retry_dlq` (3) also contain real, unskipped tests. `test_exposure_is_logged_only_after_treatment_surface_view` exists as a function in `tests/experiments/test_deterministic_assignment.py:72`; I did not check whether it is a placeholder. `test_point_farming`, `test_full_state_rebuild` and `test_assignment_vs_exposure` are skipped, so their rows are accurate.

### Test result

`python -m pytest -q --ignore=tests/packaging`:

```
362 passed, 4 skipped in 8.49s
```

## Step D4

Edited only `docs/traceability-matrix.md`, with the three approved changes. No other AT was touched, and the 72h late window and every other number are unchanged.

### Before and after

| AT | Before | After |
|---|---|---|
| AT-1 | ...when the identical event (same dedup_key) is re-ingested **within the 7-day window**, then it is **silently discarded** and XP_total remains unchanged. | ...when the identical event (same dedup_key) is re-ingested **regardless of elapsed time (the event_id binding is permanent)**, then it is **recorded as a duplicate with no change to XP_total** and XP_total remains unchanged. |
| AT-3 | ...when an event with occurred_at = 2026-09-21 **arrives at ingested_at = 2026-09-24** (within 72h), then **a micro-replay is triggered from 2026-09-21 forward**. | ...when an event with occurred_at = 2026-09-21 **arrives, received by the server at 2026-09-24 (trusted receive clock)** (within 72h), then **the learner state is recomputed from the full event log and reflects chronological order**. |
| AT-7 | ...**when the daily reconciliation job runs**, then it applies a differential correction... | ...**when the reconciliation job is invoked (scheduling is not yet implemented)**, then it applies a differential correction... |
| top of file | (latest changelog was 2026-10-26, wording corrections) | New changelog line dated 2026-10-26 naming the three AT edits. |

### Notes

- **AT-3 grammar:** to keep the sentence grammatical I dropped the word "at" before your replacement phrase, so it reads "arrives, received by the server at 2026-09-24". The replacement text is otherwise verbatim.
- **AT-1 wording:** the resulting sentence repeats itself slightly ("no change to XP_total and XP_total remains unchanged"), because I substituted your wording literally and left the rest of the sentence alone.
- **Still open from the Step H3 list:** AT-1 still says "same dedup_key" as the duplicate identity, although the code detects duplicates by event_id and completion_id (`dedup_key` is only validated). It was not in this approval, so it was not changed.

### Test result

`python -m pytest -q --ignore=tests/packaging`:

```
362 passed, 4 skipped in 8.52s
```

### Follow-up to Step D4: wording polish

Two wording side effects from D4 were fixed in `docs/traceability-matrix.md`. No numbers or meaning changed.

| AT | Before | After |
|---|---|---|
| AT-1 | ...then it is recorded as a duplicate with no change to XP_total and XP_total remains unchanged. | ...then it is recorded as a duplicate and XP_total remains unchanged. |
| AT-3 | ...an event with occurred_at = 2026-09-21 arrives, received by the server at 2026-09-24 (trusted receive clock) (within 72h), then... | ...an event with occurred_at = 2026-09-21 is received by the server at 2026-09-24 (trusted receive clock, within 72h), then... |
