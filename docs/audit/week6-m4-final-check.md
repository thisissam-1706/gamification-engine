# Week 6 / M4 — final check

Status: **M4 Steps 1-5 complete. Final check done; no source code changed in this prompt.**
Git rule in force: no state-changing git commands were run (only `git status`, `git diff --stat`, `git ls-files --others`).

## 1. Final test run (exact output)

```
$ python -m pytest -q
........................................................................ [ 84%]
........................................................................ [ 93%]
...............................................                          [100%]
765 passed, 2 skipped, 1 deselected in 23.77s

$ python -m pytest -m integration -q
.                                                                        [100%]
1 passed, 767 deselected in 4.33s
```
(Run again after the two doc fixes in §2: `765 passed, 2 skipped, 1 deselected in 23.87s`; `1 passed, 767 deselected in 5.34s`.) Only the last progress rows of the full run are reproduced. Nothing failed.
The 2 skips and the 1 deselected test (the integration test, excluded by `addopts = "-m 'not integration'"`) were not investigated.

### Per-file counts, `tests/interventions` (107 tests, all passing)

| File | Step | Tests |
|---|---|---:|
| `test_checkpoint_attempt_ingestion.py` | 1 | 19 |
| `test_remediation_policy.py` | 2 | 18 |
| `test_remediation_service.py` | 2 | 15 |
| `test_remediation_quest.py` | 3 | 20 |
| `test_remediation_end_to_end.py` | 3 | 10 |
| `test_quest_expiry.py` | 4 | 22 |
| `test_learning_remediation.py` (AT-17) | 5 | 3 |

Full suite history: 710 (end of Step 2) → 740 (Step 3) → 757 (Step 4 sweep + lazy-expiry stamp) → 762 (Step 4 edge cases) → 765 (AT-17). Docs-only prompts did not change the count.

## 2. Consistency grep (README.md and docs/, excluding docs/audit/)

Commands: `grep -rn "M4" README.md docs --include='*.md' --exclude-dir=audit`, and
`grep -rnE "not started|AT-17 \(to be written\)|to be written|lesson_completed only|only .?lesson_completed|currently only" ...` (the first attempt with an unquoted `--include=*.md` failed in zsh; re-run quoted).

**"M4" occurrences (all current, none stale):** README.md lines 126 (milestone row), 139 (AT-17 row); `docs/traceability-matrix.md` lines 3 (changelog), 22 (definition), 35 (M4 row), 86 (AT-17); `docs/event-contracts.md` changelogs and section headings (M4); `docs/adr/0007-rule-based-remediation-policy.md` line 7.

**"not started"**: no occurrence left (the M4 matrix row's "not started" was replaced in the previous prompt).
**"AT-17 (to be written)"**: no occurrence left.
**"test to be written" near AT-17**: none. Remaining "to be written" hits belong to **AT-18 / M7** and were left alone:
- `docs/traceability-matrix.md:38` (M7 row: "AT-18 (to be written)") and `:87` (item 18 "(test to be written)").
**"lesson_completed only" / "currently only lesson_completed"**: one stale M1 statement, **fixed**:
- `README.md:132`: `(currently only `lesson_completed` is ingested)` → `(currently `lesson_completed` and `checkpoint_attempted` are ingested)`. README changelog line extended accordingly.

**Other stale M4 statement, fixed:** `docs/event-contracts.md:105` (Checkpoint Attempt overview):
- before: `The first attempt per assessment is exposed to later interventions (M4).`
- after:  `The first attempt per assessment is read by the M4 remediation policy (see below).`
- changelog line added at the top of that file.

**Listed, NOT changed (not stale M4/M1 statements):**
- `docs/traceability-matrix.md:38, 87`: AT-18 / M7 "to be written" — M7's, still true.
- README paragraph under the architecture diagram (starts "The implemented path runs in one process…") does not mention the intervention policy or the expiry sweep, though the diagram does. An omission, not a false statement; add a sentence if you want it.
- `docs/metrics-framework.md:17`: "Remediation Conversion Rate: the percentage of lapsed users who successfully complete a remediation quest and generate a valid learning event the following day" — an M7 metric definition that presumes a remediation quest for lapsed users; M4 assigns remediation after a failed first checkpoint attempt, not for lapsed users, and nothing computes that metric. Needs a peer-review decision.
- `docs/problem-boundaries.md:10`: "Interventions are delivered via push notifications" — M4 has no notification hook yet (stated in the matrix/README/ADR).
- `tests/ingestion/test_dedup.py` docstring still describes a "7-day ingestion deduplication window" (code has none; recorded in `week6-m4-step5-doc-fixes-adr.md`).
- The M1 matrix row's Acceptance tests column still lists only AT-1.
- The Mermaid diagram edit was never rendered (no `mmdc`); see `week6-m4-step5-docs-matrix-readme.md` §5.

## 3. Files from M4 Steps 1-5 (for you to commit)

Source of truth: `git status --short` and `git ls-files --others --exclude-standard`, at the end of this prompt. Nothing outside this list is modified or untracked
(ignored `__pycache__` aside). **Several files changed in more than one step**; each is listed under the step that first touched it, with later steps noted.
Steps 1's files were already modified/untracked when this work began (they appear in the session's initial `git status`).

### Modified (7)
| File | Steps |
|---|---|
| `src/ingestion/service.py` | 1 (`ingest_checkpoint_attempted`, shared `_ingest`) |
| `src/ingestion/validation.py` | 1 (checkpoint event type, schema validator) |
| `src/schemas/events/checkpoint_attempted.schema.json` | 1 |
| `src/state_machine/learner.py` | 1 (`record_checkpoint_attempt`, `checkpoint_attempts`), 2 (`record_remediation_decision`, `RemediationDecided`), 3 (quest definition lookup, completion-kind filter), 4 (`expire_due_quests`, deadline `expired_at`) |
| `docs/event-contracts.md` | 1 (checkpoint section), 2 (decision section), 3 (quest), 4 (expiry), 5 (example/statement fixes, `checkpoint_completed` note), final (overview sentence) |
| `docs/traceability-matrix.md` | 5 (M1, M4, AT-17, component table, changelog) |
| `README.md` | 5 (diagram, milestone rows, test table, ADR link), final (M1 sentence) |

### New, by step
**Step 1 — checkpoint ingestion**
- `tests/interventions/__init__.py`
- `tests/interventions/test_checkpoint_attempt_ingestion.py`
- `docs/audit/week6-m4-step1-checkpoint-event.md`

**Step 2 — remediation policy and decision storage**
- `src/interventions/__init__.py`
- `src/interventions/config.py`
- `src/interventions/policy.py`
- `src/interventions/service.py` (also extended in Step 3 `run_remediation` quest assignment and Step 4 `run_quest_expiry`)
- `tests/interventions/test_remediation_policy.py`
- `tests/interventions/test_remediation_service.py` (one test narrowed in Step 3; see that audit)
- `docs/audit/week6-m4-step2-remediation-policy.md`

**Step 3 — remediation quest**
- `src/state_machine/quest_definitions.py`
- `tests/interventions/test_remediation_quest.py`
- `tests/interventions/test_remediation_end_to_end.py`
- `docs/audit/week6-m4-step3-remediation-quest.md`

**Step 4 — expiry sweep**
- `tests/interventions/test_quest_expiry.py`
- `docs/audit/week6-m4-step4-quest-expiry.md`

**Step 5 — AT-17, docs, ADR**
- `tests/interventions/test_learning_remediation.py`
- `docs/adr/0007-rule-based-remediation-policy.md`
- `docs/audit/week6-m4-step5-at17-docs.md`
- `docs/audit/week6-m4-step5-docs-matrix-readme.md`
- `docs/audit/week6-m4-step5-doc-fixes-adr.md`
- `docs/audit/week6-m4-final-check.md` (this file)

Total: 7 modified, 22 new files (counting this audit file; `src/interventions/` and `tests/interventions/` are untracked directories).
Not part of M4 and not changed: `src/schemas/events/checkpoint_completed.schema.json`, rule sets and their digests, `src/abuse`, `src/notifications`, `src/leaderboard`, `src/experiments`.
Nothing was committed; I did not stage anything.

## 4. Overall M4 summary

### Implemented (synthetic fixtures only; tested)
- **Checkpoint ingestion:** `learning.checkpoint_attempted` v1 through the shared ingestion path (schema, producer check, dedup key, 72 h late window, event-id binding, `duplicate_attempt_id`); stored on the learner as `checkpoint_attempts` and `first_checkpoint_attempt_ids` (earliest by `(occurred_at, attempt_id)`, order-independent, rebuilt on replay). Awards no XP, no streak, no quest progress.
- **Remediation policy:** pure `decide_remediation` with fixed rule order; `RemediationDecided` facts for every outcome including `no_action`, idempotent on `remediation:<attempt_id>`, never recomputed on replay.
- **Remediation quest:** `remediation_v1` / `remediation_quests_v1` in `quest_definitions.py` (target 1, `completion_kind` `remediation` only), assigned in the same save as the `assign` decision; other quests unchanged; nothing saved if the assignment fails.
- **Expiry:** `Learner.expire_due_quests` (strict `deadline_at < as_of`, sorted, idempotent, all quests) and `run_quest_expiry`; lazy expiry on completion also records `expired_at = deadline_at`.
- **AT-17** passing; docs, matrix, README, event contracts and ADR-0007 updated.

### Invoked but not scheduled
- `run_remediation` (nothing calls it except tests): no checkpoint attempt automatically produces a decision.
- `run_quest_expiry` (nothing calls it except tests): quests expire only if someone runs the sweep or a later completion arrives.
- Neither iterates over learners; both take one `learner_id`.

### Not done
- No scheduler / trigger for either function; no loop over all learners.
- No notification hook: learners are not told about a remediation quest.
- The M7 mastery metric does not read the learner's checkpoint events; `checkpoint_completed` (with `score`) is not ingested.
- No backfill: `assign` decisions stored before the quest wiring (in any dev database) have no quest.
- No JSON schema for `RemediationDecided` or the other domain events' new fields beyond the existing `quest_expired` schema; `expired_at` is not exposed by any read model.
- Policy thresholds (14 days, 7 days, single quest) are untuned defaults, not validated on real data.
- No wheel test covers the new `src/interventions` package (the packaging tests list specific files; the package is included by `include = ["src*"]`, not tested).
- Mermaid diagram edit not rendered.

## 5. Open questions for the peer review

1. **Trigger design:** who calls `run_remediation` (per accepted attempt, after ingestion?) and `run_quest_expiry` (cron/startup)? Calling `run_remediation` right after ingestion is a design choice not yet made, and it interacts with out-of-order attempts: a later-arriving *earlier* attempt can become the first attempt after a decision on a later one (tested; the stored decision stands and the earlier attempt is typically `remediated_within_window`).
2. **Window scope:** the 14-day window is learner-wide across assessments. Is per-assessment or per-skill intended?
3. **Window anchor:** it runs from the `assign` decision's `decided_at`, not from the attempt or the quest completion; an expired or completed quest does not reset it. Confirm.
4. **AT-17 wording:** the original wording was not in the repo; the matrix text came from the brief with one adjustment ("assign decision" as the unit of assignment). Does the reviewer agree?
5. **Stored decisions vs config:** a stored decision is returned regardless of later config/policy changes; a policy-version bump never re-decides old attempts. Is a migration/override path needed?
6. **Mixed `expired_at` history:** events stored before the Step 4 change carry the completion time. Nothing reads `expired_at`, but a future reader would see mixed semantics.
7. **`checkpoint_completed.schema.json`:** keep, wire in (needs `score`), or delete? It ships in the wheel.
8. **`docs/metrics-framework.md` "Remediation Conversion Rate"** (lapsed users, next day) does not match M4's trigger (failed first checkpoint attempt). Rename, redefine or implement?
9. **Test/doc debris:** `tests/ingestion/test_dedup.py` still mentions a 7-day dedup window; the matrix M1 row lists only AT-1; the README architecture paragraph omits the new components.
10. **Edited earlier test:** `test_run_remediation_leaves_xp_streak_and_attempts_unchanged` was narrowed in Step 3 (quest assertions removed because assignment is now intended); confirm acceptable.
11. **Process:** the edge-case tests mostly passed on first run, so they were not seen failing; where noted, the reasoning for their strength is in the step audits, not demonstrated by mutation testing.

## 6. Anything I was unsure about in this prompt

- Whether the two fixes in §2 (README M1 sentence, event-contracts overview sentence) count as "stale M4/M1 statements"; I judged both as direct leftovers and kept the edits minimal.
- The counts "7 modified, 22 new" are from `git status`; new files inside the two untracked directories are counted individually (`git ls-files --others --exclude-standard` lists 22 entries including this file).
