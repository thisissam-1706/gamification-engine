# Traceability & Acceptance Tests

> **Changelog (2026-11-07):** M3 cohort admission is now atomic: `assign_cohort` reads the member count, checks capacity and writes the membership inside one write-locked SQLite transaction (`BEGIN IMMEDIATE`), so two connections cannot both take the last seat (`tests/leaderboard/test_cohort_capacity_race.py`). The M3 known gap "capacity check not transactional" was removed from the M3 row; the other known gaps stand. The AT-7 text and all thresholds are unchanged.

> **Changelog (2026-11-07):** AT-18 written and passing (`tests/simulator/test_policy_comparison.py`, simulator-backed on synthetic data): assignment by the deterministic hash, the headline mastery rate recomputed independently, missing outcomes counted as not mastered, immature learners excluded and reported separately, fatigue metrics for both arms (messages per learner per day added to the arm report), and directional and sample-ratio checks across seeds 1 to 5. The M7 row status was updated and an M7 clarification was added; the AT-18 acceptance wording is unchanged. Eligibility, decision and delivery in this test are simulator stand-ins, not calls into M4/M5. No thresholds changed.

> **Changelog (2026-11-06):** M7 documented as implemented for experiment assignment and exposure on synthetic fixtures. AT-13 written and passing (`tests/experiments/test_assignment_vs_exposure.py`, `tests/experiments/test_deterministic_assignment.py`): assignment is stored as `ExperimentAssigned`, treatment-surface exposure as `ExperimentExposed` via the new ingested source event `learning.surface_viewed`. The M7 row status and its simulator wording (now consistent with `simulator/SPEC.md`: partially implemented, pilot v1) were updated, and an M7 clarification was added; the AT-13 acceptance wording is unchanged. AT-18 is still to be written. No thresholds changed.

> **Changelog (2026-11-02):** AT-15 clarified (acceptance wording unchanged): the wipe now destroys all derived state and rebuilds with a fresh application instance; the synthetic population size is stated. No thresholds changed.

> **Changelog (2026-11-02):** M4 documented as implemented for synthetic fixtures. AT-17 written and passing (`tests/interventions/test_learning_remediation.py`) with its wording made specific (earliest-first-attempt rule, 14-day window, 7-day deadline, `remediated_within_window`, expiry sweep with `expired_at` = deadline; policy and sweep invoked, not scheduled). The M4 row status, the M1 status (now also ingests `learning.checkpoint_attempted`) and the "Reused, configured, new" table (remediation config, policy/runner, quest definitions) were updated to match. No other acceptance criteria, numbers or thresholds changed.

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
| M1 Canonical engagement events | AT-1 | Canonical event envelope and ingestion | Sampadram | Madhava Asrith | partial (lesson_completed and checkpoint_attempted ingested; ID binding, late window, rule selection tested) | W2-3 |
| M2 Deterministic gamification | AT-2, AT-3, AT-4, AT-5, AT-16 | Deterministic state machine and replay | Sampadram | Madhava Asrith | slice done (active readers agree with aggregate and replay) | W2-4 |
| M3 Cohorts and leaderboards | AT-7 | Cohort projections and leaderboard service | Madhava Asrith | Sampadram | leaderboard duplicate, restart, offset and time handling tested; known gaps: cohort merge helper not wired in, member count includes inactive; no production notification-log poller; reconciliation rebuilds from the projection's applied-event log | W3-4 |
| M4 Intervention/quest policy (learning-oriented remediation) | AT-17 | Intervention and quest policy | Madhava Asrith | Sampadram | implemented for synthetic fixtures: checkpoint_attempted ingestion, rule-based remediation policy with stored decisions, remediation_v1 quest (remediation completions only), deadline expiry sweep; policy and sweep invoked, not scheduled; no notification hook | W3-4 |
| M5 Multi-channel notifications | AT-8, AT-9, AT-10, AT-11, AT-14 | Multi-channel notification engine | Madhava Asrith | Sampadram | in-app and simulated-push slice implemented; email adapter not implemented | W4 |
| M6 Abuse detection | AT-6 | Abuse rule engine | Sampadram | Madhava Asrith | implemented for synthetic fixtures; flag-only (opt-in decay ruleset v2, retroactive detectors, cohort guardrail); thresholds unvalidated on real data; no `confirmed` status or XP correction | W4-5 |
| M7 Experimentation and metrics | AT-12, AT-13, AT-15 (partial), AT-18 | Experimentation and metrics pipeline; simulator: partially implemented (pilot v1, see `simulator/SPEC.md`) | Sampadram | Madhava Asrith | experiment assignment recorded as `ExperimentAssigned` and treatment-surface exposure as `ExperimentExposed` (AT-12 and AT-13 passing; synthetic fixtures; control learners never produce exposure events); exposure is not yet consumed by the metric; simulator partially implemented (pilot v1); AT-18 passing (simulator-backed; eligibility, decision and delivery are simulator stand-ins, not calls into M4/M5) | protocol W2 / run W5 |
| M8 Scale and recovery | AT-2, AT-15 | Full rebuild and recovery orchestration | Madhava Asrith | Sampadram | implemented & passing (AT-15 multi-learner full-state rebuild, leaderboard p50/p95/p99 latency bounds, DLQ outage recovery drain, raw-log experiment reproduction) | W5-6 |

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
| Remediation policy settings (14-day frequency window, 7-day quest deadline, quest id and definition version, policy version) | configured | `RemediationConfig` in `src/interventions/config.py` |
| Ingestion late window and clock | configured | `IngestConfig(late_window, clock)`, `DEFAULT_LATE_WINDOW = 72h` in `src/ingestion/config.py` |
| Experiment exp_007 | configured | `experiments/exp_007.yaml`, checked against code by `tests/experiments/test_exp007_yaml_matches_code.py` |
| Cohort size bounds | configured | `min_size: int = 500`, `max_size: int = 5000` in `src/leaderboard/models.py` |
| Notification guardrails (consent, fatigue cap, quiet hours, TTL) | configured | `src/notifications/guardrails.py` (e.g. `max_cap: int = 5`) |
| Learner rules and readers | new | `Learner`, `LearnerApplication` in `src/state_machine/learner.py`; readers in `src/state_machine/projection.py` |
| Ingestion evidence store | new | `EvidenceStore` in `src/ingestion/evidence.py` |
| Leaderboard projection, store and reconciliation | new | `src/leaderboard/projection.py`, `src/leaderboard/store.py`, `src/leaderboard/reconciliation.py` |
| Notification dispatcher | new | `NotificationDispatcher` in `src/notifications/dispatcher.py` |
| Remediation policy and runners | new | `decide_remediation`, `RemediationDecision` in `src/interventions/policy.py`; `run_remediation`, `run_quest_expiry` in `src/interventions/service.py` |
| Quest definitions (remediation_v1) | new | `quest_definition_for`, `QuestDefinition` in `src/state_machine/quest_definitions.py` (rule-set quests fall back to `rulesets.json`) |
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
17. **Learning-Oriented Remediation Quest (M4):** Given learner L1's first attempt on checkpoint assessment A (earliest by occurred_at, then attempt_id) fails, and L1 received no remediation quest assignment (an assign decision, which assigns the quest in the same save) in the previous 14 days, when the remediation policy runs, then a stored assign decision creates a remediation_v1 quest with a deadline 7 days after the decision. If L1 received an assignment less than 14 days earlier, the policy stores an explicit no_action decision with reason remediated_within_window. An unfinished quest is expired by the expiry sweep once its deadline has passed, with expired_at equal to the deadline. The policy and the sweep are invoked, not yet scheduled.
18. **Policy Comparison (M7):** Given a synthetic population assigned by deterministic hash to treatment (streak reminders) or control (no reminders), when outcomes are computed, then the primary metric is the percent of all assigned eligible learners who pass their first eligible checkpoint within 14 days, with missing outcomes counted as not mastered, and fatigue metrics are reported for both groups.

> **Clarification (M7, 2026-11-06; the acceptance wording above is unchanged):** In AT-13 "has not yet opened the app" means no `learning.surface_viewed` event has been accepted for L1. Assignment is stored on the learner as `ExperimentAssigned` (variant from the hash, `hash_v2`, propensity, assigned time); the first assignment per experiment stays. Exposure is the ingested source event `learning.surface_viewed` from `learning-svc`, stored as `ExperimentExposed`. The variant and the assignment reference (`experiment_assignment:<exp_id>`) are derived by the engine from the stored assignment; a payload that carries `variant` or `assignment_ref` is rejected as `schema_invalid`. For `exp_007` there is exactly one treatment surface, `streak_risk_reminder`, and it belongs to `variant_a`. Every distinct `view_id` is recorded, and the learner's first exposure is derived as the earliest by `(exposed_at, view_id)`, independent of arrival order. A control (`variant_b`) learner's view is rejected (`no_treatment_surface_for_variant`), so control learners never produce an exposure event. Other refusals are recorded as rejected evidence and store nothing: `no_assignment`, `not_treatment_surface` and `exposure_before_assignment` (the view's `occurred_at` is earlier than the assignment time). The 72 hour late window applies to views like any other ingested event. A view for a learner with no aggregate at all raises an error rather than being recorded as rejected. The test uses synthetic fixtures, and exposure is not yet consumed by the metric.

> **Clarification (M7, 2026-11-07; the acceptance wording above is unchanged):** The AT-18 test is `tests/simulator/test_policy_comparison.py`. Assignment is the deterministic hash (`assign(learner_id, "exp_007")`, propensity 0.5). The headline rate counts missing outcomes as not mastered, but leaves out immature learners (those whose 14-day window has not closed at the data cutoff) and reports them separately. Messages per learner per day is `messages_sent / (eligible learners x 14)` for both arms, where messages sent counts delivered plus provider_failed. The model is synthetic: eligibility, decision and delivery are simulator stand-ins, and the directional checks (positive, harmful and null settings) use seeds 1 to 5 at n=4000. Results do not demonstrate real-world causality.

> **Clarification (M8, 2026-11-02; the acceptance wording above is unchanged):** In AT-15 the "database wipe" destroys all derived state (the leaderboard read model and the projections built on it) and rebuilds it from the retained event log using a fresh application instance opened on the same events database. The event log itself is not wiped. Before the wipe, XP, streak and freezes are read from the live aggregates; after the rebuild they are derived by replay, and leaderboard ranks must match exactly. The synthetic population is small: AT-15 uses 15 learners in 2 cohorts, 12 events per learner (5% duplicates, 5% late), seed 42, which is 612 notification-log entries replayed (`total_events`) and 612 stored domain events across all aggregates (`domain_event_count`); the wipe tests use 8 learners. This demonstrates correctness of the rebuild at that size, not scale.

> **Clarification (M6, 2026-10-26; the acceptance wording above is unchanged):** AT-1 calls the first attempt "N = 0" and AT-6 calls the third completion "N = 2", so N is the number of **prior** accepted completions of the same `lesson_id` by the same learner whose `completion_kind` is `lesson` or absent. `review` and `remediation` completions neither decay nor count toward N. With ruleset `v2` (E = 100, 50% first repeat, step 20) XP is 100, 50, 30, 10, 0 for N = 0, 1, 2, 3, 4+. "Decayed XP" in the 15% cohort threshold is read as XP awarded on repeat completions (`repeat_index` >= 1). The decay rule set is opt-in and the "policy is flagged" outcome is a report from `cohort_gaming_report`, not an automatic action.
