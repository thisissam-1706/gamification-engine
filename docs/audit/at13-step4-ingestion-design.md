# AT-13 step 4 of 8: `learning.surface_viewed` ingestion — design only

No source or test file was changed. The only file written is this one. No state-changing git command was run.
The proposed diff below was checked with `patch --dry-run -p1` against the current tree (it applies cleanly) and the patched copies were syntax-checked. It was built on copies in the scratchpad, not in the repo.

## 1. What I found (answers to the six questions)

### Q1. What does `_ingest` do when `spec.accept` returns False?
`src/ingestion/service.py:381-394`:
```
381    quest_already_complete = _quest_already_complete(learner, assignment_id)
382    accepted = spec.accept(learner, raw_id, envelope, config)
383    if not accepted:
384        original = spec.sources(learner).get(raw_id)
385        evidence.record("duplicate", learner_id, spec.duplicate_reason, action_id=..., event_id=..., original_ref=original, received_at=...)
394        return IngestionResult("duplicate", spec.duplicate_reason)
```
The caller sees `IngestionResult(status="duplicate", reason=spec.duplicate_reason)`, so `("duplicate", "duplicate_view_id")` for the new type. One `duplicate` evidence row is written (`original_ref` = the source event id of the first acceptance, or None). Nothing is saved. The same shape is repeated after an `IntegrityError` retry at `service.py:428-442` (it also calls `remember_delivery` first, line 432).
Normally a duplicate `view_id` never reaches `accept`: it is caught earlier at `service.py:364-379` (`_original_delivery` or `spec.sources(learner).get(raw_id)`). `accept` returning False is the fallback for the case where those lookups found nothing.
For the new type `accept` can only return False for a repeated `view_id` (`Learner.record_experiment_exposure` returns False only there; every other failure raises `ExposureRefused`), so "False means duplicate" stays correct.

### Q2. Catching `ExposureRefused` — smallest change, and reason strings
There are **two** `spec.accept` call sites, not one: `service.py:382` and the post-`IntegrityError` retry at `service.py:428`. Both need the same `try/except ExposureRefused`; the diff below adds both plus a 12-line helper `_record_refusal` so the two handlers are three lines each.
- It catches `ExposureRefused` only, not `ValueError`. `ExposureRefused` is a `ValueError` subclass, so a broad catch would also swallow other `ValueError`s from `accept` (for example `complete_lesson`'s `unknown completion_kind`, `learner.py:116`), which today propagate. Catching the specific class leaves `lesson_completed` and `checkpoint_attempted` unchanged: neither can raise it.
- The existing validation `except` blocks (`service.py:271-298`) wrap only the validation `try` (253-270); they do not cover `accept`, so `ExposureRefused` is not caught anywhere today and would propagate as an exception.
- `EvidenceStore` does **not** restrict reason strings. `ingestion_evidence.reason` is `TEXT NOT NULL` with no CHECK (`evidence.py:196`); only `kind` has a CHECK (`kind IN ('duplicate','rejected')`, `evidence.py:192`). The only unique index is a partial one on `kind = 'duplicate'` rows (`evidence.py:214-217`), so repeated `rejected` rows are all kept (not de-duplicated).
- The allowed-reason set at `service.py:272-278` does **not** need extending. It only maps `ValidationError` messages from the validation block (anything else becomes `schema_invalid`); refusal reasons come from `accept`, a different path.
- A refusal leaves nothing behind: `record_experiment_exposure` checks before it emits (`learner.py`, step 2), so the loaded aggregate has no pending events and `application.save` is never called. The refusal also does not call `remember_delivery` / `bind_delivery` (those are at `service.py:455-473`, after the save). Consequence: the `event_id` stays unbound, so the same event redelivered after the learner gets an assignment is evaluated again and can be accepted. This matches the step 3 test "late assignment does not accept an earlier refused exposure", at the ingestion level.

### Q3. Unknown `subject_id`: does `_ingest` create or save a Learner?
No. `service.py:340-343`:
```
340    try:
341        learner = application.repository.get(learner_uuid)
342    except AggregateNotFoundError as exc:
343        raise RuntimeError("learner aggregate must exist before ingestion") from exc
```
`_ingest` only ever calls `repository.get`; the only `application.save` calls are `service.py:396` and `444`, and both come after `accept`. This runs before `accept`, so a rejected exposure cannot create or save a learner.
**But note the behaviour:** for a `subject_id` that is a valid UUID with no aggregate, the caller gets a `RuntimeError`, not a `rejected` evidence row (no evidence row is written either). A non-UUID `subject_id` is rejected earlier: `UUID(learner_id)` at line 270 raises `ValueError`, caught at 288, giving `rejected / schema_invalid`. Existing lesson and checkpoint ingestion behave the same way. So "a rejected exposure for an unknown learner must NOT create or save one" holds today, but "rejected" is not literally what the caller sees. If you want a `rejected` row with e.g. `unknown_learner`, that is a new behaviour change that would also apply to the other two event types; I have not proposed it. The `ExposureRefused("no_assignment")` path is different: it is for a learner that **exists** but has no assignment for that `exp_id`.

### Q4. Shared-path assumptions
- `validate_lesson_completed_envelope` reads `envelope["payload"]["timezone"]` for every event type and runs `ZoneInfo(...)` on it (`validation.py:95-98`); a missing key becomes `ValidationError("schema_invalid")`. Called unconditionally at `service.py:254`. **So the new payload must include `timezone`** (and a valid IANA name). The aggregate's exposure method does not use it; it is validated and then ignored.
- Other lesson-specific or shared assumptions:
  - `SUPPORTED_SCHEMA_VERSIONS.get(event_type)` (`validation.py:82-84`): an unlisted type skips the version check (`None`), leaving only the envelope enum `[1]` (`envelope.schema.json`). The new type should be added to be strict. Adding it is in the diff.
  - `ALLOWED_EVENT_TYPES["learning-svc"]` (`validation.py:21`) must list the new type or the event is rejected `untrusted_producer` (`service.py:259-263`). In the diff.
  - Late window (`service.py:327-338`) applies to every type: a `surface_viewed` whose `occurred_at` is more than the configured window (72 h by default) old is rejected `outside_late_window`. Same as checkpoints; flagging because it means a late exposure is dropped, not recorded.
  - `dedup_key`, `rule_version`, `rule_digest` (`service.py:310`, `470-471`) are applied to every type, as for checkpoints; exposure applies no rule set but still carries `<event_id>:<rule_version>`.
  - `assignment_id` (`service.py:245-250`) is read from the payload for any type; absent for the new type, so `quest_already_complete` is False. Harmless.
  - `src/ingestion/trace.py:77-102` builds `lesson_completed` facts only for lesson events; for other types it returns `None`. Not affected (no change proposed).
  - The helper's name (`validate_lesson_completed_envelope`) is misleading but not a behaviour problem; renaming is out of scope.

### Q5. Tests that enumerate schema files or event types
A search of `tests/` finds no test that lists every schema or every event type. What exists:
- `tests/packaging/test_wheel_contents.py:8-12`: `REQUIRED` lists three files (envelope schema, `lesson_completed.schema.json`, `rulesets.json`). It checks `REQUIRED <= names`, so adding a schema file does not break it. It does **not** require `checkpoint_attempted.schema.json` today. Updating is optional: add `src/schemas/events/surface_viewed.schema.json` to `REQUIRED` so a packaging mistake is caught (the glob `schemas/events/*.json` in `pyproject.toml:33` already includes it). The test runs `pip wheel`, so it is slow.
- No existing test needs to change for correctness. Tests that read single schema files by name (`tests/state_machine/test_xp_decay.py:395`, `tests/abuse/*`) are unaffected.
- New tests will be needed in step 5 (not written here): payload contract, accept/duplicate/refusal evidence rows, unknown-learner behaviour, no variant/assignment_ref accepted.
- Docs that list ingested types and will need updating later (not this step): `docs/event-contracts.md` (a new section like "Checkpoint Attempt", line 105), `docs/traceability-matrix.md:34`, `README.md:143,152`.
- Heads-up: `src/schemas/events/experiment_exposed.schema.json` already exists with **different** fields (`variant`, `assignment_ref` required). It describes the engine-derived exposure fact, not the source event. The new source schema must be a separate file, `surface_viewed.schema.json`, to avoid confusion. Like `checkpoint_attempted` before M4, nothing references `experiment_exposed.schema.json` in code.

## 2. The proposal

1. New payload schema `src/schemas/events/surface_viewed.schema.json` (Draft-07), required `view_id`, `exp_id`, `exposure_surface`, `timezone`; each `string`, `minLength 1`; `additionalProperties: false`, so `variant` and `assignment_ref` are rejected as `schema_invalid`. (A new file; not part of the requested diff, shown as text below.)
2. `validation.py`: add the type to `SUPPORTED_SCHEMA_VERSIONS` (v1) and to `learning-svc` in `ALLOWED_EVENT_TYPES`; add `surface_viewed_validator` / `validate_surface_viewed_payload`.
3. `service.py`: add `_accept_surface_viewed`, the `SURFACE_VIEWED` spec (`id_field="view_id"`, `action_prefix="surface_view:"`, `duplicate_reason="duplicate_view_id"`, `sources=learner.exposure_sources`, `is_accepted` = `view_id in learner.experiment_exposures`), `ingest_surface_viewed`, `_record_refusal`, and the two `except ExposureRefused` handlers.
4. `occurred_at` becomes `exposed_at` (parsed with `datetime.fromisoformat`, aware because the envelope validator already rejects naive timestamps at `validation.py:92`). The aggregate derives `variant` and `assignment_ref`.
5. The evidence table needs no change.

Schema file text (not applied):
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "SurfaceViewedPayload",
  "type": "object",
  "required": ["view_id", "exp_id", "exposure_surface", "timezone"],
  "properties": {
    "view_id": {"type": "string", "minLength": 1},
    "exp_id": {"type": "string", "minLength": 1},
    "exposure_surface": {"type": "string", "minLength": 1},
    "timezone": {"type": "string", "minLength": 1}
  },
  "additionalProperties": false
}
```

## 3. Proposed unified diff (text only, NOT applied)

```diff
--- a/src/ingestion/validation.py
+++ b/src/ingestion/validation.py
@@ -15,10 +15,15 @@
 SUPPORTED_SCHEMA_VERSIONS = {
     "learning.lesson_completed": {1},
     "learning.checkpoint_attempted": {1},
+    "learning.surface_viewed": {1},
 }
 
 ALLOWED_EVENT_TYPES = {
-    "learning-svc": {"learning.lesson_completed", "learning.checkpoint_attempted"},
+    "learning-svc": {
+        "learning.lesson_completed",
+        "learning.checkpoint_attempted",
+        "learning.surface_viewed",
+    },
     "gamification-engine": {
         "gamification.xp_awarded",
         "gamification.streak_updated",
@@ -76,6 +81,21 @@
     checkpoint_attempted_validator().validate(dict(payload))
 
 
+@lru_cache(maxsize=1)
+def surface_viewed_validator() -> Draft7Validator:
+    """Loads the checked-in v1 surface-viewed payload contract."""
+    text = (files("src") / "schemas" / "events" / "surface_viewed.schema.json").read_text(
+        encoding="utf-8"
+    )
+    schema: dict[str, Any] = json.loads(text)
+    return Draft7Validator(schema)
+
+
+def validate_surface_viewed_payload(payload: Mapping[str, Any]) -> None:
+    """Raises ``ValidationError`` when a v1 surface-viewed payload violates its contract."""
+    surface_viewed_validator().validate(dict(payload))
+
+
 def validate_lesson_completed_envelope(envelope: Mapping[str, Any]) -> None:
     """Validates lesson-completed envelope values not covered by JSON Schema."""
     event_type = envelope["event_type"]
--- a/src/ingestion/service.py
+++ b/src/ingestion/service.py
@@ -13,6 +13,7 @@
 from eventsourcing.application import AggregateNotFoundError
 from eventsourcing.persistence import IntegrityError
 
+from src.experiments.exposure import ExposureRefused
 from src.ingestion.config import DEFAULT_CONFIG, IngestConfig
 from src.ingestion.evidence import DeliveryBinding, EvidenceStore
 from src.ingestion.validation import (
@@ -21,6 +22,7 @@
     validate_lesson_completed_envelope,
     validate_lesson_completed_payload,
     validate_producer,
+    validate_surface_viewed_payload,
 )
 from src.state_machine.learner import Learner, LearnerApplication
 from src.state_machine.rules import get_ruleset
@@ -98,6 +100,22 @@
     )
 
 
+def _accept_surface_viewed(
+    learner: Learner, action_id: str, envelope: Mapping[str, Any], config: IngestConfig
+) -> bool:
+    payload = envelope["payload"]
+    # variant and assignment_ref are derived by the aggregate, never taken from the event.
+    return learner.record_experiment_exposure(
+        str(payload["exp_id"]),
+        str(payload["exposure_surface"]),
+        view_id=action_id,
+        exposed_at=datetime.fromisoformat(str(envelope["occurred_at"])),
+        source_event_id=str(envelope["event_id"]),
+        correlation_id=envelope["correlation_id"],
+        causation_id=envelope["causation_id"],
+    )
+
+
 LESSON_COMPLETED = _EventSpec(
     event_type="learning.lesson_completed",
     id_field="completion_id",
@@ -118,6 +136,16 @@
     is_accepted=lambda learner, action_id: action_id in learner.checkpoint_attempts,
     accept=_accept_checkpoint_attempt,
 )
+SURFACE_VIEWED = _EventSpec(
+    event_type="learning.surface_viewed",
+    id_field="view_id",
+    action_prefix="surface_view:",
+    duplicate_reason="duplicate_view_id",
+    validate_payload=validate_surface_viewed_payload,
+    sources=lambda learner: learner.exposure_sources,
+    is_accepted=lambda learner, action_id: action_id in learner.experiment_exposures,
+    accept=_accept_surface_viewed,
+)
 
 
 def _action_for_event_id(spec: _EventSpec, learner: Learner, event_id: str) -> str | None:
@@ -221,6 +249,40 @@
     )
 
 
+def ingest_surface_viewed(
+    application: LearnerApplication,
+    evidence: EvidenceStore,
+    envelope: Mapping[str, Any],
+    *,
+    authenticated_producer: str,
+    config: IngestConfig = DEFAULT_CONFIG,
+) -> IngestionResult:
+    """Validates and accepts one surface view or records its outcome."""
+    return _ingest(
+        SURFACE_VIEWED, application, evidence, envelope, authenticated_producer, config
+    )
+
+
+def _record_refusal(
+    evidence: EvidenceStore,
+    learner_id: str,
+    action_id: str,
+    event_id: str,
+    received_at: str,
+    reason: str,
+) -> IngestionResult:
+    """A rule refusal by the aggregate: nothing was saved, so only evidence is written."""
+    evidence.record(
+        "rejected",
+        learner_id,
+        reason,
+        action_id=action_id,
+        event_id=event_id,
+        received_at=received_at,
+    )
+    return IngestionResult("rejected", reason)
+
+
 def _ingest(
     spec: _EventSpec,
     application: LearnerApplication,
@@ -379,7 +441,12 @@
         return IngestionResult("duplicate", spec.duplicate_reason)
 
     quest_already_complete = _quest_already_complete(learner, assignment_id)
-    accepted = spec.accept(learner, raw_id, envelope, config)
+    try:
+        accepted = spec.accept(learner, raw_id, envelope, config)
+    except ExposureRefused as exc:
+        return _record_refusal(
+            evidence, learner_id, action_id, event_id, received_at, exc.reason
+        )
     if not accepted:
         original = spec.sources(learner).get(raw_id)
         evidence.record(
@@ -425,7 +492,12 @@
             return IngestionResult("duplicate", spec.duplicate_reason)
 
         quest_already_complete = _quest_already_complete(learner, assignment_id)
-        accepted = spec.accept(learner, raw_id, envelope, config)
+        try:
+            accepted = spec.accept(learner, raw_id, envelope, config)
+        except ExposureRefused as exc:
+            return _record_refusal(
+                evidence, learner_id, action_id, event_id, received_at, exc.reason
+            )
         if not accepted:
             original = spec.sources(learner).get(raw_id)
             if original is not None:
```

## 4. Assumptions
- `view_id` is unique per learner across experiments: `Learner.experiment_exposures` is keyed by `view_id` alone (step 2), and the delivery table is keyed by `(learner_id, "surface_view:"+view_id)`. The same `view_id` used for two different `exp_id`s is therefore treated as a duplicate. I assume producers make `view_id` globally unique.
- `exposed_at` = the envelope's `occurred_at` (source time), not the trusted `received_at`.
- `received_at` and the rejected evidence row use the same clock as other rejections.
- The diff keeps the public function name `ingest_surface_viewed`, parallel to `ingest_checkpoint_attempted`.

## 5. Unsure about
- Unknown learner gives a `RuntimeError`, not a rejected row (Q3). Needs your decision; I did not change it.
- A refused exposure leaves the `event_id` unbound, so redelivery is re-evaluated each time and produces another `rejected` row each time (no de-duplication of rejected rows). Probably fine, but it is unbounded evidence growth for a producer that retries a refused event.
- The 72 h late window drops late exposures, so a delayed view event is lost rather than recorded.
- The timezone field is required but unused by the aggregate; it exists only because of the shared validator. Dropping it would need a change to the shared `validate_lesson_completed_envelope`, which you did not ask for.
- I did not run the proposed code against any tests; only a dry-run apply and a syntax check were done.
