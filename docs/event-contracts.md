# Event Contracts

_Changelog: 2026-11-06 — added `learning.surface_viewed` v1 (third ingested source event type) and the `ExperimentAssigned` and `ExperimentExposed` domain events (M7)._

_Changelog: 2026-11-02 — the checkpoint-attempt overview now says the first attempt is read by the M4 remediation policy (final M4 consistency pass)._

_Changelog: 2026-11-02 — corrected the `learning.lesson_completed` example (UUID `subject_id`, `provenance.source` `client`, both checked by running it through ingestion) and two statements that did not match the code (no seven-day deduplication window; the expected rule version is the configured one, `v1` by default); noted that `checkpoint_completed.schema.json` is not ingested._

_Changelog: 2026-11-02 — added `learning.checkpoint_attempted` v1 (second ingested source event type)._

_Changelog: 2026-11-02 — added the `RemediationDecided` domain event (M4 remediation decision)._

_Changelog: 2026-11-02 — documented the `remediation_v1` quest and its link to an `assign` decision (M4)._

_Changelog: 2026-11-02 — documented quest expiry (sweep and on-completion paths, `expired_at = deadline_at`) (M4)._

This document defines the versioned payload contracts used inside the canonical event envelope. The envelope schema remains the common contract for routing, timestamps, subject identity, provenance, and ingestion deduplication.

## Lesson Completion (`learning.lesson_completed`), v1

This is a source learning event. It records that a learner completed a specific lesson attempt; it does not contain an XP amount, a streak outcome, or a quest or badge decision. Those outcomes are emitted as separate gamification events.

### Payload

| Field | Required | Meaning |
|---|---:|---|
| `completion_id` | Yes | The immutable identifier for one completed attempt, created by the learning service. This is the stable business action identity. |
| `lesson_id` | Yes | Stable identifier of the completed lesson. |
| `lesson_version` | No | Immutable version of the lesson definition completed. |
| `timezone` | Yes | Learner IANA timezone at completion, such as `Asia/Kolkata`. |
| `completion_kind` | No | One of `lesson`, `review`, or `remediation`. Ingestion passes it to the aggregate (M6). Absent means `lesson`. `review` and `remediation` never decay and do not count toward the repeat count `N`. |

The machine-readable payload contract is [lesson_completed.schema.json](../src/schemas/events/lesson_completed.schema.json).

### Producer and idempotency rules

The learning service creates `completion_id` once, when the completion succeeds. It must send that same value on every delivery or resend of the same action. A resend uses a new envelope `event_id`; an event UUID identifies a delivery, while `completion_id` identifies the business action.

For this event, construct the envelope `dedup_key` as `<event_id>:<rule_version>`, for example `<event_id>:v1`. Ingestion expects the configured rule version (`IngestConfig.rule_version`, `v1` by default) and rejects any other value with `dedup_key_mismatch`; a redelivery of an already-accepted `event_id` is checked against the version it was accepted under (see "Effective rule version"). There is no time-limited deduplication window: an `event_id` stays bound to its first delivery, and a repeat delivery is recorded as a duplicate (an evidence row, not silently dropped) however old it is.

The aggregate must also retain the completion/reward relationship, independently of delivery-level deduplication. A derived XP award uses the identity `xp:{completion_id}`. The applied rule version is normal reward payload data, not part of business identity. Consequently, a resend after eight days or a later rule update cannot increment the lesson count or create a second XP award.

Use the envelope `occurred_at` as the authoritative completion timestamp. Convert it using `payload.timezone` when deriving the learner's local activity date and streak state.

### Effective rule version

- The rule version that applies to an event is the trusted ingress configuration (`IngestConfig.rule_version`) in force when the event is **accepted**. It is never taken from the envelope and never derived from `occurred_at`. A late arrival whose `occurred_at` is before a version change is still judged under the version in force when it arrives.
- The applied version is persisted in two places: on the delivery's provenance row (`delivery_ids.rule_version`) and on the stored `XPAwarded` fact (`rule_version`).
- Historical facts keep their version on replay. Replay reads the stored `XPAwarded` amounts and versions, and never re-evaluates them under the current rule set.
- A redelivery of an already-accepted event (same `event_id`) is judged against the version it was accepted under. Its `dedup_key` must end in that version, even if the configuration has changed since. A new `event_id` must use the version currently configured.

### Rule sets

- Rule sets live in `src/state_machine/rulesets.json`, one entry per version. Each entry holds `xp_amount`, `quest_targets`, `badge_threshold`, `badge_id` and `sha256`. The `sha256` is the SHA-256 of the compact, key-sorted JSON of the four content fields, and it is verified when the rule sets are loaded. A rule set may also carry an optional `decay` object (`first_repeat_percent`, `step_xp`); `decay` joins the digested content only when present, so `v1`'s digest is unchanged.
- Published versions are immutable. A content change needs a new version with a new digest, never an edit to an existing one. Stored facts name a version, and that name must keep meaning the same content.
- Rule sets with a decay schedule (`v2`) live in `src/state_machine/rulesets_decay.json` and are **opt-in**: they are not in the default registry until `register_decay_rulesets()` is called, and an `IngestConfig` for `v2` is only valid after that. (They are kept apart because existing tests pin the default registry to exactly `v1`.)
- At acceptance, the digest of the rule set that decided the event is recorded on the provenance row (`delivery_ids.rule_digest`), next to `rule_version`. Rows accepted before this was recorded have `NULL`.

### XP award (`gamification.xp_awarded`), M6 additions

Two optional fields are stored on the `XPAwarded` fact (see [xp_awarded.schema.json](../src/schemas/events/xp_awarded.schema.json)). Both are absent on awards stored before M6, and such streams replay unchanged.

| Field | Meaning |
|---|---|
| `repeat_index` | `N` at decision time: prior accepted `lesson`/absent-kind completions of the same `lesson_id` by the same learner. Recorded as `0` for `review` and `remediation` completions, which have no repeat position. |
| `completion_kind` | The kind of the rewarded completion (`lesson`, `review`, `remediation`), or absent. |

The amount is decided once, in `Learner.complete_lesson`. Under a decay rule set it is `E` for `N = 0` and `max(0, E * first_repeat_percent // 100 - step_xp * (N - 1))` for `N >= 1` (v2: 100, 50, 30, 10, 0). Replay and the leaderboard read the stored `xp_amount`; neither recomputes it.

### Late-event window

- Lateness is measured from the envelope `occurred_at` to the server receive time, which comes from the trusted ingress clock (`IngestConfig.clock`). The envelope `ingested_at` is provenance only and never affects this decision.
- An event received **more than 72 hours** after its `occurred_at` is rejected as `outside_late_window`. It is recorded as rejected evidence with the receive time, and causes no state change: no completion, no reward, no streak update, no delivery binding.
- An event received **exactly 72 hours** after its `occurred_at` is accepted.
- A redelivery of an already-accepted event (same `event_id`) is a duplicate (`duplicate_event_id`) regardless of its age.
- Under this policy, a legitimate learner who was offline for more than 72 hours loses that completion's reward. This is a product decision and can be revisited.

### Example

```json
{
  "event_id": "019c26c2-7b87-7d0c-89db-8ef5b5110c4a",
  "event_type": "learning.lesson_completed",
  "schema_version": 1,
  "occurred_at": "2026-10-03T09:15:00Z",
  "ingested_at": "2026-10-03T09:15:02Z",
  "subject_id": "5b0d6c6e-2f4a-4a53-9a57-3c1f7e2d9b10",
  "producer": "learning-svc",
  "correlation_id": "cmp_492_003",
  "causation_id": null,
  "dedup_key": "019c26c2-7b87-7d0c-89db-8ef5b5110c4a:v1",
  "payload": {
    "completion_id": "cmp_492_003",
    "lesson_id": "l_492",
    "lesson_version": 1,
    "timezone": "Asia/Kolkata",
    "completion_kind": "lesson"
  },
  "provenance": {
    "source": "client"
  }
}
```

`subject_id` must be the UUID of an existing learner, and `provenance.source` must not be `server_authoritative` (rejected as `untrusted_producer`).

## Checkpoint Attempt (`learning.checkpoint_attempted`), v1

This is a source learning event. It records that a learner made one attempt at an assessment checkpoint. It does not carry a score or a reward. It is stored on the learner and awards **no XP**, makes **no streak change** and does **not progress any quest** or badge. The first attempt per assessment is read by the M4 remediation policy (see below).

### Payload

All fields are required; no other fields are allowed.

| Field | Required | Meaning |
|---|---:|---|
| `attempt_id` | Yes | Non-empty string. The immutable identifier of one checkpoint attempt, created by the producer. This is the stable business action identity. |
| `assessment_id` | Yes | Non-empty string. Stable identifier of the assessment. |
| `assessment_version` | Yes | Integer, 1 or greater. Immutable version of the assessment attempted. |
| `passed` | Yes | Boolean. Whether the learner passed. Strict: `1`, `0` and `"true"` are rejected. |
| `timezone` | Yes | Learner IANA timezone at the time of the attempt, such as `Asia/Kolkata`. Validated the same way as for lesson completion. |

The machine-readable payload contract is [checkpoint_attempted.schema.json](../src/schemas/events/checkpoint_attempted.schema.json).

`src/schemas/events/checkpoint_completed.schema.json` (a payload with `passed` and `score`) is not ingested: no producer may send that event type and no code reads the file. `passed` is carried on `learning.checkpoint_attempted` instead. The file is kept for now.

### Producer and idempotency rules

The envelope is the same canonical envelope as for lesson completion, with `event_type` `learning.checkpoint_attempted` and `schema_version` 1. The producer is `learning-svc`. The `dedup_key`, the trusted ingress clock, the 72 hour late-event window and the effective rule version behave exactly as described for `learning.lesson_completed` above; a checkpoint attempt applies no rule set, but its `dedup_key` is still `<event_id>:<rule_version>` for the configured version.

`attempt_id` is the business identity of the attempt and must be sent unchanged on every delivery or resend of the same attempt. The envelope `event_id` identifies one delivery, so a resend uses a new `event_id`.

- The same `event_id` delivered again is answered `duplicate` / `duplicate_event_id`, whatever its age.
- A new `event_id` with an `attempt_id` that was already accepted is answered `duplicate` / `duplicate_attempt_id`. No second `CheckpointAttempted` event is stored.
- Reusing an `event_id` with different content, or for a different attempt, is rejected as `conflicting_event_id_reuse`.

Ingestion records `attempt_id` in the same evidence tables as lesson completions, stored as `checkpoint_attempt:<attempt_id>` so that an attempt id can never collide with a lesson `completion_id`.

### Conflicting content for the same `attempt_id`

When a repeat of an accepted `attempt_id` arrives under a new `event_id` with **different** content (for example `passed` changed), the content is not compared. The delivery is a plain `duplicate_attempt_id`, the first accepted content stays, and only a duplicate evidence row is written. This mirrors how a repeated lesson `completion_id` is handled. It means a producer that corrects an attempt must send a new `attempt_id`; a correction under the old one is silently ignored.

### First attempt per assessment

The learner exposes the first attempt for each `assessment_id`, ordered by `(occurred_at, attempt_id)`. The order does not depend on arrival order, so a later attempt that arrives first is replaced by an earlier one that arrives after it, and equal `occurred_at` values are broken by `attempt_id`. The value is rebuilt from the stored events on replay.

The learner id (`subject_id`) must be a UUID and `provenance.source` must not be `server_authoritative`; ingestion rejects that value as `untrusted_producer`.

### Example

```json
{
  "event_id": "019c26c2-7b87-7d0c-89db-8ef5b5110c4b",
  "event_type": "learning.checkpoint_attempted",
  "schema_version": 1,
  "occurred_at": "2026-10-03T09:30:00Z",
  "ingested_at": "2026-10-03T09:30:02Z",
  "subject_id": "5b0d6c6e-2f4a-4a53-9a57-3c1f7e2d9b10",
  "producer": "learning-svc",
  "correlation_id": "att_492_001",
  "causation_id": null,
  "dedup_key": "019c26c2-7b87-7d0c-89db-8ef5b5110c4b:v1",
  "payload": {
    "attempt_id": "att_492_001",
    "assessment_id": "checkpoint_math_v1",
    "assessment_version": 1,
    "passed": false,
    "timezone": "Asia/Kolkata"
  },
  "provenance": {
    "source": "client"
  }
}
```

## Remediation decision (`RemediationDecided`), M4

A domain event emitted by the `Learner` aggregate (not an ingested source event) when the remediation policy
(`src/interventions/policy.py`) decides what to do about a checkpoint attempt. It earns no XP and touches no streak,
quest or checkpoint-attempt state.

### Fields

| Field | Type | Notes |
|---|---|---|
| `decision_id` | string | `remediation:<attempt_id>`; the identity of the decision |
| `attempt_id` | string | the checkpoint attempt decided on |
| `assessment_id` | string | taken from the attempt |
| `action` | `assign` \| `no_action` | |
| `reason` | string \| null | null when `action` is `assign` |
| `decided_at` | ISO-8601 string | the clock time of the decision |
| `deadline_at` | ISO-8601 string \| null | `decided_at` + 7 days for `assign`; null for `no_action` |
| `quest_id` | string \| null | `remediation_v1` for `assign`; null for `no_action` |
| `policy_version` | string | `remediation_policy_v1` |

### Identity and replay

- `decision_id = remediation:<attempt_id>` is the identity. Recording is idempotent on it: a second decision for the same
  attempt returns without emitting and the first stays.
- Decisions are stored facts and are never recomputed on replay. Re-running the policy for an already-decided attempt
  returns the stored decision, even if the clock has moved on.
- An explicit `no_action` is a recorded fact and is stored like an `assign`.

### Outcomes

Rules are checked in this order; the first that applies wins.

1. `not_first_attempt` (`no_action`): the attempt is not the first attempt for its `assessment_id`.
2. `passed` (`no_action`): the attempt passed.
3. `remediated_within_window` (`no_action`): an earlier `assign` decision has `decided_at` later than `now` minus 14 days.
4. `assign`: otherwise; `deadline_at` is `now` + 7 days and `quest_id` is `remediation_v1`.

### Frequency window and deadline

- The window is 14 days, measured from the `decided_at` of the learner's prior `assign` decisions (not from when the
  attempt occurred). It spans all assessments of the learner. An `assign` decided exactly 14 days earlier is outside
  the window and does not block; one decided 14 days minus 1 second earlier blocks. Prior `no_action` decisions never block.
- The quest deadline is 7 days after `decided_at`.

### Remediation quest (`remediation_v1`), M4

An `assign` decision creates a real quest for the learner.

- **Definition:** quest `remediation_v1`, definition version `remediation_quests_v1`, target 1. Only a completion with
  `completion_kind = "remediation"` counts; a completion of kind `lesson`, `review` or absent (absent counts as `lesson`)
  does not progress it.
- **Where it lives:** `src/state_machine/quest_definitions.py`, not in the rule sets. Published rule sets
  (`rulesets.json`, `rulesets_decay.json`) are immutable, so their content and digests are unchanged.
- **Assignment:** `assignment_id = remediation_quest:<attempt_id>`; `assigned_at` is the decision's `decided_at` and
  `deadline_at` is its `deadline_at` (7 days). The `assign` decision and its `QuestAssigned` event are written in the same
  save; if the quest cannot be assigned, nothing is saved. A `no_action` decision assigns no quest, and re-running for an
  already-decided attempt assigns nothing new.
- **Other quests:** quests defined by a rule set (e.g. `lesson_starter_v1`) accept any completion kind and keep progressing
  on every completion kind, as before.
- **Completion rules:** a completion that occurred before `assigned_at` does not progress the quest; one after
  `deadline_at` does not complete it. A remediation completion earns its normal XP (no decay, `repeat_index` 0) whether
  or not a remediation quest is active.

### Quest expiry, M4

Every quest (remediation and rule-set quests alike) expires through the existing `QuestExpired` event, by two paths that
record the same value:

- **Sweep:** `run_quest_expiry(app, learner_id, clock=...)` calls `Learner.expire_due_quests(as_of)`. It expires each quest
  with status `assigned` and `deadline_at < as_of` (strictly: a quest at exactly its deadline is not expired), in sorted
  `assignment_id` order, and returns the expired ids. A naive `as_of` raises `ValueError` and emits nothing. The learner is
  saved only if something expired.
- **On completion:** a completion processed after a quest's `deadline_at` expires that quest, as before.
- **`expired_at = deadline_at`** on both paths, never the completion time or the sweep time, so the fact does not depend on
  when it was noticed. Events stored before this change keep the completion time they were written with; nothing reads
  `expired_at` back (the aggregate and the quest read model take only the status).
- **Idempotent:** completed and already-expired quests are never touched, so a second sweep returns `[]`, emits nothing and
  saves nothing.
- **Window:** an expired quest does not reset the 14-day remediation window; that is measured from the `assign` decision's
  `decided_at`.
- **Scheduling:** nothing schedules the sweep yet. It is invoked explicitly, like reconciliation (`src/leaderboard/reconciliation.py`).

## Surface view (`learning.surface_viewed`), v1

This is a source learning event. It records that a learner viewed a learning surface that belongs to an experiment. It is the only way an experiment exposure is created. It awards **no XP**, makes **no streak change** and does **not progress any quest**.

### Payload

All fields are required; no other fields are allowed. There is deliberately no `variant` and no `assignment_ref`: the engine derives both from the learner's stored assignment, and a payload that carries either is rejected as `schema_invalid`.

| Field | Required | Meaning |
|---|---:|---|
| `view_id` | Yes | Non-empty string. The immutable identifier of one view, created by the producer. This is the stable business action identity. |
| `exp_id` | Yes | Non-empty string. The experiment the surface belongs to, for example `exp_007`. |
| `exposure_surface` | Yes | Non-empty string. The surface viewed. For `exp_007` the only treatment surface is `streak_risk_reminder`. |
| `timezone` | Yes | Learner IANA timezone at the time of the view. Validated the same way as for lesson completion; the exposure does not use it. |

The machine-readable payload contract is [surface_viewed.schema.json](../src/schemas/events/surface_viewed.schema.json).

### Producer and idempotency rules

The envelope is the same canonical envelope as for lesson completion, with `event_type` `learning.surface_viewed` and `schema_version` 1. The producer is `learning-svc`. The `dedup_key`, the trusted ingress clock, the 72 hour late-event window and the effective rule version behave exactly as for `learning.lesson_completed`.

`view_id` is the business identity of the view and must be sent unchanged on every resend. It is unique per learner across experiments.

- The same `event_id` delivered again is answered `duplicate` / `duplicate_event_id`.
- A new `event_id` with a `view_id` that was already accepted is answered `duplicate` / `duplicate_view_id`. No second `ExperimentExposed` event is stored.
- Reusing an `event_id` with different content, or for a different view, is rejected as `conflicting_event_id_reuse`.

Ingestion records `view_id` in the same evidence tables as other actions, stored as `surface_view:<view_id>`.

### Refusals

The aggregate refuses an exposure that its rules do not allow. The delivery is answered `rejected` with the refusal as the reason, one rejected evidence row is written, and nothing is stored. The `event_id` is not bound, so a redelivery is evaluated again.

| Reason | When |
|---|---|
| `no_assignment` | The learner has no assignment for `exp_id`. |
| `no_treatment_surface_for_variant` | The learner's variant has no treatment surface (a control learner, `variant_b`). |
| `not_treatment_surface` | `exposure_surface` is not the variant's treatment surface. |
| `exposure_before_assignment` | `occurred_at` is earlier than the assignment time. Equal is allowed. |

Other rejections are the shared ones (`schema_invalid`, `untrusted_producer`, `outside_late_window`, `dedup_key_mismatch`). `subject_id` must be the UUID of an existing learner: for a learner with no aggregate ingestion raises an error and creates nothing.

### Example

```json
{
  "event_id": "019c26c2-7b87-7d0c-89db-8ef5b5110c4c",
  "event_type": "learning.surface_viewed",
  "schema_version": 1,
  "occurred_at": "2026-10-03T09:30:00Z",
  "ingested_at": "2026-10-03T09:30:02Z",
  "subject_id": "5b0d6c6e-2f4a-4a53-9a57-3c1f7e2d9b10",
  "producer": "learning-svc",
  "correlation_id": "view_492_001",
  "causation_id": null,
  "dedup_key": "019c26c2-7b87-7d0c-89db-8ef5b5110c4c:v1",
  "payload": {
    "view_id": "view_492_001",
    "exp_id": "exp_007",
    "exposure_surface": "streak_risk_reminder",
    "timezone": "Asia/Kolkata"
  },
  "provenance": {
    "source": "client"
  }
}
```

## Experiment assignment (`ExperimentAssigned`), M7

A domain event emitted by the `Learner` aggregate (not an ingested source event) when a learner is assigned to an experiment. It earns no XP and touches no streak, quest or checkpoint state. It is not an exposure.

| Field | Type | Notes |
|---|---|---|
| `exp_id` | string | the experiment |
| `variant` | string | `variant_a` or `variant_b`, decided by `assign(str(learner id), exp_id)` |
| `hash_version` | string | `hash_v2` |
| `propensity` | number | probability of the selected variant, 0.5 |
| `assigned_at` | ISO-8601 string | timezone-aware; supplied by the caller. A naive time is refused with `ValueError` |

The event also carries `source_event_id`, `correlation_id` and `causation_id` (provenance only, null when not supplied). Recording is idempotent on `exp_id`: a second assignment returns without emitting, even if `assigned_at` differs, and the first stays. The aggregate keeps these facts in `experiment_assignments` (`exp_id` to the fields above); replay applies the stored values and never recomputes the hash.

## Experiment exposure (`ExperimentExposed`), M7

A domain event emitted by the `Learner` aggregate when an assigned learner views a treatment surface (normally from an accepted `learning.surface_viewed`). It earns no XP and touches no streak, quest or checkpoint state.

| Field | Type | Notes |
|---|---|---|
| `exp_id` | string | the experiment |
| `variant` | string | taken from the stored assignment, never from the source event |
| `exposure_surface` | string | the treatment surface viewed (`streak_risk_reminder` for `variant_a` of `exp_007`) |
| `assignment_ref` | string | `experiment_assignment:<exp_id>` |
| `view_id` | string | the identity of the view |
| `exposed_at` | ISO-8601 string | the source event's `occurred_at`; timezone-aware |

The event also carries `source_event_id`, `correlation_id` and `causation_id` (provenance). The learner keeps `experiment_exposures` (`view_id` to the fields above), `first_exposure_view_ids` (`exp_id` to the `view_id` of the first exposure) and `exposure_sources` (`view_id` to the source `event_id`).

- Recording is idempotent on `view_id`. Every distinct view is recorded.
- The first exposure per experiment is derived as the earliest by `(exposed_at, view_id)`, so it does not depend on arrival order; equal times are broken by `view_id`. It is rebuilt from the stored events on replay.
- Exposure is never created by assignment alone, and a control learner never has one.
