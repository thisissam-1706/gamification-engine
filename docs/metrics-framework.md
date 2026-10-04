# Metrics Framework

This framework dictates how the experimentation engine evaluates cohort interventions. It establishes distinct boundaries between actual learning progress, superficial engagement, and platform abuse.

## 5.1 Primary Learning Outcome

* **First-Attempt Mastery Accuracy:** The core objective is educational progression, not click volume. This metric calculates the average accuracy score across all Checkpoint Attempt events exclusively where the attempt sequence is exactly 1.

## 5.2 Long-Term Retention & Spaced Repetition

Instead of measuring simple application logins, retention is evaluated through the completion and accuracy of Spaced Repetition Review Lessons.
* **Milestone Reviews:** Scheduled systematically at Day 7, Day 15, and Day 30 post-cohort assignment.
* **Intermittent Reviews:** Triggered dynamically after a defined volume of lessons.
  * Measurement: Tracking the accuracy on these specific review channels serves a dual purpose: measuring continued platform engagement (retention) while simultaneously gauging the decay of the learner’s mastery over time.
* **Remediation Conversion Rate:** The percentage of lapsed users who successfully complete a remediation quest and generate a valid learning event the following day.

## 5.3 Guardrails (Experiment Invalidation Criteria)

Experiments that improve engagement at the cost of learner well-being or system integrity are automatically marked as failed.
* **The Notification Fatigue & Escalation Limit:** Enforces a hard cap of 5 intervention push notifications per 24 hours per learner, strictly bounded by localized quiet hours. To support habit formation, “streak at risk” notification intervals decrease exponentially (firing more rapidly) leading up to localized midnight, provided the 5-message daily cap is not breached and localized quiet hours are strictly respected (quiet hours take absolute precedence, truncating any escalating schedule upon entry).
* **The Gaming Threshold:** If > 15% of a cohort’s total weekly XP generation originates from decayed (repeated) lessons, the specific intervention policy is flagged for reward abuse and disqualified.
* Quiet hours are strictly defined as 10:00 PM to 8:00 AM in the learner’s local timezone. During this window, all push notification deliveries are blocked regardless of priority or experiment policy.

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
  `A(subject_id, exp_id) = H(subject_id || exp_id) mod 100`
  where H is a versioned hash algorithm (e.g., MurmurHash3 / Hash Algorithm v2).
* **Primary Metric (Mprimary):** The core educational or behavioral outcome measure used to evaluate policy efficacy.
* **Guardrails (G):** A set of systemic constraints (e.g., daily notification caps, fatigue thresholds, decayed-XP caps) that, if violated by any treatment variant, automatically invalidate the experiment and halt policy execution.
* **Contamination & SRM Checks (C):** Automated statistical controls, including a chi-square (χ2) goodness-of-fit test on assignment distribution, to catch Sample-Ratio Mismatch (SRM).

**Implementation Example (exp_007)**
* **Experiment Identifier (exp_id):** "exp_007" (Streak-Risk Reminder Policy vs. Control).
* **Variants (V):**
  * variant_a (Treatment): Fires an automated, exponential-interval push reminder during the 4 hours preceding localized midnight when a learner’s streak is at risk.
  * variant_b (Control): Suppresses streak-risk interventions; observes baseline learner behavior.
* **Assignment & Exclusions:** 50/50 split via `H(subject_id || "exp_007") mod 100`. Excludes learners with platform tenure < 3 days or those who opted out of the push × streak_risk consent pair.
* **Primary Metric (Mprimary):** First-Attempt Mastery Accuracy over a 14-day window (with Remediation Conversion Rate as the short-horizon operational signal).
* **Guardrails (G):** The experiment is automatically marked as invalidated if variant_a causes push notifications to exceed 5 messages per 24 hours (§5.3) or drives cohort decayed-XP share above 15% (§5.3).
* **SRM Safeguard (C):** A daily pipeline job runs a χ2 test comparing actual assignment ratios against the expected 50/50 split (p < 0.001 triggers an SRM alert).

## 5.5 Bandit Policy Allocation

We will use a contextual multi-armed bandit for notification policy selection, rather than fixed A/B allocation, but with propensity logging as a hard requirement from day one. Our literature review (paper 4) surfaced the central risk directly: a bandit can converge on an arm before there’s statistically significant evidence it’s actually better, and it optimizes an easy-to-measure proxy (email/push opens) rather than the outcome we actually care about (learning).
We mitigate this by:
1. Logging the propensity score: the probability the policy assigned to the arm it picked, on every Experiment Assignment event, which lets us later re-weight results with inverse-propensity scoring instead of trusting raw bandit outcomes at face value; and
2. Capping exploitation with a minimum exploration floor (e.g., ϵ-greedy with ϵ ≥ 0.1) for the first two weeks of any experiment, so no arm is starved of data before we have enough exposures to trust the comparison.
We are not using a bandit for core reward-rule selection (XP amounts, decay rates), those stay fixed and rule-versioned.

## Experiment protocol (M7)

The primary metric is the percentage of all assigned eligible learners who pass their
first eligible checkpoint within 14 days of assignment. A `CheckpointAttempt` is
matched to its `CheckpointCompleted` event by `attempt_id` and
`assessment_id`/version. Learners who never complete the assessment count as
“not mastered”, and the missing-outcome rate is reported separately. The same
assessment is used for everyone, or results are reported by difficulty level.

Fatigue metrics include messages per learner per day, opt-outs, and cap breaches;
the cap is 5 messages per 24 hours. Assignment, eligibility, decision, delivery,
and exposure are logged as five separate events. Both groups use the same consent
and fatigue rules. Results come from synthetic data and do not demonstrate
real-world causality.

## 5.6 System-Performance & Reliability Metrics

This framework maps directly to the required milestones (M1–M8) to prove the system handles scale, recovery, and strict consistency.
* **Peak-Load Ingestion & Idempotency (M1, M8):** The P99 latency measured from event generation to canonical storage under a synthetic high-volume workload, including the exact computational overhead of verifying business transaction identifier and dropping duplicate events using their business deduplication identifiers.
* **State Rebuild & Recovery Time (M2, M8):** The exact time required to sequentially read the append-only event log and completely reconstruct the deterministic gamification state (XP, streaks, badges) for the synthetic population following a simulated database wipe.
* **Leaderboard Consistency & Latency (M3, M8):** The measurable update and query latency for Redis sorted sets at both the cohort and global levels, tracking the time delta between event ingestion and read-model availability.
* **Notification Reliability & Dead-Letter Routing (M5, M8):** The percentage of failed notification candidates successfully routed to the Dead-Letter Queue (DLQ), alongside the recovery time required to process a backlog of delayed retry events after a simulated external channel failure.
* **Abuse Detection Efficacy (M6):** The latency and success rate of the rules engine in identifying scripted point farming or impossible timing exploits, measured by the volume of canonical events successfully flagged with stored evidence rather than silently dropped.
