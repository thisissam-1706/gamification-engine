# Week 4 Fix 6b: Enforce the dedup_key format

Status: **done. Not committed.**

- Week 3 review row 26 (second part): ingestion accepted any `dedup_key`. It
  now rejects every value except `"<event_id>:<rule_version>"`.
- `tests/ingestion/test_dedup_key.py` adds 8 tests, and **all 8 pass**.
- Suite excluding `tests/leaderboard` and `tests/notifications`: **191 passed,
  0 failed, 4 skipped**. After Fix 6a it was 183 passed, 4 skipped; the
  difference is the 8 new tests.
- This builds on the uncommitted Fix 6a working tree
  ([week4-fix6a-event-binding.md](week4-fix6a-event-binding.md)).

## Problem

`schemas/envelope.schema.json` described `dedup_key` only loosely, as
"Business key for idempotency (e.g., source event_id + rule_version)", and
the schema checks only `"type": "string"`. Ingestion never compared the key
with anything. Every test fixture sent `lesson_completed:{completion_id}`, a
value unrelated to the event_id, and it was accepted.

## Change

| File | Change |
|---|---|
| `src/ingestion/validation.py:13` | `DEFAULT_INGEST_RULE_VERSION = "v1"`, the rule version ingestion uses until a later step makes it configurable. |
| `src/ingestion/validation.py:33` | `expected_dedup_key(event_id, rule_version)` returns `f"{event_id}:{rule_version}"`. |
| `src/ingestion/service.py:153` | `ingest_lesson_completed` raises `ValidationError("dedup_key_mismatch")` when `envelope["dedup_key"] != expected_dedup_key(event_id, DEFAULT_INGEST_RULE_VERSION)`. |
| `src/ingestion/service.py:163` | `"dedup_key_mismatch"` is added to the recognised rejection reasons, so it is reported as itself instead of `schema_invalid`. |
| `schemas/envelope.schema.json:75` | The description now reads: `Source event_id + rule_version, in the exact format "<event_id>:<rule_version>". Ingestion rejects any other value with dedup_key_mismatch.` The schema still passes `Draft7Validator.check_schema`. |

### Where the check runs

The check is the **last** step inside the existing validation `try` block. It
runs after the envelope schema, event_id UUID, producer binding, event type,
reward-field and payload checks. So:

- If an envelope fails one of those earlier checks, it keeps its existing
  reason (for example `untrusted_producer`). One test pins this.
- The check runs before the Fix 6a `delivery_ids` lookup and before the
  aggregate is loaded. A mismatched delivery goes through the existing
  rejected-evidence path: one `rejected` row with `action_id` and `event_id`,
  no aggregate events, and no `delivery_ids` binding. The same event_id with
  a correct key can still be accepted afterwards.

`canonical_digest` (Fix 6a) still excludes `dedup_key`. That is consistent:
the key is now derived from `event_id`, which is also excluded.

## Fixture and helper files changed

Every envelope builder used the arbitrary key `lesson_completed:{completion_id}`.
Each one now uses
`expected_dedup_key(event_id, DEFAULT_INGEST_RULE_VERSION)`. No assertion was
changed or removed.

| File | What changed |
|---|---|
| `tests/ingestion/test_ingestion_service.py` | `_envelope`: resolves `event_id` once, then builds the key from it. `tests/ingestion/test_event_id_binding.py` and `tests/ingestion/test_dedup.py` import this helper, so they need no edit of their own for it. |
| `tests/ingestion/test_completed_quest_lesson.py` | `_envelope`: same change as above. |
| `tests/state_machine/test_active_state_path.py` | `_envelope`: builds the key from its `event_id` argument. Only the test file changed; `src/state_machine/` is untouched. |
| `tests/ingestion/test_dedup.py` | `test_duplicate_lesson_completion_is_dropped_within_ingestion_window` copies an envelope and gives it a new `event_id`, so it now also sets the matching `dedup_key`. The module docstring named the old `lesson_completed:cmp_492_003` key; it now describes the new format. Its asserts are unchanged. |

Before these fixture updates, the new check made 21 existing tests fail, all
because of the old keys. After the updates, all of them pass with their
original assertions.

One test passes a value that doesn't match on purpose: the
`"dedup_key": "other"` entry in
`tests/ingestion/test_event_id_binding.py::test_canonical_digest_covers_exactly_the_content_fields`.
It checks that the digest ignores `dedup_key`, never calls ingestion, and is
left as is.

## Tests (`tests/ingestion/test_dedup_key.py`)

| Test | What it proves |
|---|---|
| `test_expected_dedup_key_format` | `DEFAULT_INGEST_RULE_VERSION == "v1"`, and `expected_dedup_key(id, "v1") == f"{id}:v1"` (also for `v2`). |
| `test_correct_dedup_key_is_accepted` | `<event_id>:v1` is accepted, with `xp_total` 100 and no evidence rows. |
| `test_wrong_dedup_key_is_rejected_without_state_change` (×4) | Four wrong keys: the old `lesson_completed:cmp-A`, another event_id with `:v1`, the bare event_id, and the key with a trailing space. Each gives `rejected` / `dedup_key_mismatch`, `xp_total` 0, no completions, no new stored events, exactly one evidence row (`rejected`, `dedup_key_mismatch`, `cmp-A`, the event_id) and no `delivery_ids` row. A resend with the correct key is then accepted (`xp_total` 100). |
| `test_dedup_key_with_another_rule_version_is_rejected` | `expected_dedup_key(event_id, "v2")` gives `rejected` / `dedup_key_mismatch`, with `xp_total` 0, no new events and one rejected row. |
| `test_earlier_rejection_reasons_take_precedence_over_dedup_key` | An untrusted producer with a wrong key still gives `untrusted_producer`. |

## Before / after

| Delivery | Before | After |
|---|---|---|
| `dedup_key = "<event_id>:v1"` | accepted | accepted |
| `dedup_key = "lesson_completed:cmp-A"` | accepted, `xp_total` 100 | rejected, `dedup_key_mismatch`; `xp_total` 0; 1 evidence row |
| `dedup_key = "<event_id>:v2"` | accepted, `xp_total` 100 | rejected, `dedup_key_mismatch`; `xp_total` 0; 1 evidence row |
| suite (excluding leaderboard and notifications) | 183 passed, 4 skipped | 191 passed, 4 skipped |

## Doc fix

Only `docs/event-contracts.md` was changed:

- **Line 25:** the rule now reads: construct `dedup_key` as
  `<event_id>:<rule_version>`, for example `<event_id>:v1`; ingestion
  currently expects `v1` and rejects any other value with
  `dedup_key_mismatch`. It used to say `lesson_completed:{completion_id}`.
- **Line 44:** the example envelope now uses
  `"dedup_key": "019c26c2-7b87-7d0c-89db-8ef5b5110c4a:v1"`. That equals
  `expected_dedup_key(example["event_id"], DEFAULT_INGEST_RULE_VERSION)`,
  checked by parsing the JSON block. It used to be
  `"lesson_completed:cmp_492_003"`.

I searched everything under `docs/` for `lesson_completed:`, `dedup_key` and
the old schema wording `source event_id + rule_version`. Apart from
`docs/event-contracts.md` and the audit files, which only describe the change,
there is one other mention. It is listed here, not changed:

- `docs/traceability-matrix.md:33` (AT-1, M1): "when the identical event
  (same dedup_key) is re-ingested within the 7-day window, then it is
  silently discarded". With the new format, the same `dedup_key` means the
  same `event_id`. That delivery now returns `duplicate_event_id` and writes
  one duplicate evidence row (Fix 6a), so it is not silent. A resend with a
  new `event_id` has a different `dedup_key` and is caught as
  `duplicate_completion_id` instead.

The rest of line 25 also still says the dedup window "silently drops matching
deliveries". That wording is not about the key format and was left as it is.
It has the same mismatch with the evidence rows as the traceability-matrix
line.
