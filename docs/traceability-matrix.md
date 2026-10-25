# Traceability & Acceptance Tests

> **Changelog (2026-10-26):** Aligned three acceptance tests with the implementation (approved wording changes; the 72h late window and all other numbers unchanged): AT-1 (duplicates detected regardless of elapsed time via the permanent event_id binding; recorded as a duplicate, not silently discarded), AT-3 (arrival time is the server's trusted receive clock; state is recomputed from the full event log, not by a micro-replay), AT-7 (reconciliation is invoked, not yet scheduled daily).

> **Changelog (2026-10-26):** Wording-only corrections so the docs match the code: inactivity pruning and undersized-cohort merging are specified but not wired in; XP is a fixed per-ruleset amount and the decay model is planned (M6); the reconciler repairs against the projection's applied-event log; M3 status lists the missing notification-log poller. No numbers, thresholds or acceptance criteria changed.

> **Changelog (2026-10-26):** Wording-only fixes of stale statements, with no numbers, thresholds or acceptance criteria changed: `metrics-framework.md` now names the SQLite leaderboard store instead of Redis sorted sets; `problem-boundaries.md` says leaderboard projections are SQLite-backed (M3) and that rule-based abuse detection is planned (M6) but not implemented; `README.md` no longer lists abuse detection as implemented and lists in-app and simulated-push notifications as implemented.

> **Changelog (2026-10-25):** Added Owner, Peer reviewer and evidence-based Status for M1–M8 and a "Reused, configured, new" component table.

> **Changelog (2026-10-12):** Linked the quiet-hours and daily-cap acceptance criteria to their centralized problem-boundaries definitions.

## 8.1 Milestone Definitions

| Milestone | Definition |
|---|---|
| M1 | Canonical engagement events |
| M2 | Deterministic gamification |
| M3 | Cohorts and leaderboards |
| M4 | Intervention/quest policy |
| M5 | Multi-channel notifications |
| M6 | Abuse detection |
| M7 | Experimentation and metrics |
| M8 | Scale and recovery |

## 8.2 Traceability Matrix

| Milestone | Acceptance tests | Component | Owner | Peer reviewer | Status | Target week |
|---|---|---|---|---|---|---|
| M1 Canonical engagement events | AT-1 | Canonical event envelope and ingestion | Sampadram | Madhava Asrith | partial (lesson_completed only; ID binding, late window, rule selection tested) | W2-3 |
| M2 Deterministic gamification | AT-2, AT-3, AT-4, AT-5, AT-16 | Deterministic state machine and replay | Sampadram | Madhava Asrith | slice done (active readers agree with aggregate and replay) | W2-4 |
| M3 Cohorts and leaderboards | AT-7 | Cohort projections and leaderboard service | Madhava Asrith | Sampadram | leaderboard duplicate, restart, offset and time handling tested; known gaps: cohort merge helper not wired in, capacity check not transactional, member count includes inactive; no production notification-log poller; reconciliation rebuilds from the projection's applied-event log | W3-4 |
| M4 Intervention/quest policy (learning-oriented remediation) | AT-17 (to be written) | Intervention and quest policy | Madhava Asrith | Sampadram | not started | W3-4 |
| M5 Multi-channel notifications | AT-8, AT-9, AT-10, AT-11, AT-14 | Multi-channel notification engine | Madhava Asrith | Sampadram | in-app and simulated-push slice implemented; email adapter not implemented | W4 |
| M6 Abuse detection | AT-6 | Abuse rule engine | Sampadram | Madhava Asrith | not started (src/abuse is empty; point-farming test skipped) | W4-5 |
| M7 Experimentation and metrics | AT-12, AT-13, AT-15 (partial), AT-18 (to be written) | Experimentation and metrics pipeline; simulator: stub, not implemented, planned for milestone M7 | Sampadram | Madhava Asrith | assignment and metric implemented; simulator is a stub; exposure logging not implemented | protocol W2 / run W5 |
| M8 Scale and recovery | AT-2, AT-15 | Full rebuild and recovery orchestration | Madhava Asrith | Sampadram | per-learner rebuild and leaderboard replay tested; full-population orchestration not implemented | W5-6 |

## Reused, configured, new

Built from the imports in `src/`, the config files and the tests. "Evidence" names the file or import that shows the category.

| Component | Category | Evidence |
|---|---|---|
| eventsourcing 9.5.5 event store | reused | `src/state_machine/learner.py` (`Application`, `Aggregate`, `event`); pinned in `pyproject.toml` |
| eventsourcing notification log | reused | `app.notification_log.select(...)` in `tests/leaderboard/test_leaderboard_e2e.py`; `src/leaderboard/projection.py` imports `Mapper`, `JSONTranscoder` from `eventsourcing.persistence` |
| SQLite | reused | `import sqlite3` in `src/ingestion/evidence.py`, `src/state_machine/projection.py`, `src/leaderboard/store.py`, `src/notifications/store.py`; `eventsourcing.sqlite` persistence module |
| jsonschema | reused | `from jsonschema import Draft7Validator` in `src/ingestion/service.py`, `src/ingestion/validation.py` |
| importlib.resources | reused | `from importlib.resources import files` in `src/ingestion/service.py`, `src/ingestion/validation.py`, `src/state_machine/rules.py` |
| zoneinfo | reused | `from zoneinfo import ZoneInfo` in `src/state_machine/rules.py`, `src/ingestion/validation.py`, `src/notifications/guardrails.py` |
| pytest | reused | `[project.optional-dependencies] dev` and `[tool.pytest.ini_options]` in `pyproject.toml` |
| Versioned rule sets | configured | `src/state_machine/rulesets.json`, loaded by `load_rulesets` in `src/state_machine/rules.py` |
| Ingestion late window and clock | configured | `IngestConfig(late_window, clock)`, `DEFAULT_LATE_WINDOW = 72h` in `src/ingestion/config.py` |
| Experiment exp_007 | configured | `experiments/exp_007.yaml`, checked against code by `tests/experiments/test_exp007_yaml_matches_code.py` |
| Cohort size bounds | configured | `min_size: int = 500`, `max_size: int = 5000` in `src/leaderboard/models.py` |
| Notification guardrails (consent, fatigue cap, quiet hours, TTL) | configured | `src/notifications/guardrails.py` (e.g. `max_cap: int = 5`) |
| Learner rules and readers | new | `Learner`, `LearnerApplication` in `src/state_machine/learner.py`; readers in `src/state_machine/projection.py` |
| Ingestion evidence store | new | `EvidenceStore` in `src/ingestion/evidence.py` |
| Leaderboard projection, store and reconciliation | new | `src/leaderboard/projection.py`, `src/leaderboard/store.py`, `src/leaderboard/reconciliation.py` |
| Notification dispatcher | new | `NotificationDispatcher` in `src/notifications/dispatcher.py` |
| Mastery metric | new | `mastery_report`, `first_eligible_checkpoint_mastery` in `src/experiments/metrics.py` |

## 8.3 Acceptance Tests

1. **Duplicate Event Dedup (M1):** Given learner L1 submits lesson.completed for l_492 and receives XP_awarded = E (first attempt, N = 0), when the identical event (same dedup_key) is re-ingested regardless of elapsed time (the event_id binding is permanent), then it is recorded as a duplicate and XP_total remains unchanged.
2. **Deterministic Replay Checksum (M2, M8):** Given a fixed sequence of 100 events for learner L1, when the event log is replayed twice from offset 0 via f(St, e) = St+1, then the resulting state vectors {XP, StreakCount, Freezes, RuleVersion} are bit-identical both times.
3. **Out-of-Order Within 72h (M2):** Given learner L1’s last processed event has occurred_at = 2026-09-22, when an event with occurred_at = 2026-09-21 is received by the server at 2026-09-24 (trusted receive clock, within 72h), then the learner state is recomputed from the full event log and reflects chronological order.
4. **Late Event Streak Recalc (M2):** Given StreakCount = 5, Freezes = 1, last active 2026-09-20, when a valid event with occurred_at = 2026-09-22 (∆d = 2) arrives within 72h, then Freezes ≥ (∆d − 1) = 1 holds, so StreakCount = 6 and Freezes = 0.
5. **Historical Rule Versioning (M2):** Given xp_awarded was stamped with rule_version = v1 when E = 100, when the global rule config changes to E = 150 and replay occurs, then the historical event still yields XP_awarded = 100, not 150.
6. **Point Farming / Decay Cap (M6):** Given E = 100, K = 20, learner completes lesson l_492 for the 3rd time (N = 2), when XP is calculated, then XP_awarded = max(0, 0.5(100) − 20(1)) = 30, and if this pattern pushes a cohort’s decayed-XP share above 15% of weekly total, the policy is flagged.
7. **Leaderboard Reconciliation (M3):** Given the read model diverges on L1’s score by a dropped event or phantom entry, when the reconciliation job is invoked (scheduling is not yet implemented), then it applies a differential correction (pinpoint INSERT, UPDATE, or DELETE without full table flush) and resolves any tie using normalized UTC occurred_at of the event first reaching that score, then subject_id.
8. **Quiet-Hour Delay (M5):** Given L1’s timezone is Asia/Kolkata and the [defined quiet-hours and daily-cap constraints](problem-boundaries.md#notification-channels-and-constraints), when a streak-risk candidate generates at 23:10 local, then delivery reschedules to 08:00 next day and counts against that day’s cap.
9. **Duplicate Notification Proposals (M5):** Given proposal_id = p_001 was already generated for L1, when the policy attempts to generate a second candidate for the same trigger, then it is deduplicated by proposal_id before dispatch.
10. **Retry & DLQ (M5):** Given a notification delivery attempt fails due to a provider error, when retries are exhausted, then the message routes to the DLQ and is recoverable after the channel failure is resolved.
11. **Opt-Out Enforcement (M5):** Given L1 has opted out of push, when a candidate is generated for L1, then Notification Failure is emitted with failure_reason = opt_out, not a delivery attempt.
12. **Deterministic Experiment Assignment (M7):** Given learner L1 and experiment exp_007 with a fixed hash algorithm v2, when assignment runs twice, then L1 is bucketed into the same variant both times, with the propensity score logged on the event.
13. **Assignment vs. Exposure (M7):** Given L1 is assigned to variant_a for exp_007, when L1 has not yet opened the app, then only Experiment Assignment exists. Experiment Exposure is emitted only once L1 actually views the treatment surface.
14. **Notification Fatigue Guardrail (M5):** Given L1 has already reached the [daily notification cap](problem-boundaries.md#notification-channels-and-constraints) in the current 24h window, when another candidate is generated, then it is blocked and the triggering experiment variant is flagged for guardrail violation.
15. **Full State Rebuild (M2, M7, M8):** Given a simulated database wipe for the full synthetic population, when the event log is replayed from offset 0, then all learners’ {XP, StreakCount, Freezes} match their pre-wipe values exactly, and leaderboard/cohort projections rebuild to the same ranks.
16. **Badge Idempotency (M2):** Given learner L1 meets the condition for badge bid (e.g., week_warrior_v1) and badge_awarded is emitted, when the triggering state condition breaks and is rebuilt to meet the criteria again, then no second badge_awarded event for bid is emitted and BadgesIssued contains exactly one instance of bid.
17. **Learning-Oriented Remediation Quest (M4) (test to be written):** Given learner L1 failed their first eligible checkpoint attempt and has received no remediation quest in the frequency window, when the policy runs, then a remediation quest is assigned with a deadline. If L1 already received one inside the window, the outcome is an explicit no-action decision, and an unfinished quest expires at its deadline.
18. **Policy Comparison (M7) (test to be written):** Given a synthetic population assigned by deterministic hash to treatment (streak reminders) or control (no reminders), when outcomes are computed, then the primary metric is the percent of all assigned eligible learners who pass their first eligible checkpoint within 14 days, with missing outcomes counted as not mastered, and fatigue metrics are reported for both groups.
