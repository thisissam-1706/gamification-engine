# Week 6 / M4 / Step 5 — doc fixes and ADR-0007

Status: **Prompt 3 of Step 5 — docs only; no code or test changed.**
Git rule in force: no state-changing git commands (only `git status`/`git diff` were used).

## 1. Changes made

- `docs/event-contracts.md`: lesson_completed example fixed, two inaccurate statements corrected, one note about `checkpoint_completed.schema.json`, changelog line.
- `docs/adr/0007-rule-based-remediation-policy.md`: new ADR.
- `README.md`: the ADR-0007 link updated to the real file name and title, plus a changelog line.
- Not touched: any source or test file, `docs/traceability-matrix.md`, the schema file.

## 2. Lesson example (`event-contracts.md`)

Verification script (kept in the scratchpad, not in the repo): it extracts the first ```json block of the lesson_completed section from the doc itself,
creates a real `Learner`, substitutes **only** `subject_id` with that learner's id (the doc id must name an existing learner, as in the Step 1 checkpoint check),
and calls `ingest_lesson_completed` with `IngestConfig(DEFAULT_INGEST_RULE_VERSION, clock=lambda: 2026-10-03T09:15:30Z)` (30 s after `occurred_at`; default 72 h window).

Before the edit:
```
subject_id in doc: L1 | provenance.source in doc: server_authoritative
result: rejected untrusted_producer          (with the learner id substituted)
result: rejected untrusted_producer          (doc as is)
```
Reason (from `validation.py`): `validate_producer` rejects `provenance.source == "server_authoritative"` as `untrusted_producer`; `subject_id` "L1" is also not a UUID (`UUID(learner_id)` would fail → `schema_invalid`),
but the provenance check fires first, so the as-is run shows `untrusted_producer`.

After the edit (`subject_id` `5b0d6c6e-2f4a-4a53-9a57-3c1f7e2d9b10`, same as the checkpoint example; `provenance.source` `client`):
```
subject_id in doc: 5b0d6c6e-2f4a-4a53-9a57-3c1f7e2d9b10 | provenance.source in doc: client
result: accepted None
xp_total: 100 | completed: ['cmp_492_003']
```
Not verified: that the literal doc `subject_id` works unchanged. It is a valid UUID, but no learner with that id exists, so a real call would need a stored learner with that id. Added one sentence under the example saying `subject_id` must be the UUID of an existing learner and `provenance.source` must not be `server_authoritative`.

Before → after of the example: `"subject_id": "L1"` → `"5b0d6c6e-2f4a-4a53-9a57-3c1f7e2d9b10"`; `"source": "server_authoritative"` → `"source": "client"`.

## 3. "Producer and idempotency rules": the two claims checked

1. **"Ingestion currently expects rule version `v1` and rejects any other value with `dedup_key_mismatch`."** Code (`_ingest`, `service.py`): the expected key is `<event_id>:<key_version>` where
   `key_version` is the rule version the event was already bound to if it was accepted before, else `config.rule_version`. `IngestConfig.rule_version` has no default of its own, but `DEFAULT_CONFIG` uses `DEFAULT_INGEST_RULE_VERSION = "v1"`.
   So "v1" is only the default configuration, and a redelivery is judged against its accepted version. The statement was true for the default config only.
   - before: `Ingestion currently expects rule version `v1` and rejects any other value with `dedup_key_mismatch`.`
   - after:  `Ingestion expects the configured rule version (`IngestConfig.rule_version`, `v1` by default) and rejects any other value with `dedup_key_mismatch`; a redelivery of an already-accepted `event_id` is checked against the version it was accepted under (see "Effective rule version").`
2. **"The M1 ingestion deduplication window silently drops matching deliveries during its configured seven-day period."** Code: **there is no seven-day (or any) deduplication window**. `grep` over `src/ingestion` finds no such setting; the only time-based setting is `late_window` (72 h, about lateness, not dedup).
   An `event_id` stays bound to its first delivery (`delivery_binding`), a repeat is answered `duplicate` and an evidence row is written (not silently dropped), regardless of age; `tests/ingestion/test_dedup.py` exercises a resend after the "window" and the traceability matrix (AT-1) already says "regardless of elapsed time".
   - before: `The M1 ingestion deduplication window silently drops matching deliveries during its configured seven-day period.`
   - after:  `There is no time-limited deduplication window: an `event_id` stays bound to its first delivery, and a repeat delivery is recorded as a duplicate (an evidence row, not silently dropped) however old it is.`
3. Two nearby phrases referred to the same non-existent window and were corrected for consistency:
   - intro: `... provenance, and short-window ingestion deduplication.` → `... provenance, and ingestion deduplication.`
   - `The aggregate must also retain the completion/reward relationship beyond the ingestion window.` → `... relationship, independently of delivery-level deduplication.`
   Left unchanged: the example "a resend after eight days", which is still true.

**Not fixed (outside this prompt's scope, found while checking):** the docstring of `tests/ingestion/test_dedup.py` and its test names still say "7-day ingestion deduplication window"; the code has none. I did not edit tests. Anything else that repeats "seven-day" was not searched beyond `event-contracts.md`
(a `grep -rn "seven\|7-day"` over `docs`/`README.md`/`src` found no other prose hit, but the grep `--include` flags failed in zsh for part of it, so that search is not conclusive).

## 4. `checkpoint_completed.schema.json` — every reference

Search: `grep -rn "checkpoint_completed" . -I` (excluding `.git`, caches, `__pycache__`, `.egg-info`). Result:
- Only `docs/audit/week6-m4-step1-checkpoint-event.md` (lines 30, 231, 233, 236, 246, 624) — my own earlier audit notes. **No reference in `src/`, `tests/` (including `tests/packaging/*`), `pyproject.toml` content by name, README or other docs.**
- The file itself is `src/schemas/events/checkpoint_completed.schema.json` (payload: `attempt_id`, `assessment_id`, `assessment_version`, `passed`, `score`).
- It **is packaged**: `pyproject.toml` `[tool.setuptools.package-data] "src" = ["schemas/*.json", "schemas/events/*.json"]` is a glob, so the wheel includes it. `tests/packaging/test_wheel_contents.py` lists only `envelope.schema.json`, `lesson_completed.schema.json` and `rulesets.json`; `test_wheel_install.py` does not mention it. Deleting it would not break those tests, but it was kept as instructed.
- Ingestion cannot accept it: `ALLOWED_EVENT_TYPES["learning-svc"]` and `SUPPORTED_SCHEMA_VERSIONS` list only `learning.lesson_completed` and `learning.checkpoint_attempted`.

Added to the Checkpoint Attempt section, after the payload-contract link:
`src/schemas/events/checkpoint_completed.schema.json` (a payload with `passed` and `score`) is not ingested: no producer may send that event type and no code reads the file. `passed` is carried on `learning.checkpoint_attempted` instead. The file is kept for now.
(`score` is not carried anywhere; the note says only `passed` is.)

## 5. ADR-0007

New file `docs/adr/0007-rule-based-remediation-policy.md`, same format as ADR-0004/0006 (title `# 0007 - …`, **Status**, **Date**, **Context**, **Decision**, **Consequences** with *Positive*/*Negative*, **Alternatives Considered**).
Statement-by-statement checks:
- rule order and reasons — `decide_remediation` (`policy.py`). ✔
- first attempt by `(occurred_at, attempt_id)` — `Learner._record_checkpoint_attempt`. ✔
- decisions incl. `no_action` stored as `RemediationDecided`, id `remediation:<attempt_id>`, returned as stored on re-run — `run_remediation`, tests in `test_remediation_service.py`. ✔
- 14-day learner-wide window from the last assign, exactly 14 days outside, `no_action` never blocks; 7-day deadline — `policy.py`, `RemediationConfig`, policy tests. ✔ (the policy takes any prior `assign`, which is equivalent to the last one.)
- quest `remediation_quest:<attempt_id>`, same save, nothing saved on failure, remediation-only completions — `service.py`, `quest_definitions.py`, end-to-end tests. ✔
- registry `remediation_quests_v1`, target 1, rule-set quests unchanged — `quest_definitions.py`. ✔
- sweep `deadline_at < as_of`, `expired_at = deadline_at` on both paths, idempotent, all quests — `learner.py`, expiry tests. ✔
- "nothing in `src` calls `run_remediation` or `run_quest_expiry` (only tests do)" — grep over `src` outside `src/interventions/service.py` returned nothing. ✔
- "no notification hook" — no import of `src.interventions` in `src/notifications`. ✔
- "M7 metric does not read checkpoint events" — `first_eligible_checkpoint_mastery` / `mastery_report` take caller-supplied attempt/completion records; the only callers in `src` are in `src/scale_and_recovery/experiment_repro.py` with its own records; nothing reads `Learner.checkpoint_attempts`. ✔
- "assign decisions stored before the quest was wired in are not backfilled" — true of the code. In practice this repo has no persisted production data; stated as a code fact.
- "no exposure logging" (reason for rejecting a bandit) — the matrix says "exposure logging not implemented". ✔
- Date `2026-11-02` is today's date as given to me.
Bandit reference: ADR-0004 (fixed 50/50, bandit later); ADR-0006 for immutable rule sets and decide-once.

## 6. README

- ADR list: before `- [ADR-0007: Remediation Policy with Stored Decisions and a Quest Definition](docs/adr/0007-remediation-policy-and-quest.md)` → after `- [ADR-0007: Rule-Based Remediation Policy with Stored Decisions](docs/adr/0007-rule-based-remediation-policy.md)` (the previous link pointed at a file name I had guessed; now it resolves).
- Changelog line `> **Changelog (2026-11-02):** Added ADR-0007 (rule-based remediation policy) to the ADR list.` above the 2026-10-12 one.
- Still stale and still not changed: the README sentence "(currently only `lesson_completed` is ingested)" (flagged in the previous prompt).

## 7. Changelog lines

- `docs/event-contracts.md`: new top line dated 2026-11-02 (example fix, two statement corrections, `checkpoint_completed` note). The existing M4 changelog lines were kept.
- `README.md`: one new line (above).
- `docs/adr/0007-…`: new file, dated in its header (ADRs carry a date rather than a changelog; ADR-0004 has one only because it was amended).

## 8. Commands and output

```
(script) check_example.py substitute      before: rejected untrusted_producer
(script) check_example.py substitute      after:  accepted None; xp_total: 100
$ python -m pytest -q
765 passed, 2 skipped, 1 deselected in 23.72s
$ python -m pytest -m integration -q
1 passed, 767 deselected in 4.61s
```
(Docs only; the suite was re-run to confirm nothing depends on the edited text.) Mermaid and the other points from the previous prompt are unchanged.

## 9. Unsure / open

- Whether the example's `subject_id` should be a fixed UUID with a note, or a placeholder like `<learner uuid>`; I used a fixed UUID to keep the example valid JSON and mirror the checkpoint example.
- ADR numbering/title: you named the file `0007-rule-based-remediation-policy.md`; I used the title "Rule-Based Remediation Policy with Stored Decisions".
- The ADR's "Negative" list mentions untuned defaults and the unbackfilled decisions; delete anything you consider too much for an ADR.
- `tests/ingestion/test_dedup.py` still describes a seven-day window (see §3); the code and now the docs say there is none.
- The "providers may not send `learning.checkpoint_completed`" claim rests on `ALLOWED_EVENT_TYPES`; a `gamification-engine` producer could be added to it later.
