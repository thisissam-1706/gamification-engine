# Week 6 / M4 / Step 1 — `learning.checkpoint_attempted` v1 ingestion

Status: **Prompt 1 of this step — read-only audit. No source or test file was changed.**
Git rule in force: no state-changing git commands (read-only `status`/`diff`/`log` only).
Baseline: branch `main`, HEAD `0da8dd5`, working tree clean at start.

## 1. Changes made

- Created this file (`docs/audit/week6-m4-step1-checkpoint-event.md`). Nothing else.

## 2. Before / after behaviour

No behaviour change in this prompt. Current behaviour for the new type (verified by reading, not by running):
an envelope with `event_type = learning.checkpoint_attempted` sent to `ingest_lesson_completed` is
rejected `untrusted_producer` (see §4, check #3), because `learning-svc` is only allowed `learning.lesson_completed`.

## 3. How `learning.lesson_completed` flows end to end

### 3.1 Schemas (`src/schemas/`)

- `envelope.schema.json` (Draft-07, `additionalProperties: false`). Required: `event_id` (uuid format), `event_type`
  (pattern `^[a-z]+(\.[a-z_]+)+$`, so `learning.checkpoint_attempted` already matches), `schema_version` (**enum [1]**,
  shared by every event type), `occurred_at`, `ingested_at` (date-time), `subject_id`, `producer`, `correlation_id`,
  `causation_id` (string|null), `dedup_key`, `payload` (free object), `provenance` (`source` in `client|server_authoritative`).
- `events/lesson_completed.schema.json`: payload requires `completion_id`, `lesson_id`, `timezone`; optional
  `lesson_version`, `assignment_id`, `completion_kind` (`lesson|review|remediation`); `additionalProperties: false`.
- `events/checkpoint_attempted.schema.json` **already exists** and is not referenced by any code, test or doc
  (grep over the repo finds only the file itself). Payload requires `attempt_id`, `assessment_id`,
  `assessment_version` (int ≥ 1), `timezone`; `additionalProperties: false`. No `passed`/`score` (those are in
  `checkpoint_completed.schema.json`, also unreferenced).
- Schemas ship in the wheel via `pyproject.toml` package-data `src = ["schemas/*.json", "schemas/events/*.json"]`
  (a new schema file in that folder is already covered).

### 3.2 Ingestion entry point: `ingest_lesson_completed` (`src/ingestion/service.py:117`)

Signature: `(application, evidence, envelope, *, authenticated_producer, config=DEFAULT_CONFIG) -> IngestionResult(status, reason)`.
Statuses seen: `accepted`, `duplicate`, `rejected`.

1. **Receive time** (`:127`): `received = config.clock()` (trusted clock; must be tz-aware, else `ValueError`).
   Envelope `ingested_at` is never used for decisions.
2. **Pre-extract identity** (`:131-144`): `learner_id = subject_id`, `action_id = payload.completion_id`
   (hard-coded key), `assignment_id = payload.assignment_id`, `event_id`. Used for evidence rows even on rejection.
3. **Validation block** (`:146-191`), in this order; first failure rejects with evidence, no state change:
   1. `validate_lesson_completed_envelope` — supported `schema_version` for the event type
      (`unsupported_schema_version`); `occurred_at`/`ingested_at` parse as tz-aware ISO (`schema_invalid`);
      `payload.timezone` is a valid IANA zone (`schema_invalid`).
   2. Envelope JSON Schema (+ `FormatChecker`) → `schema_invalid`.
   3. `UUID(event_id)`.
   4. `authenticated_producer == envelope.producer` else `untrusted_producer`.
   5. `validate_producer(producer, event_type, provenance)` → `untrusted_producer` if the type is not in
      `ALLOWED_EVENT_TYPES[producer]` or `provenance.source == "server_authoritative"`.
   6. Hard-coded `event_type != "learning.lesson_completed"` → `untrusted_producer` (`:157`).
   7. Reward fields (`xp_amount`, `reward_id`, `new_streak`) in payload → `reward_field_in_source_event`.
   8. `validate_lesson_completed_payload` (lesson_completed payload schema) → `schema_invalid`.
   9. `UUID(subject_id)` (learner aggregate id).
   Any `ValidationError` whose message is not in a small allow-list is mapped to `schema_invalid`;
   `KeyError/TypeError/ValueError` → `schema_invalid`.
4. **dedup_key check** (`:195-212`): see §5.1.
5. **Event-id binding** (`:213-217`): if `delivery_ids` already has this `event_id` → `_resolve_bound_event_id` (§5.3).
6. **Late window** (`:219-231`): see §5.2.
7. **Load aggregate** (`:233-236`): `application.repository.get(UUID(subject_id))`; missing aggregate → `RuntimeError`
   (learner must pre-exist).
8. **Crash-gap backfill** (`:238-255`): aggregate already holds this `event_id` but no binding row → bind then resolve.
9. **completion_id duplicate by evidence/aggregate** (`:257-272`): `accepted_deliveries` row or
   `learner.completion_sources[completion_id]` → `duplicate / duplicate_completion_id`.
10. **Accept** (`:274-285`): `learner.complete_lesson(completion_id, lesson_id, source_event_id=event_id,
    occurred_at, timezone, correlation_id, causation_id, rule_version=config.rule_version, completion_kind=...)`.
    `lesson_version` and `assignment_id` are **not** passed to the aggregate (assignment_id is only used for the
    "quest already complete" evidence row).
11. **Persist** (`:298-367`): `application.save(learner)`. `IntegrityError` → reload and retry once; second failure →
    `rejected / concurrency_conflict`. Conflicting event_id reuse detected here too.
12. **Bind + remember** (`:368-386`): `evidence.remember_delivery(learner, completion_id, event_id)` and
    `evidence.bind_delivery(event_id, learner, completion_id, digest, producer, event_type, schema_version,
    occurred_at, ingested_at, correlation_id, causation_id, dedup_key, provenance, rule_version, rule_digest, received_at)`.
13. Returns `accepted` (or `accepted / quest_already_completed` plus a `duplicate` evidence row when the
    payload's `assignment_id` quest was already complete).

### 3.3 `validation.py`

- `DEFAULT_INGEST_RULE_VERSION = "v1"`.
- `SUPPORTED_SCHEMA_VERSIONS = {"learning.lesson_completed": {1}}`. For an unlisted type the lookup returns `None`
  and the version check is **skipped** (only the envelope enum [1] applies).
- `ALLOWED_EVENT_TYPES`: `learning-svc → {learning.lesson_completed}`; `gamification-engine → 7 gamification.* types`.
  Nothing for `assessment-svc` (named as an example producer in the envelope schema).
- `expected_dedup_key(event_id, rule_version) = f"{event_id}:{rule_version}"`.
- `lesson_completed_validator()` (lru_cached Draft7Validator for the lesson payload schema),
  `validate_lesson_completed_payload`, `validate_lesson_completed_envelope` (named lesson-specific but only the
  `SUPPORTED_SCHEMA_VERSIONS` lookup is type-aware; the timestamp and timezone checks read `payload["timezone"]`).

### 3.4 `config.py`

`IngestConfig(rule_version, late_window=72h, clock=utc_now)`, frozen. `rule_version` must be in `RULESETS`;
`late_window` must be a positive `timedelta` or `None`; `clock` callable. `DEFAULT_CONFIG = IngestConfig("v1")`.
Config is trusted ingress state, never read from the envelope.

### 3.5 `evidence.py` (SQLite, same DB file as the event store)

Tables: `ingestion_evidence` (kind `duplicate|rejected`, learner_id, action_id, event_id, reason, original_ref,
received_at; unique index on duplicate rows `(kind, learner_id, action_id, event_id, reason)`), `accepted_deliveries`
(PK `(learner_id, action_id)` → event_id), `delivery_ids` (PK `event_id` → learner_id, action_id, digest, plus
provenance columns producer/event_type/schema_version/occurred_at/ingested_at/correlation_id/causation_id/dedup_key/
provenance_json/rule_version/rule_digest/received_at). Everything is keyed on a single generic `action_id` string, so
the store itself is not lesson-specific. `trace.py` reads these and is lesson-specific only in its aggregate lookup
(`Learner.LessonCompleted` / `XPAwarded` by `completion_id == binding.action_id`).

### 3.6 Aggregate: `Learner.complete_lesson` (`src/state_machine/learner.py:83`)

- Returns `False` (no events) if `completion_id in completed_lesson_ids`, or if `xp:{completion_id}` is already in
  `awarded_reward_ids`. Otherwise it emits, in one `save`: `LessonCompleted`, `XPAwarded` (id `xp:{completion_id}`,
  amount from `xp_for_completion(rule_version, repeat_index)`), `StreakUpdated` (when activity date derivable),
  possibly `BadgeAwarded`, and quest `QuestProgressed`/`QuestCompleted`/`QuestExpired` for assigned quests.
- **Idempotency**: purely the *presence* of `completion_id` in `completed_lesson_ids` (folded from `LessonCompleted`).
  `completion_sources[completion_id] = source_event_id` records which delivery won. No content is compared inside the
  aggregate. Reward identity depends on `completion_id` only, never on `event_id` or rule version.
- `_record_lesson_completion` folds `completed_lesson_ids`, `completion_sources`, counts, `activity_dates`,
  `timezone_history`.
- Aggregate has no concept of a checkpoint attempt (no `attempt_id` set, no checkpoint event/fold).
  `grep` finds no `checkpoint` in `src/state_machine`.

### 3.7 `docs/event-contracts.md`

Documents only `learning.lesson_completed` v1 (payload table, idempotency, effective rule version, rule sets,
M6 XP fields, late window, example). No section for checkpoint events. Staleness noticed (not changed):
line 25 says ingestion "currently expects rule version `v1`" and describes a "seven-day" M1 dedup window silently
dropping matches; the code now uses `IngestConfig.rule_version` and a durable `event_id`/`completion_id` evidence trail
with a 72 h late window (lines 56-62). The doc's own §Late-event window matches the code.

## 4. Every place `event_type` is checked or routed

| # | Location | What it does |
|---|---|---|
| 1 | `src/schemas/envelope.schema.json` `event_type` | Pattern `^[a-z]+(\.[a-z_]+)+$` only; no enum. |
| 2 | `src/ingestion/validation.py:15-17` `SUPPORTED_SCHEMA_VERSIONS` | Per-type allowed `schema_version`; used in `validate_lesson_completed_envelope`. Only lesson type listed; unlisted types skip this check. |
| 3 | `src/ingestion/validation.py:19-30` `ALLOWED_EVENT_TYPES` + `validate_producer` | Producer→type allow-list; `untrusted_producer` if absent. **Blocks checkpoint_attempted today.** |
| 4 | `src/ingestion/service.py:157` | Hard-coded `event_type != "learning.lesson_completed"` → `untrusted_producer`. |
| 5 | `src/ingestion/service.py:147` | Calls `validate_lesson_completed_envelope` (lesson-named, reads `payload["timezone"]`). |
| 6 | `src/ingestion/service.py:162` | Always validates payload against the **lesson** schema (`validate_lesson_completed_payload`) — no dispatch by type. |
| 7 | `src/ingestion/service.py:132-144` | Identity extraction hard-codes `payload.completion_id` (action id) and `payload.assignment_id`. |
| 8 | `src/ingestion/service.py:160` | Reward-field guard (`xp_amount`, `reward_id`, `new_streak`) — applies to whichever payload arrives. |
| 9 | `src/ingestion/service.py:33-40` `DIGEST_FIELDS` | `event_type` is part of the canonical digest, so the same event_id re-sent with a different type conflicts. |
| 10 | `src/ingestion/service.py:375` + `evidence.py` `delivery_ids.event_type` | Stores accepted `event_type` as provenance; surfaced by `trace.py:65`. Not used for routing. |
| 11 | `src/ingestion/service.py:275-285` | Acceptance goes straight to `Learner.complete_lesson`; no router. The whole function is `ingest_lesson_completed` (one type). |
| 12 | `src/ingestion/trace.py` | Looks up `Learner.LessonCompleted`/`XPAwarded` only; `DeliveryTrace.lesson_completed` field. |
| 13 | `src/notifications/*`, `dispatcher.py` | Own internal `event_type` strings (`notification_*`); unrelated to ingestion routing. |
| 14 | Tests that pin type handling | `tests/ingestion/test_ingestion_service.py:436-460` (derived gamification types → `untrusted_producer`); `test_event_id_binding.py:296` (`learning.other` changes digest). |

Not routing, but related: `src/experiments/metrics.py:176 first_eligible_checkpoint_mastery` and
`src/scale_and_recovery/experiment_repro.py:61-75` already use `attempt_id`/`assessment_id`/`assessment_version`
in their own synthetic data; they do not go through ingestion.

## 5. Dedup, late window, same-id-different-content

### 5.1 `dedup_key`

- Format `"<event_id>:<rule_version>"` (`validation.py:33`). Checked at `service.py:198-212` *after* envelope/producer/
  payload validation and *before* the binding/late-window checks.
- `key_version` = the `rule_version` stored on the existing `delivery_ids` row for this `event_id` if there is one,
  else `config.rule_version`. Mismatch → `rejected / dedup_key_mismatch`, evidence row, no state change.
- `dedup_key` is **not** the deduplication mechanism itself: real dedup is by `event_id` (delivery_ids PK) and by
  `completion_id` (aggregate + `accepted_deliveries`). `dedup_key` is only verified, then stored for provenance.
  It is excluded from the digest (`DIGEST_FIELDS`).

### 5.2 72-hour late window

- `IngestConfig.late_window = timedelta(hours=72)` (`config.py:10`), `None` disables.
- `service.py:220-231`: `if received - occurred_at > late_window` → `rejected / outside_late_window`, evidence row,
  no completion/reward/streak/binding. `received` is `config.clock()`. Exactly 72 h is accepted (strict `>`).
  A future `occurred_at` is not rejected.
- Position in the pipeline: after validation and dedup_key, after the `event_id` binding lookup (so a redelivery of an
  accepted `event_id` is never "late"), **before** the completion_id duplicate checks (so a *new* event_id carrying
  an old, already-accepted completion_id that is >72 h old is reported `outside_late_window`, not
  `duplicate_completion_id`).
- Existing tests mostly bypass this via `tests/ingestion/support.py` (`late_window=None`).

### 5.3 Same `completion_id` with DIFFERENT content

Behaviour depends on whether the `event_id` is the same.

| Case | Outcome | Source |
|---|---|---|
| Same `event_id`, same `completion_id`, same digest content | `duplicate / duplicate_event_id` | `_resolve_bound_event_id` |
| Same `event_id`, same `completion_id`, changed digest content (event_type, schema_version, subject_id, producer, occurred_at, or payload — e.g. different `lesson_id`) | `rejected / conflicting_event_id_reuse`; original unchanged | `_resolve_bound_event_id`, digest compare |
| Same `event_id`, different `completion_id` | `rejected / conflicting_event_id_reuse` | binding.action_id mismatch |
| **New `event_id`**, same `completion_id`, same content | `duplicate / duplicate_completion_id`, `original_ref` = first event_id | `service.py:257-272` |
| **New `event_id`**, same `completion_id`, **different content** (different `lesson_id`, `timezone`, `occurred_at`, `completion_kind`, etc.) | **`duplicate / duplicate_completion_id`**. Content is never compared on this path; the first accepted content stays, the differing resend is silently treated as a plain duplicate (evidence row only, no conflict flag, its own event_id is not bound). | `service.py:257-272`; aggregate returns False at `learner.py:106` |
| Same `completion_id` for a different `subject_id` | Not a duplicate: `accepted_deliveries` and `completion_sources` are per learner. | evidence PK `(learner_id, action_id)` |

Digest fields are only compared against an `event_id`'s own binding, never against the first delivery of a
`completion_id`. So "same business action id, changed content, new transport id" is the unflagged case.

### 5.4 Other outcome notes

- Rejected deliveries never bind their `event_id`; a duplicate_completion_id delivery doesn't either.
- A rejected delivery (e.g. `outside_late_window`) leaves no `accepted_deliveries`/`delivery_ids` row, so it can be
  retried with the same `event_id` later and judged afresh.

## 6. Existing tests covering `lesson_completed` ingestion

`tests/ingestion/`:
- `test_ingestion_service.py` — main flow, producer/derived-event rejection, schema/timestamp/timezone errors, reward-field rejection (defines `_envelope`, reused elsewhere).
- `test_lesson_completed_contract.py` — payload schema: required identity fields, reward fields rejected, valid payload accepted.
- `test_dedup.py` — duplicate within window, resend after window, crash-before-ack single reward.
- `test_event_id_binding.py` — event_id binding, conflicting reuse, digest, restart, crash-gap backfill, migration, digest field set.
- `test_dedup_key.py` — key format, mismatch rejection, rule-version mismatch, rejection precedence.
- `test_late_window.py` and `test_late_window_behaviour.py` — 72 h boundary, clock, ingested_at ignored, redelivery after window, unbounded.
- `test_provenance.py`, `test_trace.py` — stored envelope provenance and delivery→reward trace.
- `test_rule_selection.py`, `test_rule_version_change.py`, `test_rule_digest.py` — rule version/digest at acceptance.
- `test_completed_quest_lesson.py` — assignment_id / already-completed quest path.
- `test_evidence_migration_concurrency.py` — evidence store migration/concurrency.
- `support.py` — shared helper (late window off).

Elsewhere using the ingestion path: `tests/packaging/test_wheel_install.py`, `tests/packaging/test_wheel_contents.py`,
`tests/state_machine/test_xp_decay.py`, `test_streak_updates.py`, `test_quest.py`, `test_quest_reader.py`,
`test_active_state_path.py`, `tests/abuse/*` (fixtures, harness, detectors, receive timing, review misuse, signal store),
`tests/leaderboard/test_leaderboard_e2e.py`, `test_leaderboard_projection.py`,
`tests/scale_and_recovery/test_workload_generator.py`, `tests/simulator/test_pilot_report.py`.

## 7. Tests added

None (read-only prompt).

## 8. Commands run (read-only)

All run from the repo root. Outputs are source/file listings, summarised rather than pasted verbatim because the
full source text is in the repo and the same lines are cited above; no test suite was run.

| Command | Result |
|---|---|
| `git status --short` | empty (clean tree) |
| `ls docs docs/audit` | confirmed docs and existing audit files (`week4-*` series; no week6 file existed) |
| `find src/schemas src/ingestion src/state_machine -type f \| grep -v pycache` | listed 3 top-level schemas, 18 files in `src/schemas/events/` (incl. `checkpoint_attempted`, `checkpoint_completed`), 6 ingestion files, 5 state_machine files |
| `wc -l src/ingestion/*.py src/state_machine/learner.py docs/event-contracts.md` | config 40, evidence 314, service 399, trace 105, validation 82, learner 515, event-contracts 89 |
| `cat` of envelope, lesson_completed, checkpoint_attempted, checkpoint_completed schemas | content summarised in §3.1 |
| `cat -n src/ingestion/{config,validation,service}.py`, `docs/event-contracts.md`, `src/ingestion/evidence.py`, `src/state_machine/learner.py` (lines 1-330), `src/ingestion/trace.py` | content summarised in §3-5 |
| `grep -rn "event_type\|learning\.\|checkpoint_" src ... --include='*.py'` | results in §4 (rows 13, "not routing" note) |
| `grep -rln "checkpoint_attempted\|checkpoint_completed\|CheckpointAttempted" . --exclude-dir=...` | only `src/schemas/events/checkpoint_attempted.schema.json` matched |
| `grep -rlE "ingest_lesson_completed\|lesson_completed\|completion_id" tests` | list in §6 |
| `grep -n "^def test"` on dedup / binding / late-window / dedup_key / contract tests; targeted `sed -n` on test_event_id_binding, test_ingestion_service, test_dedup | test names used in §5-6 |
| `git log --oneline -3`, `python --version` | `0da8dd5`, `3600816`, `4163eb6`; Python 3.14.6 |

One earlier `grep ... --include=*.py` was mistyped without quotes and failed under zsh (`no matches found`); it was
re-run with quotes. No output from it was used.

## 9. Assumptions

- "Checkpoint event" means only `learning.checkpoint_attempted` v1 using the existing `checkpoint_attempted.schema.json`; I did not assume `checkpoint_completed` is in scope for M4 step 1.
- Line numbers cite the tree at `0da8dd5`.
- Behaviour claims in §5.3 are derived from reading code and the cited tests, not from executing new probes.

## 10. Things I am unsure about

1. The existing `checkpoint_attempted.schema.json` has no producer field implied. `assessment-svc` appears as a producer example in the envelope schema but is absent from `ALLOWED_EVENT_TYPES`; which producer should send checkpoint attempts is undecided.
2. Whether the checkpoint attempt's business identity (`attempt_id`) should share the lesson `completion_id` namespace/tables (`accepted_deliveries`, `completion_sources` are keyed by one generic action id) — to be decided in design, not now.
3. §5.3 "new event_id, same completion_id, different content → plain duplicate" is existing behaviour with no test I found asserting the *different-content* variant (tests cover identical content with a new event_id, and changed payload with the *same* event_id). Worth confirming before relying on it for checkpoints.
4. `docs/event-contracts.md` lines 25-27 look stale vs. code (see §3.7); not touched.
5. Whether `lesson_version`/`assignment_id` being dropped before the aggregate is intentional.

---

# Prompt 2 — failing tests only (no implementation)

## Changes made

- Added `tests/interventions/__init__.py` (empty; every other test folder has one).
- Added `tests/interventions/test_checkpoint_attempt_ingestion.py` (9 test cases, 5 test functions + 1 parametrised).
- No file under `src/` or any existing test was touched. No git state changes.

## Before / after behaviour

No behaviour change. The new tests describe behaviour that does not exist yet.

## Tests added (all currently failing)

| Test | Covers |
|---|---|
| `test_valid_checkpoint_attempt_is_accepted_and_stored_once` | Requirement 1: accepted; learner has exactly one attempt with the same fields; one `CheckpointAttempted` event; no evidence row. |
| `test_invalid_checkpoint_payload_is_rejected_without_state_change[missing_attempt_id]` | Requirement 2 |
| `...[passed_is_integer_1]`, `...[passed_is_string_true]` | Requirement 2: strict boolean (both `1` and `"true"`) |
| `...[assessment_version_0]`, `...[unknown_extra_field]` | Requirement 2 |
| `test_accepting_an_attempt_leaves_xp_streak_and_quests_unchanged` | Requirement 3: learner with an assigned quest and one lesson (xp>0, streak 1, quest progress 1); a checkpoint on the next local day leaves XP, reward ids, streak, activity dates, quest progress/completions and lesson count identical, and adds exactly one `CheckpointAttempted` event and nothing else. |
| `test_first_attempt_per_assessment_ignores_arrival_order` | Requirement 4: later attempt arrives first, earlier last; also a second assessment. |
| `test_first_attempt_ties_on_occurred_at_break_by_attempt_id` | Requirement 4 tie-break (extra, from the stated `(occurred_at, attempt_id)` order). |

Each rejection test asserts `rejected / schema_invalid`, unchanged event stream, and one `schema_invalid` evidence row.
Style follows `tests/ingestion/test_ingestion_service.py` (tmp_path SQLite, `LearnerApplication` + `EvidenceStore`,
try/finally close, `_envelope` shape, `expected_dedup_key`). Lesson setup in the unchanged-state test reuses
`tests.ingestion.support.ingest_lesson_completed` and `_envelope` from `test_ingestion_service.py`.
The checkpoint ingest uses `replace(DEFAULT_CONFIG, late_window=None)` because the fixed 2026-09 timestamps would
otherwise fall outside the real-clock 72 h window (same reason as `tests/ingestion/support.py`).

## Assumed API (not specified in the task; chosen by me, adjust if you prefer others)

- `src.ingestion.service.ingest_checkpoint_attempted(application, evidence, envelope, *, authenticated_producer, config)`; same signature and `IngestionResult` as `ingest_lesson_completed`.
- `Learner.record_checkpoint_attempt(...)` emitting `CheckpointAttempted` (as specified); the tests only observe its effects through ingestion, so its argument list is not pinned.
- `Learner.checkpoint_attempts`: `dict[attempt_id -> {attempt_id, assessment_id, assessment_version, passed, timezone, occurred_at}]`, `occurred_at` equal to the envelope string.
- `Learner.first_checkpoint_attempt_ids`: `dict[assessment_id -> attempt_id]`.
- Producer is `learning-svc` (same as lesson_completed); the task did not name another.
- Event class is looked up by `type(event).__name__ == "CheckpointAttempted"`.

## Command run and output

```
python -m pytest tests/interventions/test_checkpoint_attempt_ingestion.py -v
```

Per-test result lines and summary (full traceback output was long and repetitive; each failure is identical, see below):

```
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0 -- /home/sampadram/miniconda3/bin/python
cachedir: .pytest_cache
rootdir: /home/sampadram/Sampadram/IITH/SEM 7/DATGC/project/gamification-engine
configfile: pyproject.toml
plugins: anyio-4.12.1, asyncio-1.4.1
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 9 items

tests/interventions/test_checkpoint_attempt_ingestion.py::test_valid_checkpoint_attempt_is_accepted_and_stored_once FAILED [ 11%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_invalid_checkpoint_payload_is_rejected_without_state_change[missing_attempt_id-<lambda>] FAILED [ 22%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_invalid_checkpoint_payload_is_rejected_without_state_change[passed_is_integer_1-<lambda>] FAILED [ 33%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_invalid_checkpoint_payload_is_rejected_without_state_change[passed_is_string_true-<lambda>] FAILED [ 44%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_invalid_checkpoint_payload_is_rejected_without_state_change[assessment_version_0-<lambda>] FAILED [ 55%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_invalid_checkpoint_payload_is_rejected_without_state_change[unknown_extra_field-<lambda>] FAILED [ 66%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_accepting_an_attempt_leaves_xp_streak_and_quests_unchanged FAILED [ 77%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_first_attempt_per_assessment_ignores_arrival_order FAILED [ 88%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_first_attempt_ties_on_occurred_at_break_by_attempt_id FAILED [100%]

=================================== FAILURES ===================================
__________ test_valid_checkpoint_attempt_is_accepted_and_stored_once ___________
...
=========================== short test summary info ============================
FAILED tests/interventions/test_checkpoint_attempt_ingestion.py::test_valid_checkpoint_attempt_is_accepted_and_stored_once
FAILED tests/interventions/test_checkpoint_attempt_ingestion.py::test_invalid_checkpoint_payload_is_rejected_without_state_change[missing_attempt_id-<lambda>]
FAILED tests/interventions/test_checkpoint_attempt_ingestion.py::test_invalid_checkpoint_payload_is_rejected_without_state_change[passed_is_integer_1-<lambda>]
FAILED tests/interventions/test_checkpoint_attempt_ingestion.py::test_invalid_checkpoint_payload_is_rejected_without_state_change[passed_is_string_true-<lambda>]
FAILED tests/interventions/test_checkpoint_attempt_ingestion.py::test_invalid_checkpoint_payload_is_rejected_without_state_change[assessment_version_0-<lambda>]
FAILED tests/interventions/test_checkpoint_attempt_ingestion.py::test_invalid_checkpoint_payload_is_rejected_without_state_change[unknown_extra_field-<lambda>]
FAILED tests/interventions/test_checkpoint_attempt_ingestion.py::test_accepting_an_attempt_leaves_xp_streak_and_quests_unchanged
FAILED tests/interventions/test_checkpoint_attempt_ingestion.py::test_first_attempt_per_assessment_ignores_arrival_order
FAILED tests/interventions/test_checkpoint_attempt_ingestion.py::test_first_attempt_ties_on_occurred_at_break_by_attempt_id
============================== 9 failed in 0.22s ===============================
```

Every one of the 9 failures has the same cause, at `tests/interventions/test_checkpoint_attempt_ingestion.py:74`:

```
E   ImportError: cannot import name 'ingest_checkpoint_attempted' from 'src.ingestion.service'
```

## Why each failure is the right one

- The import of the test module itself succeeds (collection found 9 items), so there is no typo or fixture error; the only missing name is the new feature, imported lazily inside `_ingest_checkpoint`.
- All fixture and setup code ran before the failing line, including the lesson/quest setup of the unchanged-state test (its pre-conditions `xp_total > 0`, `new_streak == 1`, quest progress 1 passed).
- Limitation: because every test dies on the missing ingest function, nothing here yet proves the later assertions (the schema `reason`, storage shape, ordering) are satisfiable. They will each be exercised as the implementation lands.

## Notes for the implementation step

- `src/schemas/events/checkpoint_attempted.schema.json` currently has no `passed` field and `additionalProperties: false`, so it must be updated (add required boolean `passed`) or the valid-event test will be rejected.
- The existing `lesson_completed` path rejects `event_type != learning.lesson_completed` as `untrusted_producer`; `ALLOWED_EVENT_TYPES["learning-svc"]` and `SUPPORTED_SCHEMA_VERSIONS` need the new type.
- Strict boolean: Draft-07 `{"type": "boolean"}` already rejects `1` and `"true"` in `jsonschema`; not verified by running.

## Unsure

- Whether `first_checkpoint_attempt_ids` as a plain dict is the shape you want exposed, or a method.
- Whether the tie-break test (extra) should stay.
- The same-`attempt_id`-different-content, dedup_key, late-window and untrusted-producer behaviours for checkpoints are not tested yet; the task listed only four groups.

---

# Prompt 3 — implementation

## Changes made (4 source files; no test file changed)

| File | Change |
|---|---|
| `src/schemas/events/checkpoint_attempted.schema.json` | Added required boolean `passed`. The file already existed (see §3.1) and is already packaged by the `schemas/events/*.json` package-data glob, same as `lesson_completed.schema.json`. Draft 7, `additionalProperties: false` unchanged. |
| `src/ingestion/validation.py` | `SUPPORTED_SCHEMA_VERSIONS` and `ALLOWED_EVENT_TYPES["learning-svc"]` gain `learning.checkpoint_attempted`. New `checkpoint_attempted_validator()` / `validate_checkpoint_attempted_payload()` mirroring the lesson pair. `validate_lesson_completed_envelope` is reused unchanged (timestamps and IANA timezone). |
| `src/ingestion/service.py` | The body of `ingest_lesson_completed` became a shared private `_ingest(spec, ...)`. New `_EventSpec` (frozen dataclass) holds what differs per type. `ingest_lesson_completed` and the new `ingest_checkpoint_attempted` are thin wrappers. |
| `src/state_machine/learner.py` | New state `checkpoint_attempts`, `checkpoint_sources`, `first_checkpoint_attempt_ids`; new `record_checkpoint_attempt`, `@event("CheckpointAttempted") _record_checkpoint_attempt`, `_attempt_order`. |

`git diff --stat`: service.py 181 lines changed (+/-), validation.py 18, schema 5, learner.py 72 (+228 / -48 total).

## Before / after of each changed function

**`ingest_lesson_completed` (service.py)**
- Before: ~280-line function with the lesson specifics inline (`completion_id` key, `learning.lesson_completed` literal, `validate_lesson_completed_payload`, two copies of a `learner.complete_lesson(...)` call, `completed_lesson_ids`/`completion_sources`, reason `duplicate_completion_id`).
- After: 5-line wrapper `return _ingest(LESSON_COMPLETED, ...)`. Same signature, same results. The only edits inside the pipeline are replacing those lesson specifics with `spec.*` lookups; step order, evidence rows, binding, dedup_key check, `config.clock` receive time, 72 h window and `IntegrityError` retry are untouched and shared.

**`_action_for_event_id` (service.py)**
- Before: `(learner, event_id)`, scanned `learner.completion_sources`.
- After: `(spec, learner, event_id)`, scans `spec.sources(learner)` and returns `spec.action_prefix + id`. Identical for lessons (prefix empty). Private; no outside callers (grep).

**`_ingest` pipeline specifics now taken from the spec:** event type literal (`spec.event_type`), payload id field (`spec.id_field`), payload validator, `sources`, `is_accepted`, `accept`, `duplicate_reason`, `action_prefix`.

**`Learner` (learner.py):** `__init__` gains 3 attributes; `record_checkpoint_attempt` returns False if `attempt_id` already stored, else emits one `CheckpointAttempted` and returns True. It never calls `_award_xp`, `_update_streak`, quest or badge code. The apply method stores the record, source event id, and maintains the first attempt per assessment as the minimum of `(occurred_at as UTC datetime, attempt_id)`; it only folds, so replay reproduces it.

**`validation.py`:** only additions plus the two dict entries; no existing function body changed.

## Decisions to review

1. **Same `attempt_id`, different content** (what I chose): mirrors `complete_lesson`. `record_checkpoint_attempt` returns False on any repeat of `attempt_id` and compares no content, so the first accepted content stays. Ingestion with a new `event_id` answers `duplicate / duplicate_attempt_id` (lesson: `duplicate_completion_id`) and writes a duplicate evidence row. Same `event_id` with changed content is rejected `conflicting_event_id_reuse` by the shared digest check, exactly as for lessons (inherited, not retested for checkpoints).
2. **`duplicate_attempt_id`**: a new reason string; lesson keeps `duplicate_completion_id`.
3. **Namespaced action id in the evidence tables:** `accepted_deliveries`, `delivery_ids.action_id` and evidence rows are keyed by one id per learner. A checkpoint `attempt_id` equal to a lesson `completion_id` would have collided and been dropped as a duplicate, so checkpoints store `checkpoint_attempt:<attempt_id>` there; the aggregate keeps the raw `attempt_id`. Lesson ids are unprefixed, so existing rows and tests are unaffected. Verified by probe below.
4. `dedup_key` is still `<event_id>:<rule_version>` using `config.rule_version`, though a checkpoint attempt applies no rule set (it is stored in `delivery_ids.rule_version`/`rule_digest` like any acceptance). Left as-is per "envelope rules identical".
5. `trace_delivery` is lesson-specific: for a checkpoint delivery it returns a trace with no lesson fact and no XP. Not changed.
6. `ingest_lesson_completed` name is retained; the module docstring still says "lesson-completion".

## Commands and output

Baseline before any source edit:

```
python -m pytest tests/ingestion tests/state_machine -q
........................................................................ [ 29%]
........................................................................ [ 59%]
........................................................................ [ 89%]
.........................                                                [100%]
241 passed in 3.34s
```

New tests:

```
python -m pytest tests/interventions/test_checkpoint_attempt_ingestion.py -v
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0 -- /home/sampadram/miniconda3/bin/python
cachedir: .pytest_cache
rootdir: /home/sampadram/Sampadram/IITH/SEM 7/DATGC/project/gamification-engine
configfile: pyproject.toml
plugins: anyio-4.12.1, asyncio-1.4.1
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 9 items

tests/interventions/test_checkpoint_attempt_ingestion.py::test_valid_checkpoint_attempt_is_accepted_and_stored_once PASSED [ 11%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_invalid_checkpoint_payload_is_rejected_without_state_change[missing_attempt_id-<lambda>] PASSED [ 22%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_invalid_checkpoint_payload_is_rejected_without_state_change[passed_is_integer_1-<lambda>] PASSED [ 33%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_invalid_checkpoint_payload_is_rejected_without_state_change[passed_is_string_true-<lambda>] PASSED [ 44%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_invalid_checkpoint_payload_is_rejected_without_state_change[assessment_version_0-<lambda>] PASSED [ 55%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_invalid_checkpoint_payload_is_rejected_without_state_change[unknown_extra_field-<lambda>] PASSED [ 66%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_accepting_an_attempt_leaves_xp_streak_and_quests_unchanged PASSED [ 77%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_first_attempt_per_assessment_ignores_arrival_order PASSED [ 88%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_first_attempt_ties_on_occurred_at_break_by_attempt_id PASSED [100%]

============================== 9 passed in 0.20s ===============================
...
.........                                                                [100%]
9 passed in 0.21s
```

(Verbose run printed the 9 test lines, each `PASSED`; output above is the same run, header and per-test lines.)

Existing folders after the change:

```
python -m pytest tests/ingestion tests/state_machine -q
........................................................................ [ 29%]
........................................................................ [ 59%]
........................................................................ [ 89%]
.........................                                                [100%]
241 passed in 3.27s
```

Extra check, whole suite (not asked for):

```
python -m pytest tests -q -x -p no:cacheprovider
667 passed, 2 skipped, 1 deselected in 23.53s
```

(The 2 skipped and 1 deselected come from existing markers/config; I did not investigate them.) The suite total includes the 9 new tests.

Throwaway probe (script kept outside the repo, `$SCRATCH/probe.py`; not a test) for decision 1 and 3:

```
first         ('accepted', None)
new id, diff  ('duplicate', 'duplicate_attempt_id')     # same attempt_id, different passed/assessment_id
lesson, same raw id ('accepted', None)                    # lesson completion_id == attempt_id, no collision
accept        ('accepted', None)
same evt, diff ('rejected', 'dedup_key_mismatch')         # invalid probe: my fixture gave the altered copy its own dedup_key
stored False checkpoint_math_v1                           # first content kept
duplicate duplicate_attempt_id checkpoint_attempt:same
rejected dedup_key_mismatch checkpoint_attempt:att-2
```

The "same evt, diff" line does not exercise `conflicting_event_id_reuse` (fixture mistake in the probe); that path is unchanged shared code and is not verified for checkpoints.

## Not tested / unsure

- No tests yet for: duplicate attempt_id via ingestion, checkpoint late window (72 h), dedup_key mismatch, untrusted producer for a checkpoint, event_id reuse across lesson and checkpoint, replay/rebuild of `first_checkpoint_attempt_ids` from stored events, or the lesson/checkpoint id-collision case. Behaviour is expected from shared code but unverified by committed tests.
- `src/state_machine/projection.py` (rebuild) was not inspected for whether it needs to know the new event; the aggregate itself replays through the `@event` fold.
- Pre-existing event stores contain no `CheckpointAttempted`; the new attributes are initialised in `__init__`, so old streams replay unchanged (not run against an old database).

---

# Prompt 4 — edge-case tests

## Changes made

- Appended 8 test functions to `tests/interventions/test_checkpoint_attempt_ingestion.py`; extended its helpers (`_ingest_checkpoint` takes an optional `config`; new `_outcome`, `_clocked`; imports `IngestConfig`, `rebuild_learner_state`).
- **Source fixes: none.** All 8 new tests passed on the first run, before any fix.

## Tests: result before any fix

| # | Test | Passed before fix? |
|---|---|---|
| 1 | `test_same_event_id_delivered_twice_is_a_duplicate_event` — `duplicate / duplicate_event_id`; one attempt, one event | Yes |
| 2 | `test_same_attempt_id_with_new_event_id_and_same_content_adds_no_event` — `duplicate / duplicate_attempt_id`, one `CheckpointAttempted`, evidence `original_ref` = first event_id, new event_id not bound | Yes |
| 3 | `test_same_attempt_id_with_new_event_id_and_changed_content_keeps_the_first` — passed flipped and assessment_id changed: duplicate, first content kept, one event, one duplicate evidence row (the 1C/Prompt-3 choice) | Yes |
| 4 | `test_received_more_than_72_hours_after_occurred_at_is_rejected` — 72h+1s: `rejected / outside_late_window`, evidence row with the clock's receive time, no events, no binding | Yes |
| 5 | `test_received_exactly_72_hours_after_occurred_at_is_accepted` | Yes |
| 6 | `test_wrong_dedup_key_rule_version_is_rejected` — key built with `v2`: `rejected / dedup_key_mismatch`, no events, no binding | Yes |
| 7 | `test_replay_rebuilds_attempts_and_first_attempt_per_assessment` — three attempts across two assessments ingested out of order; a freshly opened `LearnerApplication` on the same DB re-folds the stream; `checkpoint_attempts`, `first_checkpoint_attempt_ids`, `checkpoint_sources` identical | Yes |
| 8 | `test_replay_with_lessons_and_attempts_keeps_xp_streak_and_attempts` — two lessons on consecutive days plus one attempt; after reopening, XP, streak (2), lesson count, attempts and first ids identical; `rebuild_learner_state` agrees on XP and streak | Yes |

Tests 4 and 5 use `IngestConfig(v1, clock=...)` with the default 72 h window (not the unbounded config).

## Fixes made

None needed.

## Commands and output

```
python -m pytest tests/interventions/test_checkpoint_attempt_ingestion.py -v
```
```
tests/interventions/test_checkpoint_attempt_ingestion.py::test_valid_checkpoint_attempt_is_accepted_and_stored_once PASSED [  5%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_invalid_checkpoint_payload_is_rejected_without_state_change[missing_attempt_id-<lambda>] PASSED [ 11%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_invalid_checkpoint_payload_is_rejected_without_state_change[passed_is_integer_1-<lambda>] PASSED [ 17%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_invalid_checkpoint_payload_is_rejected_without_state_change[passed_is_string_true-<lambda>] PASSED [ 23%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_invalid_checkpoint_payload_is_rejected_without_state_change[assessment_version_0-<lambda>] PASSED [ 29%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_invalid_checkpoint_payload_is_rejected_without_state_change[unknown_extra_field-<lambda>] PASSED [ 35%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_accepting_an_attempt_leaves_xp_streak_and_quests_unchanged PASSED [ 41%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_first_attempt_per_assessment_ignores_arrival_order PASSED [ 47%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_first_attempt_ties_on_occurred_at_break_by_attempt_id PASSED [ 52%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_same_event_id_delivered_twice_is_a_duplicate_event PASSED [ 58%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_same_attempt_id_with_new_event_id_and_same_content_adds_no_event PASSED [ 64%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_same_attempt_id_with_new_event_id_and_changed_content_keeps_the_first PASSED [ 70%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_received_more_than_72_hours_after_occurred_at_is_rejected PASSED [ 76%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_received_exactly_72_hours_after_occurred_at_is_accepted PASSED [ 82%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_wrong_dedup_key_rule_version_is_rejected PASSED [ 88%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_replay_rebuilds_attempts_and_first_attempt_per_assessment PASSED [ 94%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_replay_with_lessons_and_attempts_keeps_xp_streak_and_attempts PASSED [100%]
============================== 17 passed in 0.32s ==============================
```
```
python -m pytest tests/ingestion tests/state_machine -q
........................................................................ [ 29%]
........................................................................ [ 59%]
........................................................................ [ 89%]
.........................                                                [100%]
241 passed in 3.23s
```
```
python -m pytest tests -q -p no:cacheprovider
........................................................................ [ 95%]
.............................                                            [100%]
675 passed, 2 skipped, 1 deselected in 23.26s
```

## Caveats

- Because nothing failed, these tests confirm the earlier design but did not find a bug. I did not mutation-test them (e.g. by breaking the source and watching them fail), so I cannot say each assertion would catch a regression, except that the ordering logic is also covered by the Prompt 2 tests.
- "Rebuild from the event store" in 7 and 8 means re-folding the stream into a new `Learner` aggregate through a fresh application on the same database. `rebuild_learner_state` (pure projection) is only used in test 8 for XP and streak; it does not carry attempts, and `SQLiteLearnerProjection` was not exercised.
- Still untested for checkpoints: untrusted producer, event_id reuse across lesson/checkpoint, lesson/checkpoint id collision, clock-less (default real clock) behaviour, a checkpoint with a past-72h duplicate attempt_id (late window is checked before the duplicate_attempt_id check, as for lessons).

---

# Prompt 5 — finish Step 1

## Final test runs

Counts before Step 1 (derived): 658 passed + 2 skipped + 1 deselected (the full-suite total of 667 seen after Prompt 3 minus the 9 tests added in Prompt 2). Nothing that passed before Step 1 fails now.

```
python -m pytest -q
........................................................................ [ 10%]
........................................................................ [ 21%]
.......................................................s........s....... [ 31%]
........................................................................ [ 42%]
........................................................................ [ 53%]
........................................................................ [ 63%]
........................................................................ [ 74%]
........................................................................ [ 85%]
........................................................................ [ 95%]
.............................                                            [100%]
675 passed, 2 skipped, 1 deselected in 23.23s
```
```
python -m pytest -m integration -q
.                                                                        [100%]
1 passed, 677 deselected in 4.57s
```

Notes: `pyproject.toml` sets `addopts = "-m 'not integration'"`, so the plain run deselects the one integration test (`tests/integration/test_full_state_rebuild.py`), and `-m integration` runs only it. Between them every collected test ran: 675 + 1 + 2 skipped = 678 collected. The two skips are existing (`test_assignment_vs_exposure.py:7` and `test_deterministic_assignment.py:71`, both "exposure ... not implemented/outside spike").

## Docs

- `docs/event-contracts.md`: added a changelog line under the title (dated 2026-11-02; the file had no changelog before) and a new section "Checkpoint Attempt (`learning.checkpoint_attempted`), v1" with payload table, producer/idempotency rules, conflicting-content behaviour, first-attempt-per-assessment rule, the no XP / no streak / no quest note, and one JSON example.
- The example was loaded from the doc and run through `ingest_checkpoint_attempted` with a real learner id substituted for `subject_id` and a fixed clock: result `accepted`. It uses `provenance.source = "client"` and a UUID `subject_id` because ingestion rejects `server_authoritative` (`untrusted_producer`) and requires a UUID learner id.
- Not edited, as instructed: the traceability matrix and README.

## Summary of Step 1

- Prompt 1: read-only audit of the `lesson_completed` flow.
- Prompt 2: 9 failing tests (all `ImportError` for the missing `ingest_checkpoint_attempted`).
- Prompt 3: implementation. Schema gained `passed`; validation allow-list and validator; `ingest_lesson_completed` body became a shared `_ingest(spec, ...)` with `ingest_checkpoint_attempted` as a second wrapper; `Learner.record_checkpoint_attempt`, `CheckpointAttempted` event, `checkpoint_attempts`, `checkpoint_sources`, `first_checkpoint_attempt_ids`. No XP, streak or quest effect.
- Prompt 4: 8 edge-case tests; all passed with no fix needed.
- Prompt 5: full-suite and integration runs, docs section, this summary.
- Source files changed in Step 1: `src/ingestion/service.py`, `src/ingestion/validation.py`, `src/schemas/events/checkpoint_attempted.schema.json`, `src/state_machine/learner.py`. New: `tests/interventions/` (`__init__.py`, one test file with 17 test cases), this audit file. Docs: `docs/event-contracts.md`.

## Final counts

- New test file: 17 passed.
- `tests/ingestion` + `tests/state_machine`: 241 passed (same before and after).
- Full suite: 675 passed, 2 skipped, 1 deselected; integration: 1 passed.

## Open questions

1. Duplicate reason name: `duplicate_attempt_id` is my choice; confirm or rename.
2. Conflicting content under the same `attempt_id` is silently ignored (same as lessons). Should M4 flag it as a conflict instead? That would change lessons too if done in the shared path.
3. The `checkpoint_attempt:` prefix in the evidence tables: acceptable, or should lesson ids also be namespaced later?
4. Which producer should send checkpoint attempts? It is `learning-svc` now; the envelope schema names `assessment-svc` as an example producer.
5. `checkpoint_completed` (with `passed` and `score`) has a schema but no ingestion; `passed` now lives on the attempt event as you specified. Is `checkpoint_completed` still planned?
6. `dedup_key` still carries a rule version for an event that applies no rule set.
7. `first_checkpoint_attempt_ids` is a plain dict attribute, not yet read by any policy or by the pure replay projection (`LearnerReplayState`, `SQLiteLearnerProjection`).

## Unsure about / not done

- The new tests were not mutation-tested (see Prompt 4 caveat).
- Not covered by tests: untrusted producer and `event_id` reuse across lesson/checkpoint, and a lesson `completion_id` equal to an `attempt_id`. The collision case was only checked by a throwaway probe in Prompt 3.
- `trace_delivery` returns no checkpoint fact for a checkpoint delivery.
- Pre-existing doc issue spotted, not fixed: the `learning.lesson_completed` example in `docs/event-contracts.md` uses `provenance.source = "server_authoritative"` and `subject_id = "L1"`, which ingestion would reject (`untrusted_producer` / not a UUID). Also lines 25-27 of that doc (rule version "currently `v1`", "seven-day" window) are stale versus the code.
- `docs/event-contracts.md` line wrapping/style follows the lesson section; no markdown linter was run.
- Nothing is committed; `git status` shows 4 modified source files, the edited doc, the new `tests/interventions/` folder and this audit file.

---

# Prompt 6 — collision tests

## Changes made

- Appended 2 tests to `tests/interventions/test_checkpoint_attempt_ingestion.py`. No source file changed; no fix was needed (both passed on the first run).

## Tests

1. `test_lesson_completion_id_and_checkpoint_attempt_id_do_not_collide` — lesson `completion_id` `same-123`, then checkpoint `attempt_id` `same-123`, different `event_id`s. Both `accepted`. The learner has `completed_lesson_count == 1`, `completed_lesson_ids == {"same-123"}` and one checkpoint attempt `same-123`. No duplicate/rejected evidence rows exist (accepted deliveries produce none), so the "evidence rows" are the delivery tables: the lesson's binding has `action_id == "same-123"`, the checkpoint's has `"checkpoint_attempt:same-123"`, and `accepted_deliveries` (via `original_event_id`) maps each id to its own event_id. Result: **passed**.
2. `test_event_id_accepted_for_a_lesson_cannot_be_reused_for_a_checkpoint` — lesson `cmp-1` accepted with event_id E; a checkpoint attempt `att-1` with the same E (with a valid `dedup_key` for E) is not accepted. Result: **passed**, asserting the current outcome:
   - `IngestionResult("rejected", "conflicting_event_id_reuse")`
   - one evidence row: `rejected`, reason `conflicting_event_id_reuse`, `action_id` `checkpoint_attempt:att-1`, `event_id` E, `original_ref` `cmp-1` (the lesson's action id)
   - event stream unchanged, no checkpoint attempt stored, the lesson's binding unchanged.

**Reason the current code gives for cross-type event_id reuse: `conflicting_event_id_reuse`.** It comes from the shared `_resolve_bound_event_id`: E is already bound in `delivery_ids` to action `cmp-1` (and a different digest), which differs from `checkpoint_attempt:att-1`. It is not caught earlier by the `event_type` or schema checks, because the checkpoint envelope is itself valid.

## Commands and output

```
python -m pytest tests/interventions/test_checkpoint_attempt_ingestion.py -v
collecting ... collected 19 items
tests/interventions/test_checkpoint_attempt_ingestion.py::test_valid_checkpoint_attempt_is_accepted_and_stored_once PASSED [  5%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_invalid_checkpoint_payload_is_rejected_without_state_change[missing_attempt_id-<lambda>] PASSED [ 10%]
tests/interventions/test_checkpoint_attempt_ingestion.py::test_invalid_checkpoint_payload_is_rejected_without_state_change[passed_is_integer_1-<lambda>] PASSED [ 15%]
```
```
python -m pytest -q
........................................................................ [ 95%]
...............................                                          [100%]
677 passed, 2 skipped, 1 deselected in 22.72s
```

## Results

- New file: 19 passed (17 before + 2).
- Full suite: 677 passed, 2 skipped, 1 deselected (675 before + 2). The deselected test is the integration test, not rerun this prompt (Prompt 5: 1 passed).
- Closes two items in the Prompt 5 "Not covered by tests" list (id collision, cross-type event_id reuse); this time as committed tests, not a probe. The collision test depends on the `checkpoint_attempt:` prefix: it would fail if the prefix were removed.

## Still unsure / untested

- Cross-type reuse in the opposite direction (checkpoint accepted first, then a lesson with the same event_id) is not tested; expected to be the same shared path.
- Untrusted producer for a checkpoint is still untested. No mutation testing of the new tests was done.
