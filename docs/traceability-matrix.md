# Traceability & Acceptance Tests

## 8.1 Milestone Definitions

| Milestone | Definition |
|---|---|
| M1 | Peak-load ingestion & idempotency |
| M2 | State rebuild & recovery |
| M3 | Leaderboard consistency & latency |
| M4 | Experimentation Engine - deterministic assignment, exposure tracking, propensity logging |
| M5 | Notification reliability & DLQ routing |
| M6 | Abuse detection efficacy |
| M7 | Integration & Recovery Drill - full cross-consumer replay after simulated failure |
| M8 | Full event-log replay / end-to-end rebuild |

## 8.2 Traceability Matrix

| Milestone | Acceptance Test(s) | Component |
|---|---|---|
| M1 | AT-1 | Ingestion API + envelope schema |
| M2 | AT-2, AT-3, AT-4, AT-5, AT-16 | State machine + replay |
| M3 | AT-7 | Leaderboard service (Redis) |
| M4 | AT-12, AT-13 | Experiment/bandit engine |
| M5 | AT-8, AT-9, AT-10, AT-11, AT-14 | Notification engine |
| M6 | AT-6 | Abuse rule engine |
| M7 | AT-15 (partial) | Cross-consumer integration |
| M8 | AT-2, AT-15 | Full rebuild after DB wipe |

## 8.3 Acceptance Tests

1. **Duplicate Event Dedup (M1):** Given learner L1 submits lesson.completed for l_492 and receives XP_awarded = E (first attempt, N = 0), when the identical event (same dedup_key) is re-ingested within the 7-day window, then it is silently discarded and XP_total remains unchanged.
2. **Deterministic Replay Checksum (M2, M8):** Given a fixed sequence of 100 events for learner L1, when the event log is replayed twice from offset 0 via f(St, e) = St+1, then the resulting state vectors {XP, StreakCount, Freezes, RuleVersion} are bit-identical both times.
3. **Out-of-Order Within 72h (M2):** Given learner L1’s last processed event has occurred_at = 2026-09-22, when an event with occurred_at = 2026-09-21 arrives at ingested_at = 2026-09-24 (within 72h), then a micro-replay is triggered from 2026-09-21 forward.
4. **Late Event Streak Recalc (M2):** Given StreakCount = 5, Freezes = 1, last active 2026-09-20, when a valid event with occurred_at = 2026-09-22 (∆d = 2) arrives within 72h, then Freezes ≥ (∆d − 1) = 1 holds, so StreakCount = 6 and Freezes = 0.
5. **Historical Rule Versioning (M2):** Given xp_awarded was stamped with rule_version = v1 when E = 100, when the global rule config changes to E = 150 and replay occurs, then the historical event still yields XP_awarded = 100, not 150.
6. **Point Farming / Decay Cap (M6):** Given E = 100, K = 20, learner completes lesson l_492 for the 3rd time (N = 2), when XP is calculated, then XP_awarded = max(0, 0.5(100) − 20(1)) = 30, and if this pattern pushes a cohort’s decayed-XP share above 15% of weekly total, the policy is flagged.
7. **Leaderboard Reconciliation (M3):** Given Redis and Postgres diverge on L1’s score by a dropped event, when the daily reconciliation job runs, then it applies a differential ZADD correction (not a full flush) and resolves any tie using occurred_at of the first event reaching that score, then subject_id.
8. **Quiet-Hour Delay (M5):** Given L1’s timezone is Asia/Kolkata with quiet hours 22:00–07:00, when a streak-risk candidate generates at 23:10 local, then delivery reschedules to 07:00 next day and counts against that day’s 5-message cap.
9. **Duplicate Notification Proposals (M5):** Given proposal_id = p_001 was already generated for L1, when the policy attempts to generate a second candidate for the same trigger, then it is deduplicated by proposal_id before dispatch.
10. **Retry & DLQ (M5):** Given a notification delivery attempt fails due to a provider error, when retries are exhausted, then the message routes to the DLQ and is recoverable after the channel failure is resolved.
11. **Opt-Out Enforcement (M5):** Given L1 has opted out of push, when a candidate is generated for L1, then Notification Failure is emitted with failure_reason = opt_out, not a delivery attempt.
12. **Deterministic Experiment Assignment (M4):** Given learner L1 and experiment exp_007 with a fixed hash algorithm v2, when assignment runs twice, then L1 is bucketed into the same variant both times, with the propensity score logged on the event.
13. **Assignment vs. Exposure (M4):** Given L1 is assigned to variant_a for exp_007, when L1 has not yet opened the app, then only Experiment Assignment exists. Experiment Exposure is emitted only once L1 actually views the treatment surface.
14. **Notification Fatigue Guardrail (M5):** Given L1 has already received 5 push notifications in the current 24h window, when a 6th candidate is generated, then it is blocked and the triggering experiment variant is flagged for guardrail violation.
15. **Full State Rebuild (M2, M7, M8):** Given a simulated database wipe for the full synthetic population, when the event log is replayed from offset 0, then all learners’ {XP, StreakCount, Freezes} match their pre-wipe values exactly, and leaderboard/cohort projections rebuild to the same ranks.
16. **Badge Idempotency (M2):** Given learner L1 meets the condition for badge bid (e.g., week_warrior_v1) and badge_awarded is emitted, when the triggering state condition breaks and is rebuilt to meet the criteria again, then no second badge_awarded event for bid is emitted and BadgesIssued contains exactly one instance of bid.
