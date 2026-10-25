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
* During the [defined quiet-hours window](problem-boundaries.md#notification-channels-and-constraints), all push notification deliveries are blocked regardless of priority or experiment policy.

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

## 5.5 Fixed Policy Allocation

Notification policy experiments currently use a deterministic fixed 50/50 assignment, providing a stable baseline for comparing treatment and control outcomes. Propensity logging remains a future-proofing requirement: assignment events should retain the probability used for the selected arm so that a later move to adaptive allocation can support inverse-propensity analysis. We are not using a bandit for core reward-rule selection (XP amounts, decay rates); those stay fixed and rule-versioned.

## Experiment protocol (M7)

The primary metric is the percentage of all assigned eligible learners who pass their
first eligible checkpoint within 14 days of assignment. A `CheckpointAttempt` is
matched to its `CheckpointCompleted` event by `attempt_id` and
`assessment_id`/version. Learners who never complete the assessment count as
“not mastered”, and the missing-outcome rate is reported separately. The same
assessment is used for everyone, or results are reported by difficulty level.

Fatigue metrics include messages per learner per day, opt-outs, and cap breaches;
the [daily notification cap](problem-boundaries.md#notification-channels-and-constraints) applies. Assignment, eligibility, decision, delivery,
and exposure are logged as five separate events. Both groups use the same consent
and fatigue rules. Results come from synthetic data and do not demonstrate
real-world causality.

## 5.6 System-Performance & Reliability Metrics

This framework maps directly to the required milestones (M1–M8) to prove the system handles scale, recovery, and strict consistency.
* **Peak-Load Ingestion & Idempotency (M1, M8):** The P99 latency measured from event generation to canonical storage under a synthetic high-volume workload, including the exact computational overhead of verifying business transaction identifier and dropping duplicate events using their business deduplication identifiers.
* **State Rebuild & Recovery Time (M2, M8):** The exact time required to sequentially read the append-only event log and completely reconstruct the deterministic gamification state (XP, streaks, badges) for the synthetic population following a simulated database wipe.
* **Leaderboard Consistency & Latency (M3, M8):** The measurable update and query latency for the SQLite-backed leaderboard store at both the cohort and global levels, tracking the time delta between event ingestion and read-model availability.
* **Notification Reliability & Dead-Letter Routing (M5, M8):** The percentage of failed notification candidates successfully routed to the Dead-Letter Queue (DLQ), alongside the recovery time required to process a backlog of delayed retry events after a simulated external channel failure.
* **Abuse Detection Efficacy (M6):** The latency and success rate of the rules engine in identifying scripted point farming or impossible timing exploits, measured by the volume of canonical events successfully flagged with stored evidence rather than silently dropped.
