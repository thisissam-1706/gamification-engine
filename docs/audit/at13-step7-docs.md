# AT-13 step 7 of 8: documentation

Documentation only. `src/` and `tests/` were not touched (checked with `git diff --name-only -- src tests`, which lists only the earlier steps' files; none is new in this step). No state-changing git command was run; the diffs below come from read-only `git diff`.

## Changes made
| File | Change |
|---|---|
| `docs/traceability-matrix.md` | Dated changelog entry at the top (2026-11-06); M7 row status and simulator wording updated; new "Clarification (M7, 2026-11-06; the acceptance wording above is unchanged)" block above the M8 one. AT-13 text not edited. "AT-18 (to be written)" kept. |
| `docs/event-contracts.md` | Changelog line; new sections `learning.surface_viewed` v1, `ExperimentAssigned`, `ExperimentExposed`. |
| `docs/metrics-framework.md` | One paragraph in "Experiment protocol (M7)" on assignment vs exposure. |
| `README.md` | Changelog line at the top; M7 line in Milestone Status; the two M7 rows in "Planned Placeholder Tests" now "passing (AT-13; synthetic fixtures)". |

Simulator wording: `simulator/SPEC.md:3` says "partially implemented (pilot v1: population, common random numbers, pipeline streams, analysis)". The M7 row previously said "simulator: stub, not implemented, planned for milestone M7" (and README "simulator is a stub"). Both now say "partially implemented (pilot v1)"; the matrix points to `simulator/SPEC.md`.

## Proposed rewording of AT-13 (NOT applied; for your approval)
Current (unchanged in the matrix):
> 13. **Assignment vs. Exposure (M7):** Given L1 is assigned to variant_a for exp_007, when L1 has not yet opened the app, then only Experiment Assignment exists. Experiment Exposure is emitted only once L1 actually views the treatment surface.

Proposed:
> 13. **Assignment vs. Exposure (M7):** Given learner L1 is assigned to `variant_a` for `exp_007`, when no `learning.surface_viewed` event has been accepted for L1, then L1's stored events contain an `ExperimentAssigned` event and no `ExperimentExposed` event, including after other activity such as a lesson completion. When L1 then views the treatment surface (`streak_risk_reminder`) at or after the assignment time, exactly one `ExperimentExposed` event is stored, with variant `variant_a` and the assignment reference `experiment_assignment:exp_007`, and the same state is found after reopening the event store. A learner assigned to `variant_b` who sends the same view is rejected and never has an `ExperimentExposed` event.

Why: the current text says "opened the app" and "emitted"; the code observes a surface-view event, stores the exposure, and rejects control views. The clarification block already explains this, so applying the rewording is optional.

## Schema mismatch report (schemas NOT edited)
Compared the stored event fields with the checked-in payload schemas:
- `src/schemas/events/experiment_exposed.schema.json` requires `exp_id`, `variant`, `exposure_surface`, `assignment_ref` and has `additionalProperties: false`. The stored `ExperimentExposed` event also has **`view_id`** and **`exposed_at`** (plus provenance `source_event_id`, `correlation_id`, `causation_id`), so a stored event would not validate against this schema.
- `src/schemas/events/experiment_assigned.schema.json` requires `exp_id`, `variant`, `hash_version`, `propensity`, with `additionalProperties: false`. The stored `ExperimentAssigned` event also has **`assigned_at`** (plus provenance fields).
- No code or test reads either file (only the packaging glob ships them), so nothing fails today. `docs/event-contracts.md` documents the stored fields and does not link these two schema files for that reason.
- Suggested follow-up (not done): add `view_id`/`exposed_at` and `assigned_at` to the two schemas, or decide that these files describe an external contract and say so.

## Diffs (pasted from `git diff`)

### docs/traceability-matrix.md
```diff
diff --git a/docs/traceability-matrix.md b/docs/traceability-matrix.md
index d88d436..0d2e8c6 100644
--- a/docs/traceability-matrix.md
+++ b/docs/traceability-matrix.md
@@ -1,5 +1,7 @@
 # Traceability & Acceptance Tests
 
+> **Changelog (2026-11-06):** M7 documented as implemented for experiment assignment and exposure on synthetic fixtures. AT-13 written and passing (`tests/experiments/test_assignment_vs_exposure.py`, `tests/experiments/test_deterministic_assignment.py`): assignment is stored as `ExperimentAssigned`, treatment-surface exposure as `ExperimentExposed` via the new ingested source event `learning.surface_viewed`. The M7 row status and its simulator wording (now consistent with `simulator/SPEC.md`: partially implemented, pilot v1) were updated, and an M7 clarification was added; the AT-13 acceptance wording is unchanged. AT-18 is still to be written. No thresholds changed.
+
 > **Changelog (2026-11-02):** AT-15 clarified (acceptance wording unchanged): the wipe now destroys all derived state and rebuilds with a fresh application instance; the synthetic population size is stated. No thresholds changed.
 
 > **Changelog (2026-11-02):** M4 documented as implemented for synthetic fixtures. AT-17 written and passing (`tests/interventions/test_learning_remediation.py`) with its wording made specific (earliest-first-attempt rule, 14-day window, 7-day deadline, `remediated_within_window`, expiry sweep with `expired_at` = deadline; policy and sweep invoked, not scheduled). The M4 row status, the M1 status (now also ingests `learning.checkpoint_attempted`) and the "Reused, configured, new" table (remediation config, policy/runner, quest definitions) were updated to match. No other acceptance criteria, numbers or thresholds changed.
@@ -37,7 +39,7 @@
 | M4 Intervention/quest policy (learning-oriented remediation) | AT-17 | Intervention and quest policy | Madhava Asrith | Sampadram | implemented for synthetic fixtures: checkpoint_attempted ingestion, rule-based remediation policy with stored decisions, remediation_v1 quest (remediation completions only), deadline expiry sweep; policy and sweep invoked, not scheduled; no notification hook | W3-4 |
 | M5 Multi-channel notifications | AT-8, AT-9, AT-10, AT-11, AT-14 | Multi-channel notification engine | Madhava Asrith | Sampadram | in-app and simulated-push slice implemented; email adapter not implemented | W4 |
 | M6 Abuse detection | AT-6 | Abuse rule engine | Sampadram | Madhava Asrith | implemented for synthetic fixtures; flag-only (opt-in decay ruleset v2, retroactive detectors, cohort guardrail); thresholds unvalidated on real data; no `confirmed` status or XP correction | W4-5 |
-| M7 Experimentation and metrics | AT-12, AT-13, AT-15 (partial), AT-18 (to be written) | Experimentation and metrics pipeline; simulator: stub, not implemented, planned for milestone M7 | Sampadram | Madhava Asrith | assignment and metric implemented; simulator is a stub; exposure logging not implemented | protocol W2 / run W5 |
+| M7 Experimentation and metrics | AT-12, AT-13, AT-15 (partial), AT-18 (to be written) | Experimentation and metrics pipeline; simulator: partially implemented (pilot v1, see `simulator/SPEC.md`) | Sampadram | Madhava Asrith | experiment assignment recorded as `ExperimentAssigned` and treatment-surface exposure as `ExperimentExposed` (AT-12 and AT-13 passing; synthetic fixtures; control learners never produce exposure events); exposure is not yet consumed by the metric; simulator partially implemented (pilot v1); AT-18 to be written | protocol W2 / run W5 |
 | M8 Scale and recovery | AT-2, AT-15 | Full rebuild and recovery orchestration | Madhava Asrith | Sampadram | implemented & passing (AT-15 multi-learner full-state rebuild, leaderboard p50/p95/p99 latency bounds, DLQ outage recovery drain, raw-log experiment reproduction) | W5-6 |
 
 ## Reused, configured, new
@@ -88,6 +90,8 @@ Built from the imports in `src/`, the config files and the tests. "Evidence" nam
 17. **Learning-Oriented Remediation Quest (M4):** Given learner L1's first attempt on checkpoint assessment A (earliest by occurred_at, then attempt_id) fails, and L1 received no remediation quest assignment (an assign decision, which assigns the quest in the same save) in the previous 14 days, when the remediation policy runs, then a stored assign decision creates a remediation_v1 quest with a deadline 7 days after the decision. If L1 received an assignment less than 14 days earlier, the policy stores an explicit no_action decision with reason remediated_within_window. An unfinished quest is expired by the expiry sweep once its deadline has passed, with expired_at equal to the deadline. The policy and the sweep are invoked, not yet scheduled.
 18. **Policy Comparison (M7) (test to be written):** Given a synthetic population assigned by deterministic hash to treatment (streak reminders) or control (no reminders), when outcomes are computed, then the primary metric is the percent of all assigned eligible learners who pass their first eligible checkpoint within 14 days, with missing outcomes counted as not mastered, and fatigue metrics are reported for both groups.
 
+> **Clarification (M7, 2026-11-06; the acceptance wording above is unchanged):** In AT-13 "has not yet opened the app" means no `learning.surface_viewed` event has been accepted for L1. Assignment is stored on the learner as `ExperimentAssigned` (variant from the hash, `hash_v2`, propensity, assigned time); the first assignment per experiment stays. Exposure is the ingested source event `learning.surface_viewed` from `learning-svc`, stored as `ExperimentExposed`. The variant and the assignment reference (`experiment_assignment:<exp_id>`) are derived by the engine from the stored assignment; a payload that carries `variant` or `assignment_ref` is rejected as `schema_invalid`. For `exp_007` there is exactly one treatment surface, `streak_risk_reminder`, and it belongs to `variant_a`. Every distinct `view_id` is recorded, and the learner's first exposure is derived as the earliest by `(exposed_at, view_id)`, independent of arrival order. A control (`variant_b`) learner's view is rejected (`no_treatment_surface_for_variant`), so control learners never produce an exposure event. Other refusals are recorded as rejected evidence and store nothing: `no_assignment`, `not_treatment_surface` and `exposure_before_assignment` (the view's `occurred_at` is earlier than the assignment time). The 72 hour late window applies to views like any other ingested event. A view for a learner with no aggregate at all raises an error rather than being recorded as rejected. The test uses synthetic fixtures, and exposure is not yet consumed by the metric.
+
 > **Clarification (M8, 2026-11-02; the acceptance wording above is unchanged):** In AT-15 the "database wipe" destroys all derived state (the leaderboard read model and the projections built on it) and rebuilds it from the retained event log using a fresh application instance opened on the same events database. The event log itself is not wiped. Before the wipe, XP, streak and freezes are read from the live aggregates; after the rebuild they are derived by replay, and leaderboard ranks must match exactly. The synthetic population is small: AT-15 uses 15 learners in 2 cohorts, 12 events per learner (5% duplicates, 5% late), seed 42, which is 612 notification-log entries replayed (`total_events`) and 612 stored domain events across all aggregates (`domain_event_count`); the wipe tests use 8 learners. This demonstrates correctness of the rebuild at that size, not scale.
 
 > **Clarification (M6, 2026-10-26; the acceptance wording above is unchanged):** AT-1 calls the first attempt "N = 0" and AT-6 calls the third completion "N = 2", so N is the number of **prior** accepted completions of the same `lesson_id` by the same learner whose `completion_kind` is `lesson` or absent. `review` and `remediation` completions neither decay nor count toward N. With ruleset `v2` (E = 100, 50% first repeat, step 20) XP is 100, 50, 30, 10, 0 for N = 0, 1, 2, 3, 4+. "Decayed XP" in the 15% cohort threshold is read as XP awarded on repeat completions (`repeat_index` >= 1). The decay rule set is opt-in and the "policy is flagged" outcome is a report from `cohort_gaming_report`, not an automatic action.
```

### README.md
```diff
diff --git a/README.md b/README.md
index ecf093d..1c3ec45 100644
--- a/README.md
+++ b/README.md
@@ -1,5 +1,7 @@
 # Gamification, Social Cohort, Notification and Retention Experimentation Engine
 
+> **Changelog (2026-11-06):** M7 wording: experiment assignment is recorded as `ExperimentAssigned` and treatment-surface exposure as `ExperimentExposed` (AT-12 and AT-13 passing on synthetic fixtures); the two M7 placeholder tests now pass; exposure is not yet consumed by the metric.
+
 > **Changelog (2026-11-02):** M8 wording: the AT-15 wipe destroys all derived state and rebuilds from the retained event log with a fresh application instance; the synthetic population is small (15 learners).
 
 > **Changelog (2026-11-02):** Added ADR-0007 (rule-based remediation policy) to the ADR list; the M1 description now names both ingested event types.
@@ -146,7 +148,7 @@ Then open <http://localhost:8000/>.
 | M4 | Intervention/quest policy | ☐ (implemented for synthetic fixtures: checkpoint_attempted ingestion, rule-based remediation policy with stored decisions, remediation_v1 quest (remediation completions only), deadline expiry sweep; policy and sweep invoked, not scheduled; no notification hook) |
 | M5 | Multi-channel notifications | ☐ (in-app and simulated-push slice implemented; email adapter not implemented) |
 | M6 | Abuse detection | ☐ (implemented for synthetic fixtures; flag-only; thresholds unvalidated on real data; XP decay is opt-in ruleset `v2`) |
-| M7 | Experimentation and metrics | ☐ (assignment and metric implemented; simulator is a stub; exposure logging not implemented) |
+| M7 | Experimentation and metrics | ☐ (experiment assignment recorded as `ExperimentAssigned` and treatment-surface exposure as `ExperimentExposed` (AT-12 and AT-13 passing; synthetic fixtures; control learners never produce exposure events); exposure not yet consumed by the metric; simulator partially implemented (pilot v1); AT-18 to be written) |
 | M8 | Scale and recovery | ☑ (pinned synthetic workload, multi-learner full-state rebuild AT-15 (the wipe destroys all derived state, i.e. the leaderboard and projections, and rebuilds from the retained event log with a fresh application instance; the event log itself is not wiped; synthetic population is small: 15 learners, 612 replayed entries), leaderboard p50/p95/p99 latency bounds, DLQ outage recovery drain, raw-log experiment reproduction) |
 
 M1 means events from the learning app are validated against versioned schemas before acceptance (currently `lesson_completed` and `checkpoint_attempted` are ingested), while M2 means XP, streaks, freezes, one quest, and one badge are computed deterministically and can be rebuilt exactly from the event history. M3 means synthetic learners are deterministically grouped into bounded cohorts (500–5,000 members; 30-day inactivity pruning is specified but not yet scheduled or wired in) and ranked at cohort and global levels via SQLite projections with exact tie-breaking, measurable latency, and differential reconciliation (AT-7).
@@ -163,5 +165,5 @@ M1 means events from the learning app are validated against versioned schemas be
 | `test_retry_dlq` | M5 | planned placeholder |
 | `test_leaderboard_reconciliation` | M3 | passing (AT-7) |
 | `test_full_state_rebuild` | M2, M7, M8 | passing (AT-15; multi-learner full-state disaster recovery) |
-| `test_assignment_vs_exposure` | M7 | planned placeholder |
-| `test_exposure_is_logged_only_after_treatment_surface_view` | M7 | planned placeholder |
+| `test_assignment_vs_exposure` | M7 | passing (AT-13; synthetic fixtures) |
+| `test_exposure_is_logged_only_after_treatment_surface_view` | M7 | passing (AT-13; synthetic fixtures) |
```

### docs/metrics-framework.md
```diff
diff --git a/docs/metrics-framework.md b/docs/metrics-framework.md
index a81ae44..5a95439 100644
--- a/docs/metrics-framework.md
+++ b/docs/metrics-framework.md
@@ -64,6 +64,8 @@ matched to its `CheckpointCompleted` event by `attempt_id` and
 “not mastered”, and the missing-outcome rate is reported separately. The same
 assessment is used for everyone, or results are reported by difficulty level.
 
+Analysis is by assignment: every assigned eligible learner stays in the arm they were assigned to, whether or not they ever opened the app or viewed the treatment surface, so the two groups remain comparable and the sample-ratio check stays meaningful. Exposure (`ExperimentExposed`, recorded only when a `variant_a` learner views the treatment surface) is a secondary signal, for example to report how many treated learners actually saw the reminder. Control learners never produce an exposure event, so exposure is not compared across arms. The metric does not consume exposure yet.
+
 Fatigue metrics include messages per learner per day, opt-outs, and cap breaches;
 the [daily notification cap](problem-boundaries.md#notification-channels-and-constraints) applies. Assignment, eligibility, decision, delivery,
 and exposure are logged as five separate events. Both groups use the same consent
```

### docs/event-contracts.md
```diff
diff --git a/docs/event-contracts.md b/docs/event-contracts.md
index e48ea9e..c9bd2c7 100644
--- a/docs/event-contracts.md
+++ b/docs/event-contracts.md
@@ -1,5 +1,7 @@
 # Event Contracts
 
+_Changelog: 2026-11-06 — added `learning.surface_viewed` v1 (third ingested source event type) and the `ExperimentAssigned` and `ExperimentExposed` domain events (M7)._
+
 _Changelog: 2026-11-02 — the checkpoint-attempt overview now says the first attempt is read by the M4 remediation policy (final M4 consistency pass)._
 
 _Changelog: 2026-11-02 — corrected the `learning.lesson_completed` example (UUID `subject_id`, `provenance.source` `client`, both checked by running it through ingestion) and two statements that did not match the code (no seven-day deduplication window; the expected rule version is the configured one, `v1` by default); noted that `checkpoint_completed.schema.json` is not ingested._
@@ -252,3 +254,104 @@ record the same value:
 - **Window:** an expired quest does not reset the 14-day remediation window; that is measured from the `assign` decision's
   `decided_at`.
 - **Scheduling:** nothing schedules the sweep yet. It is invoked explicitly, like reconciliation (`src/leaderboard/reconciliation.py`).
+
+## Surface view (`learning.surface_viewed`), v1
+
+This is a source learning event. It records that a learner viewed a learning surface that belongs to an experiment. It is the only way an experiment exposure is created. It awards **no XP**, makes **no streak change** and does **not progress any quest**.
+
+### Payload
+
+All fields are required; no other fields are allowed. There is deliberately no `variant` and no `assignment_ref`: the engine derives both from the learner's stored assignment, and a payload that carries either is rejected as `schema_invalid`.
+
+| Field | Required | Meaning |
+|---|---:|---|
+| `view_id` | Yes | Non-empty string. The immutable identifier of one view, created by the producer. This is the stable business action identity. |
+| `exp_id` | Yes | Non-empty string. The experiment the surface belongs to, for example `exp_007`. |
+| `exposure_surface` | Yes | Non-empty string. The surface viewed. For `exp_007` the only treatment surface is `streak_risk_reminder`. |
+| `timezone` | Yes | Learner IANA timezone at the time of the view. Validated the same way as for lesson completion; the exposure does not use it. |
+
+The machine-readable payload contract is [surface_viewed.schema.json](../src/schemas/events/surface_viewed.schema.json).
+
+### Producer and idempotency rules
+
+The envelope is the same canonical envelope as for lesson completion, with `event_type` `learning.surface_viewed` and `schema_version` 1. The producer is `learning-svc`. The `dedup_key`, the trusted ingress clock, the 72 hour late-event window and the effective rule version behave exactly as for `learning.lesson_completed`.
+
+`view_id` is the business identity of the view and must be sent unchanged on every resend. It is unique per learner across experiments.
+
+- The same `event_id` delivered again is answered `duplicate` / `duplicate_event_id`.
+- A new `event_id` with a `view_id` that was already accepted is answered `duplicate` / `duplicate_view_id`. No second `ExperimentExposed` event is stored.
+- Reusing an `event_id` with different content, or for a different view, is rejected as `conflicting_event_id_reuse`.
+
+Ingestion records `view_id` in the same evidence tables as other actions, stored as `surface_view:<view_id>`.
+
+### Refusals
+
+The aggregate refuses an exposure that its rules do not allow. The delivery is answered `rejected` with the refusal as the reason, one rejected evidence row is written, and nothing is stored. The `event_id` is not bound, so a redelivery is evaluated again.
+
+| Reason | When |
+|---|---|
+| `no_assignment` | The learner has no assignment for `exp_id`. |
+| `no_treatment_surface_for_variant` | The learner's variant has no treatment surface (a control learner, `variant_b`). |
+| `not_treatment_surface` | `exposure_surface` is not the variant's treatment surface. |
+| `exposure_before_assignment` | `occurred_at` is earlier than the assignment time. Equal is allowed. |
+
+Other rejections are the shared ones (`schema_invalid`, `untrusted_producer`, `outside_late_window`, `dedup_key_mismatch`). `subject_id` must be the UUID of an existing learner: for a learner with no aggregate ingestion raises an error and creates nothing.
+
+### Example
+
+```json
+{
+  "event_id": "019c26c2-7b87-7d0c-89db-8ef5b5110c4c",
+  "event_type": "learning.surface_viewed",
+  "schema_version": 1,
+  "occurred_at": "2026-10-03T09:30:00Z",
+  "ingested_at": "2026-10-03T09:30:02Z",
+  "subject_id": "5b0d6c6e-2f4a-4a53-9a57-3c1f7e2d9b10",
+  "producer": "learning-svc",
+  "correlation_id": "view_492_001",
+  "causation_id": null,
+  "dedup_key": "019c26c2-7b87-7d0c-89db-8ef5b5110c4c:v1",
+  "payload": {
+    "view_id": "view_492_001",
+    "exp_id": "exp_007",
+    "exposure_surface": "streak_risk_reminder",
+    "timezone": "Asia/Kolkata"
+  },
+  "provenance": {
+    "source": "client"
+  }
+}
+```
+
+## Experiment assignment (`ExperimentAssigned`), M7
+
+A domain event emitted by the `Learner` aggregate (not an ingested source event) when a learner is assigned to an experiment. It earns no XP and touches no streak, quest or checkpoint state. It is not an exposure.
+
+| Field | Type | Notes |
+|---|---|---|
+| `exp_id` | string | the experiment |
+| `variant` | string | `variant_a` or `variant_b`, decided by `assign(str(learner id), exp_id)` |
+| `hash_version` | string | `hash_v2` |
+| `propensity` | number | probability of the selected variant, 0.5 |
+| `assigned_at` | ISO-8601 string | timezone-aware; supplied by the caller. A naive time is refused with `ValueError` |
+
+The event also carries `source_event_id`, `correlation_id` and `causation_id` (provenance only, null when not supplied). Recording is idempotent on `exp_id`: a second assignment returns without emitting, even if `assigned_at` differs, and the first stays. The aggregate keeps these facts in `experiment_assignments` (`exp_id` to the fields above); replay applies the stored values and never recomputes the hash.
+
+## Experiment exposure (`ExperimentExposed`), M7
+
+A domain event emitted by the `Learner` aggregate when an assigned learner views a treatment surface (normally from an accepted `learning.surface_viewed`). It earns no XP and touches no streak, quest or checkpoint state.
+
+| Field | Type | Notes |
+|---|---|---|
+| `exp_id` | string | the experiment |
+| `variant` | string | taken from the stored assignment, never from the source event |
+| `exposure_surface` | string | the treatment surface viewed (`streak_risk_reminder` for `variant_a` of `exp_007`) |
+| `assignment_ref` | string | `experiment_assignment:<exp_id>` |
+| `view_id` | string | the identity of the view |
+| `exposed_at` | ISO-8601 string | the source event's `occurred_at`; timezone-aware |
+
+The event also carries `source_event_id`, `correlation_id` and `causation_id` (provenance). The learner keeps `experiment_exposures` (`view_id` to the fields above), `first_exposure_view_ids` (`exp_id` to the `view_id` of the first exposure) and `exposure_sources` (`view_id` to the source `event_id`).
+
+- Recording is idempotent on `view_id`. Every distinct view is recorded.
+- The first exposure per experiment is derived as the earliest by `(exposed_at, view_id)`, so it does not depend on arrival order; equal times are broken by `view_id`. It is rebuilt from the stored events on replay.
+- Exposure is never created by assignment alone, and a control learner never has one.
```

## Commands and real output
```
python -m pytest -q
812 passed, 1 deselected in 120.13s (0:02:00)
git diff --name-only -- src tests
src/experiments/assignment.py  src/ingestion/service.py  src/ingestion/validation.py  src/state_machine/learner.py
tests/experiments/test_assignment_vs_exposure.py  tests/experiments/test_deterministic_assignment.py  tests/packaging/test_wheel_contents.py
```
The suite count is unchanged from step 6 (812 passed, 0 skipped, 1 deselected); `tests/docs/` holds only `__init__.py`, so no test reads these docs. The `src`/`tests` file list is exactly the files modified in steps 1-6.

## Assumptions
- Date used for the changelog and clarification: 2026-11-06 (today's date in this session).
- "Has not yet opened the app" is read as "no `learning.surface_viewed` event accepted".
- The M7 clarification states that a view for a learner with no aggregate raises an error instead of being recorded as rejected; this is the current behaviour (steps 4 and 5) and is still awaiting your decision.
- `view_id` uniqueness across experiments is documented as it is implemented.

## Unsure about
- The M1 rows (matrix line for M1 and README M1 line/description) still say only `lesson_completed` and `checkpoint_attempted` are ingested. `learning.surface_viewed` is now a third ingested type, so they are stale. You did not list them, so I left them; say if you want them updated.
- `docs/metrics-framework.md` has no new changelog line (you asked for one paragraph); other docs in this repo do carry changelog lines.
- The matrix status says "AT-12 and AT-13 passing"; AT-12 was already passing, but the row previously did not say so.
- The M7 row still lists "AT-15 (partial)" as before.
