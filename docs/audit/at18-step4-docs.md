# AT-18 step 4 of 5: documentation

## Changes made (docs only; nothing under `src/`, `simulator/` or `tests/` touched; no state-changing git commands)
1. `docs/traceability-matrix.md`: dated changelog entry (2026-11-07) at the top; M7 row now says AT-18 passing (simulator-backed; eligibility, decision and delivery are simulator stand-ins, not calls into M4/M5) and keeps "exposure is not yet consumed by the metric"; "(test to be written)" removed from the AT-18 heading with the rest of the line unchanged; new "Clarification (M7, 2026-11-07; ...)" block placed after the 2026-11-06 M7 clarification.
2. `docs/metrics-framework.md`: one line defining messages per learner per day (14-day window, both arms).
3. `README.md`: changelog line at the top, M7 line in Milestone Status updated, new row `test_policy_comparison | M7 | passing (AT-18; simulator-backed synthetic)` in Planned Placeholder Tests.
4. `simulator/SPEC.md`: **not changed.** Its claims ("partially implemented (pilot v1...)", the "Not implemented" list, and the Limits that eligibility/decision/delivery are simulator stand-ins) are still correct.

## Proposed AT-18 rewording (NOT applied; for your approval)
Current (unchanged in the matrix):
> 18. **Policy Comparison (M7):** Given a synthetic population assigned by deterministic hash to treatment (streak reminders) or control (no reminders), when outcomes are computed, then the primary metric is the percent of all assigned eligible learners who pass their first eligible checkpoint within 14 days, with missing outcomes counted as not mastered, and fatigue metrics are reported for both groups.

Proposed:
> 18. **Policy Comparison (M7):** Given a synthetic population assigned by deterministic hash to treatment (streak reminders) or control (no reminders), when outcomes are computed, then the primary metric is the percent of all assigned eligible learners who pass their first eligible checkpoint within 14 days, with missing outcomes counted as not mastered and learners whose 14-day window has not closed at the data cutoff excluded and reported separately, and fatigue metrics (messages per learner per day, opt-outs and cap breaches) are reported for both groups.

Reason: the current wording says "all assigned eligible learners", but the implemented headline excludes immature learners; the clarification covers it, the rewording would put it in the criterion itself.

## Before / after (diffs of the three edited docs, as text)
```diff
diff --git a/README.md b/README.md
index 1c3ec45..ce93e9e 100644
--- a/README.md
+++ b/README.md
@@ -2,2 +2,4 @@
 
+> **Changelog (2026-11-07):** M7 wording: AT-18 (policy comparison) now passes on the synthetic simulator (`tests/simulator/test_policy_comparison.py`); eligibility, decision and delivery are simulator stand-ins, and exposure is still not consumed by the metric.
+
 > **Changelog (2026-11-06):** M7 wording: experiment assignment is recorded as `ExperimentAssigned` and treatment-surface exposure as `ExperimentExposed` (AT-12 and AT-13 passing on synthetic fixtures); the two M7 placeholder tests now pass; exposure is not yet consumed by the metric.
@@ -150,3 +152,3 @@ Then open <http://localhost:8000/>.
 | M6 | Abuse detection | ☐ (implemented for synthetic fixtures; flag-only; thresholds unvalidated on real data; XP decay is opt-in ruleset `v2`) |
-| M7 | Experimentation and metrics | ☐ (experiment assignment recorded as `ExperimentAssigned` and treatment-surface exposure as `ExperimentExposed` (AT-12 and AT-13 passing; synthetic fixtures; control learners never produce exposure events); exposure not yet consumed by the metric; simulator partially implemented (pilot v1); AT-18 to be written) |
+| M7 | Experimentation and metrics | ☐ (experiment assignment recorded as `ExperimentAssigned` and treatment-surface exposure as `ExperimentExposed` (AT-12 and AT-13 passing; synthetic fixtures; control learners never produce exposure events); exposure not yet consumed by the metric; simulator partially implemented (pilot v1); AT-18 passing, simulator-backed synthetic) |
 | M8 | Scale and recovery | ☑ (pinned synthetic workload, multi-learner full-state rebuild AT-15 (the wipe destroys all derived state, i.e. the leaderboard and projections, and rebuilds from the retained event log with a fresh application instance; the event log itself is not wiped; synthetic population is small: 15 learners, 612 replayed entries), leaderboard p50/p95/p99 latency bounds, DLQ ou
@@ -169 +171,2 @@ M1 means events from the learning app are validated against versioned schemas be
 | `test_exposure_is_logged_only_after_treatment_surface_view` | M7 | passing (AT-13; synthetic fixtures) |
+| `test_policy_comparison` | M7 | passing (AT-18; simulator-backed synthetic) |
diff --git a/docs/metrics-framework.md b/docs/metrics-framework.md
index 5a95439..ea6638d 100644
--- a/docs/metrics-framework.md
+++ b/docs/metrics-framework.md
@@ -69,2 +69,3 @@ Analysis is by assignment: every assigned eligible learner stays in the arm they
 Fatigue metrics include messages per learner per day, opt-outs, and cap breaches;
+messages per learner per day is the messages sent (delivered plus provider-failed) divided by eligible learners times the 14-day window, reported for both arms;
 the [daily notification cap](problem-boundaries.md#notification-channels-and-constraints) applies. Assignment, eligibility, decision, delivery,
diff --git a/docs/traceability-matrix.md b/docs/traceability-matrix.md
index 0d2e8c6..86b5d5e 100644
--- a/docs/traceability-matrix.md
+++ b/docs/traceability-matrix.md
@@ -2,2 +2,4 @@
 
+> **Changelog (2026-11-07):** AT-18 written and passing (`tests/simulator/test_policy_comparison.py`, simulator-backed on synthetic data): assignment by the deterministic hash, the headline mastery rate recomputed independently, missing outcomes counted as not mastered, immature learners excluded and reported separately, fatigue metrics for both arms (messages per learner per day added to the arm
+
 > **Changelog (2026-11-06):** M7 documented as implemented for experiment assignment and exposure on synthetic fixtures. AT-13 written and passing (`tests/experiments/test_assignment_vs_exposure.py`, `tests/experiments/test_deterministic_assignment.py`): assignment is stored as `ExperimentAssigned`, treatment-surface exposure as `ExperimentExposed` via the new ingested source event `learning.surf
@@ -41,3 +43,3 @@
 | M6 Abuse detection | AT-6 | Abuse rule engine | Sampadram | Madhava Asrith | implemented for synthetic fixtures; flag-only (opt-in decay ruleset v2, retroactive detectors, cohort guardrail); thresholds unvalidated on real data; no `confirmed` status or XP correction | W4-5 |
-| M7 Experimentation and metrics | AT-12, AT-13, AT-15 (partial), AT-18 (to be written) | Experimentation and metrics pipeline; simulator: partially implemented (pilot v1, see `simulator/SPEC.md`) | Sampadram | Madhava Asrith | experiment assignment recorded as `ExperimentAssigned` and treatment-surface exposure as `ExperimentExposed` (AT-12 and AT-13 passing; synthetic fixtures; control learners
+| M7 Experimentation and metrics | AT-12, AT-13, AT-15 (partial), AT-18 | Experimentation and metrics pipeline; simulator: partially implemented (pilot v1, see `simulator/SPEC.md`) | Sampadram | Madhava Asrith | experiment assignment recorded as `ExperimentAssigned` and treatment-surface exposure as `ExperimentExposed` (AT-12 and AT-13 passing; synthetic fixtures; control learners never produce e
 | M8 Scale and recovery | AT-2, AT-15 | Full rebuild and recovery orchestration | Madhava Asrith | Sampadram | implemented & passing (AT-15 multi-learner full-state rebuild, leaderboard p50/p95/p99 latency bounds, DLQ outage recovery drain, raw-log experiment reproduction) | W5-6 |
@@ -90,3 +92,3 @@ Built from the imports in `src/`, the config files and the tests. "Evidence" nam
 17. **Learning-Oriented Remediation Quest (M4):** Given learner L1's first attempt on checkpoint assessment A (earliest by occurred_at, then attempt_id) fails, and L1 received no remediation quest assignment (an assign decision, which assigns the quest in the same save) in the previous 14 days, when the remediation policy runs, then a stored assign decision creates a remediation_v1 quest with a d
-18. **Policy Comparison (M7) (test to be written):** Given a synthetic population assigned by deterministic hash to treatment (streak reminders) or control (no reminders), when outcomes are computed, then the primary metric is the percent of all assigned eligible learners who pass their first eligible checkpoint within 14 days, with missing outcomes counted as not mastered, and fatigue metrics ar
+18. **Policy Comparison (M7):** Given a synthetic population assigned by deterministic hash to treatment (streak reminders) or control (no reminders), when outcomes are computed, then the primary metric is the percent of all assigned eligible learners who pass their first eligible checkpoint within 14 days, with missing outcomes counted as not mastered, and fatigue metrics are reported for both g
 
@@ -94,2 +96,4 @@ Built from the imports in `src/`, the config files and the tests. "Evidence" nam
 
+> **Clarification (M7, 2026-11-07; the acceptance wording above is unchanged):** The AT-18 test is `tests/simulator/test_policy_comparison.py`. Assignment is the deterministic hash (`assign(learner_id, "exp_007")`, propensity 0.5). The headline rate counts missing outcomes as not mastered, but leaves out immature learners (those whose 14-day window has not closed at the data cutoff) and reports t
+
 > **Clarification (M8, 2026-11-02; the acceptance wording above is unchanged):** In AT-15 the "database wipe" destroys all derived state (the leaderboard read model and the projections built on it) and rebuilds it from the retained event log using a fresh application instance opened on the same events database. The event log itself is not wiped. Before the wipe, XP, streak and freezes are read fr
```
(Long lines truncated to 400 characters in this audit copy only; the files themselves are full.)

## Assumptions
- Date used: 2026-11-07 (today).
- Wording of the changelog and clarification is mine, in the existing style; "x" is used for the multiplication sign.
- The README M7 checkbox stays `☐`, as other partially implemented milestones do.

## Unsure about
- The matrix clarification is one block covering all five points you listed; it is long. Say if you want it split.
- The M7 row's AT list now reads "AT-18" with no qualifier; the status column carries "passing".
- Full-suite run was not repeated (docs only). The step-2 leftover failure (`test_pilot_report.py` notes count 3 vs 4) is still open and unchanged.
