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

For this event, construct the envelope `dedup_key` as `lesson_completed:{completion_id}`. The M1 ingestion deduplication window silently drops matching deliveries during its configured seven-day period.

The aggregate must also retain the completion/reward relationship beyond the ingestion window. A derived XP award uses the identity `xp:{completion_id}`. The applied rule version is normal reward payload data, not part of business identity. Consequently, a resend after eight days or a later rule update cannot increment the lesson count or create a second XP award.

Use the envelope `occurred_at` as the authoritative completion timestamp. Convert it using `payload.timezone` when deriving the learner's local activity date and streak state.

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
  "dedup_key": "lesson_completed:cmp_492_003",
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
