# Week 4 Fix 1: Ingestion drops lessons for already-completed quests

## Goal

`ingest_lesson_completed()` in `src/ingestion/service.py` must accept a new,
valid `learning.lesson_completed` event (a `completion_id` never seen before)
and award its XP, even when `payload.assignment_id` refers to a quest that is
already completed. Finishing a quest must not cause later lessons to be thrown
away.

The real duplicate guards stay in place:

- A resent `completion_id` is still reported as `duplicate_completion_id`.
- A completed quest still never completes twice and never gets more progress.

## Root cause (with file:line)

**Location:** `src/ingestion/service.py:130-143`, inside `ingest_lesson_completed()`.

```python
    if (
        assignment_id is not None
        and f"quest_completion:{assignment_id}" in learner.completed_quest_ids
    ):
        quest_completion_id = f"quest_completion:{assignment_id}"
        evidence.record(
            "duplicate",
            learner_id,
            "duplicate_quest_completion",
            action_id=quest_completion_id,
            event_id=event_id,
            original_ref=quest_completion_id,
        )
        return IngestionResult("duplicate", "duplicate_quest_completion")
```

`assignment_id` is read from the payload at `src/ingestion/service.py:57-62`:

```python
    assignment_id = (
        str(envelope.get("payload", {}).get("assignment_id"))
        if isinstance(envelope.get("payload"), Mapping)
        and envelope["payload"].get("assignment_id") is not None
        else None
    )
```

**Why this is wrong:**

1. The check runs after the `completion_id` dedup (`service.py:114-128`). So
   any event that reaches line 130 is a lesson completion we have not seen
   before. The check then looks only at *quest* state
   (`quest_completion:{assignment_id}` in `learner.completed_quest_ids`). It
   does not ask whether this *lesson* was already recorded.
2. When the check matches, the function returns early at line 143, before
   `learner.complete_lesson(...)` at `service.py:144-150`. So no
   `LessonCompleted` and no `XPAwarded` are recorded. The only trace is a
   `duplicate` row in `ingestion_evidence`, so the lesson and its XP are gone.
3. The guard is also not needed. `Learner.complete_lesson()` already
   protects completed quests itself, and it does not read the payload's
   `assignment_id` at all. It loops over every assigned quest and skips
   quests that are not active, and quests that are already complete:
   - `src/state_machine/learner.py:170-171`: `if instance["status"] != "assigned": continue`
   - `src/state_machine/learner.py:180-181`: `quest_completion_id = f"quest_completion:{assignment_id}"` / `if quest_completion_id not in self.completed_quest_ids:`

   So taking out the ingestion-level early return would not let a quest
   complete twice. The lesson would be recorded with its XP, and the
   completed quest would be left alone.

**The existing test expects the bug:**
`tests/ingestion/test_ingestion_service.py:340-387`
(`test_duplicate_quest_completion_writes_durable_evidence`) completes
`assignment-1` with three lessons. It then sends a fourth lesson with a *new*
`completion_id` (`"quest-repeat"`) and asserts it is rejected:

```python
        duplicate = _envelope(learner_id, "quest-repeat", "00000000-0000-4000-8000-000000000204")
        duplicate["payload"]["assignment_id"] = "assignment-1"
        ...
        assert (result.status, result.reason) == (
            "duplicate",
            "duplicate_quest_completion",
        )
        assert list(application.events.get(learner.id)) == events_before
```

## Changes made (file / function / before / after / why)

All in `src/ingestion/service.py`. Line numbers are post-change.

### 1. New helper `_quest_already_complete` (lines 34-38)

- **Before:** none.
- **After:**
  ```python
  def _quest_already_complete(learner: Learner, assignment_id: str | None) -> bool:
      return (
          assignment_id is not None
          and f"quest_completion:{assignment_id}" in learner.completed_quest_ids
      )
  ```
- **Why:** one definition of "this quest is already done", used on both the first
  attempt and the retry.

### 2. `ingest_lesson_completed`: early return removed, flag computed (line 137)

- **Before** (old lines 130-143):
  ```python
      if (
          assignment_id is not None
          and f"quest_completion:{assignment_id}" in learner.completed_quest_ids
      ):
          quest_completion_id = f"quest_completion:{assignment_id}"
          evidence.record("duplicate", learner_id, "duplicate_quest_completion", ...)
          return IngestionResult("duplicate", "duplicate_quest_completion")
      accepted = learner.complete_lesson(
  ```
- **After:**
  ```python
      quest_already_complete = _quest_already_complete(learner, assignment_id)
      accepted = learner.complete_lesson(
  ```
- **Why:** this was the root cause. The lesson now reaches `complete_lesson()`,
  which already ignores completed quests.

### 3. `ingest_lesson_completed`: flag recomputed in the IntegrityError retry (line 174)

- **Before:** no flag; the retry went straight to `learner.complete_lesson(...)`.
- **After:**
  ```python
          quest_already_complete = _quest_already_complete(learner, assignment_id)
          accepted = learner.complete_lesson(
  ```
- **Why:** `learner` is reloaded at that point (`repository.get`), so the quest
  may have completed in between.

### 4. `ingest_lesson_completed`: evidence and result on accept (lines 209-219)

- **Before:**
  ```python
      evidence.remember_delivery(learner_id, action_id, event_id)
      return IngestionResult("accepted")
  ```
- **After:**
  ```python
      evidence.remember_delivery(learner_id, action_id, event_id)
      if quest_already_complete:
          quest_completion_id = f"quest_completion:{assignment_id}"
          evidence.record(
              "duplicate",
              learner_id,
              "quest_already_completed",
              action_id=quest_completion_id,
              event_id=event_id,
              original_ref=quest_completion_id,
          )
          return IngestionResult("accepted", "quest_already_completed")
      return IngestionResult("accepted")
  ```
- **Why:** the lesson is accepted with its XP. A durable evidence row records
  that it was tagged to a completed quest. The result carries the reason as
  information.

## Tests added or changed

- **Added** `tests/ingestion/test_completed_quest_lesson.py::test_new_lesson_for_completed_quest_is_accepted_with_xp`
  (new file; `src/` untouched). It assigns `lesson_starter_v1` as `assignment-1`
  and ingests 3 lessons tagged `assignment-1`. Each is accepted, XP is 300 and the
  quest is completed. It then ingests a 4th lesson with a new `completion_id`
  and expects: `accepted`, `xp_total == 400`, `completed_lesson_count == 4`,
  quest still `completed` with progress 3, exactly 4 `XPAwarded` and 1
  `QuestCompleted` events, and no evidence rows.

- **Changed (rewritten and renamed)** in `tests/ingestion/test_ingestion_service.py`:
  `test_duplicate_quest_completion_writes_durable_evidence` is now
  `test_lesson_for_completed_quest_is_accepted_and_writes_durable_evidence`.

  *Old assertion (wrong):* after 3 lessons completed `assignment-1`, a 4th
  lesson with a **new** `completion_id` (`quest-repeat`) was expected to return
  `("duplicate", "duplicate_quest_completion")`, to leave the event stream
  unchanged (`list(application.events.get(learner.id)) == events_before`), and
  to leave one evidence row with reason `duplicate_quest_completion`.

  *Why it is wrong:* a lesson with a never-seen `completion_id` is a valid new
  action, not a duplicate. Real duplicates are caught by `completion_id`
  (`service.py:114-128`). Asserting that the stream is unchanged encoded the
  bug as the contract: the lesson and its 100 XP were silently dropped
  just because the quest it was tagged to had finished.

  *New contract:* the lesson is `accepted`, XP goes from 300 to 400, and one
  evidence row records the tag as informational: reason
  `quest_already_completed`, `action_id` and `original_ref` both
  `quest_completion:assignment-1`. The row is still there after reopening
  `EvidenceStore` (restart check kept, now also checking `action_id` and
  `original_ref`).

  *Resolved:* `test_completed_quest_lesson.py` first asserted
  `evidence.list(learner_id) == []`, which contradicted this contract. It was
  updated (see the next entry).

- **Changed** `test_new_lesson_for_completed_quest_is_accepted_with_xp` in
  `tests/ingestion/test_completed_quest_lesson.py`. It was stale after the fix.
  Lessons 0-2 now expect `("accepted", None)` and no evidence rows so far.
  Lesson 3 expects `("accepted", "quest_already_completed")`. At the end it
  expects exactly one row: reason `quest_already_completed`, `action_id` and
  `original_ref` both `quest_completion:assignment-1`, and `event_id` equal to
  the 4th envelope's `event_id`.

- **Added** to `tests/ingestion/test_completed_quest_lesson.py` (shared helpers
  `_setup`, `_ingest`, `_complete_quest_and_fourth_lesson`). All four passed on
  the fixed code, so `learner.py` was not touched:

  | Case | Test | Result |
  |---|---|---|
  | a | `test_resent_fourth_completion_id_is_duplicate_and_xp_unchanged`: the 4th lesson's same `completion_id` with a new `event_id` gives `duplicate` / `duplicate_completion_id`, XP stays 400 | PASSED |
  | b | `test_lesson_tagged_to_never_assigned_assignment_is_accepted_without_quest_evidence`: lesson tagged to an `assignment_id` that was never assigned is accepted, XP 100, no evidence rows, no exception | PASSED |
  | c | `test_lesson_tagged_to_expired_quest_is_accepted_without_completed_quest_evidence`: quest deadline 2026-08-15, lesson occurs 2026-09-01. The lesson is accepted with XP 100, the quest ends `expired`, and no `quest_already_completed` row exists | PASSED |
  | d | `test_rebuild_after_fourth_lesson_matches_live_aggregate`: `rebuild_learner_state(events)` gives `xp_total` 400 and matches the live aggregate on `xp_total`, `new_streak`, `freezes_remaining` and `activity_dates`. `completed_quests == ("lesson_starter_v1",)` | PASSED |

  Case (c) note: the expired quest is expired by `complete_lesson()` itself
  while the lesson is being processed (the lesson is later than the
  deadline). It was not expired beforehand.
  Case (d) note: replay compares five fields. It does not compare
  `completed_quest_ids`, because the aggregate stores several id forms.

## Commands run and exact results

### New test on unfixed code (expected to FAIL, and it does)

Command (the system `python` has no pytest, so the project venv is used):

```
.venv/bin/python -m pytest tests/ingestion/test_completed_quest_lesson.py -p no:cacheprovider
```

Exact output:

```
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0
rootdir: /home/sampadram/Sampadram/IITH/SEM 7/DATGC/project/gamification-engine
configfile: pyproject.toml
collected 1 item

tests/ingestion/test_completed_quest_lesson.py F                         [100%]

=================================== FAILURES ===================================
___________ test_new_lesson_for_completed_quest_is_accepted_with_xp ____________

tmp_path = PosixPath('/tmp/pytest-of-sampadram/pytest-0/test_new_lesson_for_completed_0')

    def test_new_lesson_for_completed_quest_is_accepted_with_xp(tmp_path):
        ...
            for index in range(4):
                envelope = _envelope(
                    learner_id,
                    f"quest-{index}",
                    f"00000000-0000-4000-8000-00000000040{index}",
                )
                envelope["payload"]["assignment_id"] = "assignment-1"
                result = ingest_lesson_completed(
                    application, evidence, envelope, authenticated_producer="learning-svc"
                )
>               assert (result.status, result.reason) == ("accepted", None), index
E               AssertionError: 3
E               assert ('duplicate',...t_completion') == ('accepted', None)
E                 
E                 At index 0 diff: 'duplicate' != 'accepted'
E                 Use -v to get more diff

tests/ingestion/test_completed_quest_lesson.py:60: AssertionError
=========================== short test summary info ============================
FAILED tests/ingestion/test_completed_quest_lesson.py::test_new_lesson_for_completed_quest_is_accepted_with_xp
============================== 1 failed in 0.12s ===============================
```

(The test body is abbreviated with `...`; everything else is verbatim pytest
output. The pytest traceback also prints the setup lines, left out here.)

Reading: lessons 0-2 passed the loop assertions, including the mid-loop check
of XP 300 and quest `completed`. Lesson index 3 (the 4th) came back
`duplicate` / `duplicate_quest_completion` instead of `accepted`. This
confirms the root cause.

### Both tests after the fix in `service.py`

Command:

```
.venv/bin/python -m pytest tests/ingestion/test_completed_quest_lesson.py \
  "tests/ingestion/test_ingestion_service.py::test_lesson_for_completed_quest_is_accepted_and_writes_durable_evidence" \
  -p no:cacheprovider
```

Result (tail of the output): `1 failed, 1 passed in 0.16s`

- `test_lesson_for_completed_quest_is_accepted_and_writes_durable_evidence`: **PASSED**.
- `test_new_lesson_for_completed_quest_is_accepted_with_xp`: **FAILED**, and not
  because of the fix:
  ```
  >               assert (result.status, result.reason) == ("accepted", None), index
  E               AssertionError: 3
  E               assert ('accepted', ...dy_completed') == ('accepted', None)
  E                 At index 1 diff: 'quest_already_completed' != None
  ```
  The 4th lesson is now accepted, but the test still expects `reason is None`.
  The agreed contract returns `("accepted", "quest_already_completed")`. The
  test's last line, `assert evidence.list(learner_id) == []`, is also stale (see
  the earlier note on the evidence contract). Both assertions in this test
  encoded the pre-fix expectation. It was updated afterwards, see below.

### Whole file `test_completed_quest_lesson.py` with cases a-d added

Command:

```
.venv/bin/python -m pytest tests/ingestion/test_completed_quest_lesson.py -p no:cacheprovider -v
```

Result: `1 failed, 4 passed in 0.18s`. The four new tests (a-d) PASSED. The one
failure is the same stale test as above
(`test_new_lesson_for_completed_quest_is_accepted_with_xp`, `AssertionError: 3`,
expects `reason None`, gets `quest_already_completed`). No failure came from
cases a-d.

### Full suite after all changes

Command: `.venv/bin/python -m pytest -q -p no:cacheprovider`

```
6 failed, 113 passed, 9 skipped in 1.49s
```

All 6 failures are in `tests/state_machine/test_active_state_path.py`, a file
that was untracked in the working tree before this fix and that I did not
touch. Split runs for attribution:

| Run | Result |
|---|---|
| `--ignore=tests/state_machine/test_active_state_path.py` | `111 passed, 9 skipped in 1.42s` |
| `tests/state_machine/test_active_state_path.py` only | `6 failed, 2 passed in 0.20s` |

Baseline was `106 passed, 9 skipped`. 111 = 106 + 5 new tests (cases a-d plus
the new-lesson test). The rewritten test replaced the old one, so it adds none.

Failing tests in `test_active_state_path.py` (none call `ingest_lesson_completed`):

- `test_streak_projection_matches_aggregate_for_late_oct1`
- `test_streak_projection_counts_freeze_acquired_after_latest_activity`
- `test_streak_projection_is_idempotent_when_events_are_delivered_twice`
- `test_quest_projection_keeps_repeated_assignments_separate`
- `test_quest_projection_marks_overdue_assignment_expired_without_new_lesson`
- `test_quest_projection_applies_expiry_events`

Causes seen in the error output: `LearnerQuestProjection.get_summary()` has no
`as_of` argument (`TypeError`), `LearnerQuestSummary` has no `.assignments`
attribute (`AttributeError`), and the streak projection assertions give
different values. They look like tests written ahead of unimplemented
projection features. I did not run this file against the pre-fix code to
confirm that.

## Assumptions checked

- **`complete_lesson()` already protects completed quests.** It skips quests
  whose status is not `assigned` (`learner.py:170-171`) and quests already in
  `completed_quest_ids` (`learner.py:180-181`). Checked by test: after the 4th
  lesson the quest is still `completed`, progress 3, with exactly 1
  `QuestCompleted`.
- **Real duplicates are still caught by `completion_id`.** Case (a): same
  `completion_id` with a new `event_id` gives `duplicate` /
  `duplicate_completion_id`, and XP stays 400.
- **A tag for an unknown assignment is harmless.** Case (b): accepted, no
  evidence, no exception.
- **An expired quest does not produce `quest_already_completed`.** Case (c):
  the lesson is accepted, the quest is expired, and no such row exists.
- **Replay agrees with the live aggregate.** Case (d): `xp_total` 400, and
  streak, freezes and activity dates match.
- **Evidence survives a restart.** The rewritten test in
  `test_ingestion_service.py` reopens `EvidenceStore` and reads the same row.

## Remaining uncertainty

- **Evidence `kind` is `duplicate` for an accepted lesson.** That is what was
  requested, and the table only allows `duplicate` or `rejected`. A reader that
  counts `duplicate` rows as dropped deliveries would now over-count. I did
  not search the repo for such readers.
- **Return contract changed.** An accepted result can now carry a non-None
  `reason` (`quest_already_completed`). A caller that treats
  `reason is not None` as failure would misread it. I did not check callers
  outside the tests.
- **Evidence write is not atomic with the save.** It happens after
  `remember_delivery`. A crash in between would leave an accepted lesson
  without its informational row. Not tested.
- **The `IntegrityError` retry path is untested.** The flag is recomputed there
  (`service.py:174`), but no test forces that path.
- **Concurrency is untested.** If another worker completes the quest between the
  read and the save, the flag may be stale. It only affects the informational
  row, not XP.
- **Repeat tagged lessons.** Each new lesson tagged to the completed quest
  writes its own row (different `event_id`). Only one such row was tested.
- **`test_active_state_path.py`** failures are unrelated and were not
  investigated beyond the error messages.
- **`README.md`** shows as modified in `git status`. It was modified before this
  work and I did not touch it.

## AI-assisted implementation log

- **Task:** Stop `ingest_lesson_completed()` from rejecting a new valid lesson
  as `duplicate_quest_completion` when its `assignment_id` belongs to an
  already completed quest. The lesson is now accepted with its XP, and an
  informational `quest_already_completed` evidence row is recorded.
- **Affected files:**
  - `src/ingestion/service.py` (helper `_quest_already_complete`, early return
    removed, flag computed twice, evidence and result on accept)
  - `tests/ingestion/test_ingestion_service.py` (one test rewritten and renamed)
  - `tests/ingestion/test_completed_quest_lesson.py` (new file, 5 tests)
  - `docs/audit/week4-fix1-ingestion.md` (this file)
- **Tests that verified it:**
  - `test_lesson_for_completed_quest_is_accepted_and_writes_durable_evidence`
  - `test_new_lesson_for_completed_quest_is_accepted_with_xp`
  - `test_resent_fourth_completion_id_is_duplicate_and_xp_unchanged`
  - `test_lesson_tagged_to_never_assigned_assignment_is_accepted_without_quest_evidence`
  - `test_lesson_tagged_to_expired_quest_is_accepted_without_completed_quest_evidence`
  - `test_rebuild_after_fourth_lesson_matches_live_aggregate`
  - Full suite without the unrelated file: `111 passed, 9 skipped`.
- **Git:** the tool ran no git commands that change anything; commits are left
  to the author.
