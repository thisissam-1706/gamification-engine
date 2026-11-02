# Week 6 / M4 / Step 5 — AT-17 acceptance test

Status: **Prompt 1 of this step — AT-17 acceptance test written; it passes. No source change.**
Git rule in force: no state-changing git commands (none were run).

## 1. Changes made

- Created this file and `tests/interventions/test_learning_remediation.py` (3 tests).
- Not touched: any source file, `docs/traceability-matrix.md`, README, `docs/event-contracts.md`.

## 2. Before / after

Before: AT-17 appears only as "AT-17 (to be written)" in `docs/traceability-matrix.md` line 33; no test carried the AT-17 name.
After: one acceptance file exercising the M4 path end to end. The matrix row is deliberately unchanged in this prompt.

## 3. Where the existing acceptance tests live and how they are named

| AT | File | Style |
|---|---|---|
| AT-6 | `tests/abuse/test_point_farming.py` | module docstring `"""AT-6: Point Farming / Decay Cap (M6)` + the given/when/then wording; functions named after the behaviour |
| AT-7 | `tests/leaderboard/test_leaderboard_reconciliation.py` | `"""AT-7: Leaderboard Reconciliation (M3)` + wording |
| AT-15 | `tests/integration/test_full_state_rebuild.py` | `"""AT-15: Full State Rebuild (M2, M7, M8)` + wording; its test carries `integration` marker semantics (`-m 'not integration'` is the default) |

So an AT file sits in the test directory of the feature area, is named `test_<feature>.py` (not `test_at17`), and opens with a docstring `AT-<n>: <title> (M<k>)`.
AT-17 therefore goes in `tests/interventions/` as `test_learning_remediation.py`, with docstring `AT-17: Learning-oriented remediation (M4)`. It is not marked `integration`
(AT-6 and AT-7 are not), so it runs in the default suite. Function names carry an `at17` tag for greppability: `test_at17_<behaviour>`.

**The AT-17 wording itself is not in the repo.** `grep AT-17 docs` finds only the matrix row ("to be written"). The docstring therefore restates the three scenarios from your M4 brief and says so. If you have the original acceptance text, the docstring should quote it.

## 4. Tests (all in `tests/interventions/test_learning_remediation.py`)

Common setup: real `LearnerApplication` on a tmp_path SQLite DB; attempts through `ingest_checkpoint_attempted` and completions through `ingest_lesson_completed`, both with
`IngestConfig(DEFAULT_INGEST_RULE_VERSION, clock=lambda: now)` — the default 72 h late window with a fixed trusted clock, not the unbounded test config. Each failed attempt occurred 1 h before
it was received. `run_remediation` / `run_quest_expiry` get fixed clocks. T0 = 2026-10-12T09:00Z.

- (a) `test_at17_failed_first_attempt_with_no_recent_assign_stores_assign_and_quest` — failed first attempt on `math` → stored `assign` decision (`decided_at` = T0) and quest
  `remediation_quest:att-1` with quest_id `remediation_v1`, status `assigned`, `assigned_at` = T0, `deadline_at` = T0 + 7 days; one `RemediationDecided` and one `QuestAssigned` in the stream.
- (b) `test_at17_failed_attempt_within_14_days_of_an_assign_stores_an_explicit_no_action` — assign at T0; a failed first attempt on `physics` 5 days later → stored `no_action`
  with reason `remediated_within_window` (quest_id/deadline None), two `RemediationDecided`, still one `QuestAssigned`, only the first quest exists.
- (c) `test_at17_uncompleted_quest_expires_after_its_deadline_and_cannot_be_completed` — sweep at exactly the deadline → `[]`, still `assigned`; sweep at deadline + 1 s → expired,
  one `QuestExpired` with `expired_at == deadline`; then a `lesson_completed` with `completion_kind: "remediation"` (occurred and received at deadline + 2 h, via ingestion) is accepted
  but the quest stays `expired` with progress 0, no `QuestCompleted`, and no other quest appears.

## 5. Command and output

```
$ python -m pytest tests/interventions/test_learning_remediation.py -v
collecting ... collected 3 items

tests/interventions/test_learning_remediation.py::test_at17_failed_first_attempt_with_no_recent_assign_stores_assign_and_quest PASSED [ 33%]
tests/interventions/test_learning_remediation.py::test_at17_failed_attempt_within_14_days_of_an_assign_stores_an_explicit_no_action PASSED [ 66%]
tests/interventions/test_learning_remediation.py::test_at17_uncompleted_quest_expires_after_its_deadline_and_cannot_be_completed PASSED [ 100%]

============================== 3 passed in 0.19s ==============================
```
(Header lines — platform, rootdir, plugins — omitted here.) All three passed on the first run; no source change was needed or made.
The full suite was not re-run in this prompt.

## 6. Assumptions / unsure

- File name `test_learning_remediation.py` and the `at17` tag in function names are my choice, following the "feature-named file" convention above.
- Reuses private helpers (`_open`, `_new_learner`, `_learner`, `_checkpoint_envelope`) from `test_checkpoint_attempt_ingestion.py`, as the other `tests/interventions` files do; the lesson envelope is built locally.
- The lesson completion in (c) is dated after the deadline, so the lazy path never gets a chance to expire it (already expired) and only the `expired` status stops completion.
  A completion that occurred before the deadline but arrived after the sweep is covered by `test_swept_quest_is_not_progressed_or_completed_by_a_later_remediation_completion`, not here.
- Scenario (a) "no remediation assign in the last 14 days" is shown by a learner with no prior decisions; (b) shows the 5-day case. The 14-day boundary itself is covered by the policy unit tests, not repeated here.
- Not marked `integration`, so it runs in the default suite; say if AT-17 should be marked like AT-15.
- The docstring says the AT-17 wording is absent from the repo; that line should be replaced once the real wording is supplied.
