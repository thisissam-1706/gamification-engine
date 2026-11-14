# Metrics Framework

> **Changelog (2026-10-12):** Linked notification constraints to the shared problem boundaries and replaced contextual-bandit allocation with the implemented fixed 50/50 assignment.

This framework dictates how the experimentation engine evaluates cohort interventions. It establishes distinct boundaries between actual learning progress, superficial engagement, and platform abuse.

## 5.1 Primary Learning Outcome

* **First eligible checkpoint mastery:** The core objective is educational progression, not click volume. This metric is the share of assigned eligible learners who pass their first eligible checkpoint within 14 days of assignment; the missing-outcome rate is reported separately.

## 5.2 Long-Term Retention & Spaced Repetition

Instead of measuring simple application logins, retention is evaluated through the completion and accuracy of Spaced Repetition Review Lessons.
* **Milestone Reviews:** Scheduled systematically at Day 7, Day 15, and Day 30 post-cohort assignment.
* **Intermittent Reviews:** Triggered dynamically after a defined volume of lessons.
  * Measurement: Tracking the accuracy on these specific review channels serves a dual purpose: measuring continued platform engagement (retention) while simultaneously gauging the decay of the learner’s mastery over time.
* **Remediation Conversion Rate:** The percentage of lapsed users who successfully complete a remediation quest and generate a valid learning event the following day.

## 5.3 Guardrails (Experiment Invalidation Criteria)

Experiments that improve engagement at the cost of learner well-being or system integrity are automatically marked as failed.
* **The Notification Fatigue & Escalation Limit:** Enforces the [daily notification cap and quiet hours](problem-boundaries.md#notification-channels-and-constraints). To support habit formation, “streak at risk” notification intervals decrease exponentially (firing more rapidly) leading up to localized midnight, provided the shared notification constraints are respected (quiet hours take absolute precedence, truncating any escalating schedule upon entry).
* **The Gaming Threshold:** If > 15% of a cohort’s total weekly XP generation originates from decayed (repeated) lessons, the specific intervention policy is flagged for reward abuse and disqualified.
  * *Implementation note (M6):* `cohort_gaming_report` in `src/abuse/guardrail.py` computes the weekly repeat-XP share per cohort ("decayed XP" is read as XP awarded on completions with `repeat_index` >= 1) and `breached = share > 0.15` (threshold in `abuse_rules.json`). It only reports and never changes an award or an assignment. The exp_007 runner (`run_experiment`, see "Real-log experiment pipeline (M7)") calls it once per ISO week with the variant as the group key; a breach in either arm sets `valid` to False with the reason `gaming_share_breach`. That verdict in the report is the only consequence: nothing disqualifies a policy automatically.
* During the [defined quiet-hours window](problem-boundaries.md#notification-channels-and-constraints), all push and email notification deliveries are blocked regardless of priority or experiment policy.

## 5.4 Controlled Experiment Specification

**Formal Definition (Experiment System Contract)**
Every controlled experiment deployed within the platform is formally defined as a tuple:
`E = ⟨exp_id, V, A, Mprimary, G, C⟩`
where:
* **Experiment Identifier (exp_id):** A unique, immutable string key that scopes state transitions, variant hashing, and exposure logging.
* **Variant Set (V):** A set of mutually exclusive treatment policies
  `V = {v0, v1, ..., vk}`
  where v0 is strictly designated as the control policy (baseline/no-intervention).
* **Deterministic Assignment Function (A):** A pure function mapping a learner identifier (subject_id) and exp_id to a variant using uniform hash bucketing:
  `A(subject_id, exp_id) = int.from_bytes(sha256(json.dumps([subject_id, exp_id], separators=(',', ':')).encode('utf-8')).digest()[:8], 'big') mod 100`
  i.e. the first 8 bytes of the SHA-256 of the compact UTF-8 JSON array [subject_id, exp_id], read as a big-endian unsigned integer, mod 100 (versioned as hash_v2). The JSON array deliberately avoids plain concatenation, which collides: ('ab', 'c') and ('a', 'bc') would both hash 'abc'.
* **Primary Metric (Mprimary):** The core educational or behavioral outcome measure used to evaluate policy efficacy.
* **Guardrails (G):** A set of systemic constraints (e.g., daily notification caps, fatigue thresholds, decayed-XP caps) that, if violated by any treatment variant, automatically invalidate the experiment and halt policy execution.
* **Contamination & SRM Checks (C):** Automated statistical controls, including a chi-square (χ2) goodness-of-fit test on assignment distribution, to catch Sample-Ratio Mismatch (SRM).

**Implementation Example (exp_007)**
* **Experiment Identifier (exp_id):** "exp_007" (Streak-Risk Reminder Policy vs. Control).
* **Variants (V):**
  * variant_a (Treatment): Fires an automated, exponential-interval push reminder during the 4 hours preceding localized midnight when a learner’s streak is at risk.
  * variant_b (Control): Suppresses streak-risk interventions; observes baseline learner behavior.
* **Assignment & Exclusions:** 50/50 split via `bucket = int.from_bytes(sha256(json.dumps([subject_id, exp_id], separators=(',', ':')).encode('utf-8')).digest()[:8], 'big') mod 100`, i.e. the first 8 bytes of the SHA-256 of the compact UTF-8 JSON array [subject_id, exp_id], read as a big-endian unsigned integer, mod 100; bucket < 50 → variant_a (propensity 0.5), otherwise variant_b. The JSON array deliberately avoids plain concatenation, which collides: ('ab', 'c') and ('a', 'bc') would both hash 'abc'. Excludes learners with platform tenure < 3 days or those who opted out of the push × streak_risk consent pair.
* **Primary Metric (Mprimary):** First eligible checkpoint mastery over a 14-day window (with Remediation Conversion Rate as the short-horizon operational signal). The 14-day window is measured from assignment; learners whose window has not closed are reported as immature and left out of the denominator, and the missing-outcome rate is reported separately.
* **Guardrails (G):** The experiment is automatically marked as invalidated if variant_a breaches the [daily notification cap](problem-boundaries.md#notification-channels-and-constraints) (§5.3) or drives cohort decayed-XP share above 15% (§5.3).
* **SRM Safeguard (C):** A daily pipeline job runs a χ2 test comparing actual assignment ratios against the expected 50/50 split (p < 0.001 triggers an SRM alert).

> **Clarification (M5, 2026-11-14; the sentence above is unchanged):** The treatment schedule is 20:00, 21:00, 21:30 and 21:45 in the learner's local time (the gaps halve: 60, 30, 15 minutes; the times are one constant, `REMINDER_TIMES_LOCAL` in `src/notifications/streak_risk.py`). The last reminder is at 21:45 because quiet hours start at 22:00, which truncates the schedule; nothing is generated inside quiet hours or before 20:00, and a sweep sends only the latest due reminder, never a burst of older ones. Treatment can therefore use up to 4 of the 5 daily messages; the other messages a learner receives (for example a remediation notice) share the same 5-per-24h cap. The reminder sweep is run by the caller; nothing schedules it. Cap breaches of exp_007 proposals are recorded as guardrail violations per variant; the M7 runner (`run_experiment`) reads them, counts rows with reason `daily_cap_exceeded` per learner per UTC day, and a treatment-arm breach sets `valid` to False with `cap_breach_treatment` (see "Real-log experiment pipeline (M7)").

## 5.5 Fixed Policy Allocation

Notification policy experiments currently use a deterministic fixed 50/50 assignment, providing a stable baseline for comparing treatment and control outcomes. Propensity logging remains a future-proofing requirement: assignment events should retain the probability used for the selected arm so that a later move to adaptive allocation can support inverse-propensity analysis. We are not using a bandit for core reward-rule selection (XP amounts, decay rates); those stay fixed and rule-versioned.

## Experiment protocol (M7)

The primary metric is the percentage of all assigned eligible learners who pass their
first eligible checkpoint within 14 days of assignment. A `CheckpointAttempt` is
matched to its `CheckpointCompleted` event by `attempt_id` and
`assessment_id`/version. Learners who never complete the assessment count as
“not mastered”, and the missing-outcome rate is reported separately. The same
assessment is used for everyone, or results are reported by difficulty level.

Analysis is by assignment: every assigned eligible learner stays in the arm they were assigned to, whether or not they ever opened the app or viewed the treatment surface, so the two groups remain comparable and the sample-ratio check stays meaningful. Exposure (`ExperimentExposed`, recorded only when a `variant_a` learner views the treatment surface) is a secondary signal, for example to report how many treated learners actually saw the reminder. Control learners never produce an exposure event, so exposure is not compared across arms. Exposure is reported per arm as a diagnostic and never enters the mastery metric: the treatment arm reports exposed over eligible, and the control rate is None (with a note) because the exposure rules give `variant_b` no surface.

Fatigue metrics include messages per learner per day, opt-outs, and cap breaches;
in the simulator report, messages per learner per day is the messages sent (delivered plus provider-failed) divided by eligible learners times the 14-day window, reported for both arms (the real-log pipeline measures it over each learner's observed window instead, see below);
the [daily notification cap](problem-boundaries.md#notification-channels-and-constraints) applies. Assignment, eligibility, decision, delivery,
and exposure are logged as five separate events. Both groups use the same consent
and fatigue rules. Results come from synthetic data and do not demonstrate
real-world causality.

## Real-log experiment pipeline (M7)

`run_experiment` (`src/experiments/runner.py`) reports exp_007 from the stored logs. It reads the event log in one pass, reads no clock, writes nothing, and requires an explicit timezone-aware `as_of` cutoff (None or a naive time raises `ValueError`). Its inputs are the application (event log), the `ExperimentStore`, the `NotificationStore`, the XP awards, `assessment_id`, an optional `assessment_version`, `fatigue_cap` (default 5) and an optional `current_prefs`.

**What is run by hand.** Nothing here is scheduled. `scripts/run_experiment.py` is a manual entry point with three subcommands, each taking `--db PATH` (an existing SQLite file holding the events and the stores) and each run by a person or a caller: `assign` (assign every learner and record eligibility at a given instant), `sweep` (the streak-risk sweep at a given instant, with the real dispatcher and the experiment store) and `report` (write the run as JSON; exit code 0 even when the run is invalid). Every time comes from an argument and must carry a timezone. **Notification preferences are not in the event log**: they come from a JSON file (`--prefs`), `{"<learner_id>": {"timezone": "Asia/Kolkata", "opted_out_channels": ["push"], "opted_out_categories": ["streak_risk"]}}`, read by `load_preferences` (`src/experiments/prefs.py`). Missing optional keys default to UTC and no opt-outs; a bad entry (unknown channel, category or key, bad timezone, wrong type) raises an error naming the learner, and nothing is skipped silently. The learners are those with a stored Created event (`list_learner_ids`).

* **Assignment and eligibility.** Every learner is assigned through the existing assignment, and one eligibility row per learner is persisted for both arms: the stored variant, `eligible`, a reason (`missing_preferences` first, then `excluded_tenure` for under 3 days at assignment, then `excluded_opted_out` for a push or streak_risk opt-out), a snapshot of the preferences used (the two opt-outs and the timezone) and the tenure in days. The first record wins, so re-running or changing preferences later changes neither a row nor the event log. The runner reads eligibility from these rows and does not recompute it.
* **Decisions.** `run_streak_risk_sweep(..., store=...)` writes one `experiment_decisions` row per learner outcome (`sent` with the proposal and the dispatcher's fact, or `skipped` with the reason). A learner whose stored eligibility row is ineligible is skipped with `ineligible:<stored reason>` before the treatment-arm and streak checks, in both arms (a learner with no preferences entry is reported as `missing_preferences` first); a learner with no assignment is recorded with the variant `unassigned`.
* **Completions.** One completion is derived per `CheckpointAttempted` event, 1:1: the same attempt, learner, assessment id and version, `passed` copied as stored, and the attempt's own timestamp. No new event type is written. Only eligible learners' attempts for `assessment_id` count. Attempts for other assessments are left out and counted (`ignored_other_assessment_attempts`); attempts by learners with no assignment for the experiment are left out and counted separately (`ignored_unassigned_attempts`).
* **Assessment version.** If counted attempts span more than one `assessment_version`, the run is invalid with `mixed_assessment_versions`. With `assessment_version` given, only that version counts, the other versions are ignored and counted with the other-assessment attempts, and that reason cannot fire.
* **Exposure diagnostic.** Among eligible learners per arm, a learner counts as exposed with at least one `ExperimentExposed` at or before `as_of`. The treatment rate is exposed over eligible. The control rate is None, with a note, because the exposure rules give `variant_b` no surface. It is a diagnostic: it never enters the mastery metric.
* **Sample ratio.** `srm_check` (chi-square against 50/50, alpha 0.001) runs on the assigned counts and on the eligible counts.
* **Cap breaches.** A breach is a guardrail-violation row of the experiment with the reason `daily_cap_exceeded` (the notification dispatcher writes one when the daily cap suppresses a message that carries an experiment id); rows with any other reason are ignored. The arm comes from the learner's assignment, not the row's variant. Rows after `as_of` and rows of ineligible learners are ignored. Breaches are counted per learner per UTC day: each arm reports breach learner-days, distinct breached learners, and that count over eligible learners. Any treatment-arm breach invalidates the run (`cap_breach_treatment`); a control-only breach is reported but does not invalidate.
* **Delivered cap.** From the dispatch table (`notification_dispatches`, every channel and category), each eligible learner's largest number of messages in any rolling 24 hours that starts at a dispatch (`[t, t + 24 h)`), counting only dispatches inside the learner's observed window. More than `fatigue_cap` is a breach: `delivered_cap_breach_learners` counts them, and any treatment learner in breach invalidates the run (`delivered_over_cap_treatment`); control is reported only. Exactly the cap is not a breach.
* **Fatigue fields.** The observed window of an eligible learner is `[assigned_at, min(assigned_at + 14 days, as_of))`, start included and end excluded; a learner assigned at or after `as_of` contributes no days and no messages. Per arm: `messages_total` (dispatch rows inside the windows), `messages_streak_risk`, `learner_days_observed` (the windows added up, fractional days), `messages_per_learner_per_day` (messages over learner days, None with no observed days) and `max_delivered_in_24h`. Dispatch rows are messages sent; provider failures and suppressions are not counted here.
* **Opt-outs.** `opt_outs` counts eligible learners whose stored snapshot had both the push opt-out and the streak_risk opt-out off but whose current preferences (`current_prefs`, learner id to preferences) opt out of the push channel or the streak_risk category. Without `current_prefs` it is None and the result carries a note saying preferences were not supplied; a learner missing from `current_prefs` is not counted.
* **Contrast.** `contrast` holds `mastery_rate_difference` (treatment minus control; None if either rate is None), `mastery_ci95` (`{"low", "high"}`, a Wald interval from the two rates and denominators; None if either rate is None or a denominator is 0) and `messages_per_learner_per_day_difference` (None if either arm has no observed days).
* **Gaming share.** `cohort_gaming_report` runs once per ISO week (UTC), from the week of the first assignment through the week of `as_of`, with the variant as the group key and only eligible learners in the cohorts. The threshold is the default abuse-rules value. XP awards are supplied by the caller (the entry point takes them from the abuse service over the same database); awards after `as_of` are dropped. A breach in either arm sets `gaming_share_breach`.
* **Invalid reasons.** The run is valid only when `invalid_reasons` is empty. The reasons, in this order: `cap_breach_treatment`, `gaming_share_breach`, `srm_assigned`, `srm_eligible`, `missing_eligibility_records` (a learner assigned in the log has no eligibility row), `no_eligible_learners`, `mixed_assessment_versions`, `delivered_over_cap_treatment`. An invalid run still returns every arm's numbers, and nothing in the run changes an award or an assignment.
* **Output.** `ExperimentRunResult.to_dict()` is plain JSON with sorted keys at every level; two runs on the same data give identical text.
* **Reproduction.** `verify_engine_reproduction` (`src/scale_and_recovery/experiment_repro.py`) opens fresh objects twice on one database file (events, experiment, notification and evidence tables), runs the pipeline with awards from the abuse service, and compares the SHA-256 of each run's sorted-key JSON. It too is run by hand.

## 5.6 System-Performance & Reliability Metrics

This framework maps directly to the required milestones (M1–M8) to prove the system handles scale, recovery, and strict consistency.
* **Peak-Load Ingestion & Idempotency (M1, M8):** The P99 latency measured from event generation to canonical storage under a synthetic high-volume workload, including the exact computational overhead of verifying business transaction identifier and dropping duplicate events using their business deduplication identifiers.
* **State Rebuild & Recovery Time (M2, M8):** The exact time required to sequentially read the append-only event log and completely reconstruct the deterministic gamification state (XP, streaks, badges) for the synthetic population following a simulated database wipe.
* **Leaderboard Consistency & Latency (M3, M8):** The measurable update and query latency for the SQLite-backed leaderboard store at both the cohort and global levels, tracking the time delta between event ingestion and read-model availability.
* **Notification Reliability & Dead-Letter Routing (M5, M8):** The percentage of failed notification candidates successfully routed to the Dead-Letter Queue (DLQ), alongside the recovery time required to process a backlog of delayed retry events after a simulated external channel failure.
* **Abuse Detection Efficacy (M6):** The latency and success rate of the rules engine in identifying scripted point farming or impossible timing exploits, measured by the volume of canonical events successfully flagged with stored evidence rather than silently dropped.
