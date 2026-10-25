# Week 4 Fix 7b: Read-only delivery trace

Status: **done. Not committed.**

- Week 3 review row 26 (fourth part): trace source → reward → exposure.
  `trace_delivery` follows one transport `event_id` to its provenance, its
  `LessonCompleted` and `XPAwarded` facts, and every evidence row that
  mentions it. `exposures` is an empty tuple until the pilot.
- `tests/ingestion/test_trace.py` adds 5 tests, and **all 5 pass**. Every
  hand-written expected value matched on the first run.
- Suite excluding `tests/leaderboard` and `tests/notifications`: **202 passed,
  0 failed, 4 skipped**. After Fix 7a it was 197 passed, 4 skipped; the
  difference is the 5 new tests.
- No existing test or assertion changed. `src/state_machine/` is unchanged.
- This builds on the uncommitted Fix 6a, 6b and 7a working tree.

## Problem

After Fix 7a each piece of the chain was stored, but in two different places:

- provenance in `delivery_ids`;
- facts in the event store;
- outcomes in `ingestion_evidence`.

Nothing joined them. Answering "which delivery produced this XP, and what
happened to its retries" meant writing SQL by hand against two stores.

## Change

### `src/ingestion/trace.py` (new)

`trace_delivery(application, evidence, event_id) -> DeliveryTrace | None`
returns frozen dataclasses:

| Field | Type | Source |
|---|---|---|
| `provenance` | `DeliveryProvenance`: `event_id, learner_id, action_id, digest, producer, event_type, schema_version, occurred_at, ingested_at, correlation_id, causation_id, dedup_key, rule_version` | `delivery_ids` row, via `evidence.delivery_binding(event_id)` |
| `lesson_completed` | `LessonCompletedFact(originator_version, source_event_id)`, or `None` | stored `Learner.LessonCompleted` with `completion_id == action_id` |
| `xp_awards` | `tuple[XPAwardedFact(reward_id, amount, rule_version), ...]` | every stored `Learner.XPAwarded` with `completion_id == action_id`. `amount` is the event's `xp_amount`. |
| `evidence` | `tuple[EvidenceRecord, ...]` | `ingestion_evidence` rows with `event_id = ?` **or** `original_ref = ?`, ordered by `id` |
| `exposures` | `tuple[()]`, always `()` | Exposure facts arrive with the pilot. A comment in the dataclass says so. |

How it reads the data:

- **Unknown `event_id`.** If `delivery_ids` has no row, the result is `None`.
  A delivery that was never accepted (a duplicate under a new `event_id`, or
  a rejection) has no row, so it returns `None` as well. Those deliveries
  appear in the trace of the delivery they point back to.
- **Read-only.** Only `SELECT`s and `application.events.get(...)`. The
  aggregate is never loaded through the repository, and nothing is saved or
  committed.
- **"Mentions".** Matching on `original_ref` as well as `event_id` means a
  resend under a new `event_id` (`duplicate_completion_id`, whose
  `original_ref` is the first `event_id`) appears in the trace of the
  delivery it duplicated.
- **Not in the provenance.** `provenance_json` is not part of
  `DeliveryProvenance`, because it was not in the requested field list. It is
  still on the `delivery_ids` row.

### `src/ingestion/evidence.py`

`EvidenceStore.list_mentioning(event_id)` (`evidence.py:176`) is a new
`SELECT`. It is the only change to an existing file.

## Tests (`tests/ingestion/test_trace.py`)

The scenario: create the learner (version 1), accept `cmp-A` under `…07b1`,
resend it unchanged, then send `cmp-B` under the same `event_id`.

| Test | What it proves |
|---|---|
| `test_trace_follows_one_delivery_to_its_reward_and_evidence` | The exact trace: the full literal provenance; `LessonCompletedFact(2, "…07b1")`; `(XPAwardedFact("xp:cmp-A", 100, "v1"),)`; evidence rows `(1, duplicate, cmp-A, …07b1, duplicate_event_id, original_ref …07b1)` and `(2, rejected, cmp-B, …07b1, conflicting_event_id_reuse, original_ref cmp-A)`; `exposures == ()`. It also proves the trace is read-only: `total_changes` on the evidence connection is unchanged, the event list is unchanged, the SQLite file's SHA-256 is byte-identical, and `xp_total` is 100. |
| `test_trace_is_identical_after_restart` | The same exact assertions pass after closing and reopening both the application and the evidence store on the same file, and the two traces are equal. |
| `test_unknown_event_id_traces_to_none` | An unknown `event_id` returns `None`, and the file hash is unchanged. |
| `test_resend_under_a_new_event_id_is_traced_through_original_ref` | A `duplicate_completion_id` row from another `event_id` appears in the original's trace through `original_ref`. The resend's own `event_id` returns `None`. |
| `test_delivery_that_predates_provenance_still_traces` | `delivery_ids` is dropped, so the store rebuilds it from `accepted_deliveries` (pre-Fix-6a file). The provenance then has every column `None`, including `digest`. The facts (`originator_version` 2, `xp:cmp-A`, 100, `v1`) are still found, and `evidence == ()`. |

The only values not written by hand are `learner_id`, which is a random
aggregate UUID, `digest`, computed with `canonical_digest(first)` because it
hashes that UUID, and `received_at`, a wall-clock timestamp that is only
checked to be timezone-aware ISO.

## Worked example (real values)

These come from running the test scenario once against a scratch SQLite file
and printing `dataclasses.asdict(trace_delivery(app, evidence, "…07b1"))`:

```json
{
  "provenance": {
    "event_id": "00000000-0000-4000-8000-0000000007b1",
    "learner_id": "abfbc600-5034-4d16-863b-f0077682f7f0",
    "action_id": "cmp-A",
    "digest": "46e901381e7d91aaae7249f413aff9944030e542116052e2a4c0c5cabc9c4265",
    "producer": "learning-svc",
    "event_type": "learning.lesson_completed",
    "schema_version": 1,
    "occurred_at": "2026-09-01T00:00:00+00:00",
    "ingested_at": "2026-09-01T00:01:00+00:00",
    "correlation_id": "00000000-0000-4000-8000-0000000007c1",
    "causation_id": null,
    "dedup_key": "00000000-0000-4000-8000-0000000007b1:v1",
    "rule_version": "v1"
  },
  "lesson_completed": {
    "originator_version": 2,
    "source_event_id": "00000000-0000-4000-8000-0000000007b1"
  },
  "xp_awards": [
    {"reward_id": "xp:cmp-A", "amount": 100, "rule_version": "v1"}
  ],
  "evidence": [
    {
      "id": 1, "kind": "duplicate",
      "learner_id": "abfbc600-5034-4d16-863b-f0077682f7f0",
      "action_id": "cmp-A",
      "event_id": "00000000-0000-4000-8000-0000000007b1",
      "reason": "duplicate_event_id",
      "original_ref": "00000000-0000-4000-8000-0000000007b1",
      "received_at": "2026-10-25T11:01:08.188787+00:00"
    },
    {
      "id": 2, "kind": "rejected",
      "learner_id": "abfbc600-5034-4d16-863b-f0077682f7f0",
      "action_id": "cmp-B",
      "event_id": "00000000-0000-4000-8000-0000000007b1",
      "reason": "conflicting_event_id_reuse",
      "original_ref": "cmp-A",
      "received_at": "2026-10-25T11:01:08.189904+00:00"
    }
  ],
  "exposures": []
}
```

Reading it, from source to reward:

1. Delivery `…07b1` came from `learning-svc`, `correlation_id …07c1`, with no
   causation.
2. It was bound to action `cmp-A`.
3. It became `LessonCompleted` at learner version 2, whose `source_event_id`
   points back to `…07b1`.
4. That produced exactly one reward, `xp:cmp-A` = 100 XP under rule `v1`.
5. The identical resend was recorded as a duplicate.
6. The attempt to reuse `…07b1` for `cmp-B` was rejected, with
   `original_ref` naming the action it was really bound to.
7. Nothing has been exposed yet.

## Known limits

- **Exposure.** The "exposure" link of row 26 is still empty. It needs
  exposure facts from the pilot.
- **Scope.** The trace starts from a transport `event_id` and covers only
  lesson completions. Starting from a `reward_id` or a learner would need a
  separate entry point.
- **Event scan.** It reads the learner's whole event stream, which is linear
  in stream length. That is fine at current sizes; there is no index from
  `completion_id` to event version.
