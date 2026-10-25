# Event Contracts

This document defines the versioned payload contracts used inside the canonical event envelope. The envelope schema remains the common contract for routing, timestamps, subject identity, provenance, and short-window ingestion deduplication.

## Lesson Completion (`learning.lesson_completed`), v1

This is a source learning event. It records that a learner completed a specific lesson attempt; it does not contain an XP amount, a streak outcome, or a quest or badge decision. Those outcomes are emitted as separate gamification events.

### Payload

| Field | Required | Meaning |
|---|---:|---|
| `completion_id` | Yes | The immutable identifier for one completed attempt, created by the learning service. This is the stable business action identity. |
| `lesson_id` | Yes | Stable identifier of the completed lesson. |
| `lesson_version` | No | Immutable version of the lesson definition completed. |
| `timezone` | Yes | Learner IANA timezone at completion, such as `Asia/Kolkata`. |
| `completion_kind` | No | One of `lesson`, `review`, or `remediation`. |

The machine-readable payload contract is [lesson_completed.schema.json](../schemas/events/lesson_completed.schema.json).

### Producer and idempotency rules

The learning service creates `completion_id` once, when the completion succeeds. It must send that same value on every delivery or resend of the same action. A resend uses a new envelope `event_id`; an event UUID identifies a delivery, while `completion_id` identifies the business action.

For this event, construct the envelope `dedup_key` as `<event_id>:<rule_version>`, for example `<event_id>:v1`. Ingestion currently expects rule version `v1` and rejects any other value with `dedup_key_mismatch`. The M1 ingestion deduplication window silently drops matching deliveries during its configured seven-day period.

The aggregate must also retain the completion/reward relationship beyond the ingestion window. A derived XP award uses the identity `xp:{completion_id}`. The applied rule version is normal reward payload data, not part of business identity. Consequently, a resend after eight days or a later rule update cannot increment the lesson count or create a second XP award.

Use the envelope `occurred_at` as the authoritative completion timestamp. Convert it using `payload.timezone` when deriving the learner's local activity date and streak state.

### Effective rule version

- The rule version that applies to an event is the trusted ingress configuration (`IngestConfig.rule_version`) in force when the event is **accepted**. It is never taken from the envelope and never derived from `occurred_at`. A late arrival whose `occurred_at` is before a version change is still judged under the version in force when it arrives.
- The applied version is persisted in two places: on the delivery's provenance row (`delivery_ids.rule_version`) and on the stored `XPAwarded` fact (`rule_version`).
- Historical facts keep their version on replay. Replay reads the stored `XPAwarded` amounts and versions, and never re-evaluates them under the current rule set.
- A redelivery of an already-accepted event (same `event_id`) is judged against the version it was accepted under. Its `dedup_key` must end in that version, even if the configuration has changed since. A new `event_id` must use the version currently configured.

### Rule sets

- Rule sets live in `src/state_machine/rulesets.json`, one entry per version. Each entry holds `xp_amount`, `quest_targets`, `badge_threshold`, `badge_id` and `sha256`. The `sha256` is the SHA-256 of the compact, key-sorted JSON of the four content fields, and it is verified when the rule sets are loaded.
- Published versions are immutable. A content change needs a new version with a new digest, never an edit to an existing one. Stored facts name a version, and that name must keep meaning the same content.
- At acceptance, the digest of the rule set that decided the event is recorded on the provenance row (`delivery_ids.rule_digest`), next to `rule_version`. Rows accepted before this was recorded have `NULL`.

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
  "subject_id": "L1",
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
    "source": "server_authoritative"
  }
}
```
