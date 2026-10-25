# Week 4 Fix 3: Quest reader loses assignment identity, status and deadline

Status: **done. Not committed.** The quest reader now keeps one record per
`assignment_id` and agrees with the `Learner` aggregate on status, progress and
`completed_at` for 300 seeded random histories (0 mismatches). `get_summary`
takes an optional `as_of` for read-time expiry. `test_quest_reader.py` passes
12/12, and the full suite is **145 passed, 0 failed, 9 skipped**. No aggregate
change was needed or made.

The sections below are a chronological log. Earlier sections keep the output
from that point in time. "Final verification" has the current state.

## Summary

| What | Where | Change |
|---|---|---|
| New `AssignmentRecord` frozen dataclass (`assignment_id, quest_id, status, progress, assigned_at, deadline_at, completed_at`) | `src/state_machine/projection.py` | one read-model record per assignment; timestamps parsed with `learner._as_datetime` |
| `_STATUS_RANK` / `_stronger_status` | `src/state_machine/projection.py` | status precedence `completed > expired > assigned` |
| `LearnerQuestSummary` | `src/state_machine/projection.py` | new field `assignments: dict[str, AssignmentRecord]` (default `{}`). `assigned_quests` / `completed_quests` are now one entry per assignment, sorted by `(quest_id, assignment_id)`; `quest_progress` keyed by `assignment_id` |
| `LearnerQuestProjection.apply` | `src/state_machine/projection.py` | handles all 4 quest events; records created lazily; progress = max; `completed_at` = earliest; precedence for status. Order- and redelivery-independent |
| `LearnerQuestProjection.get_summary(learner_id, as_of=None)` | `src/state_machine/projection.py` | optional read-time expiry: `assigned` and `as_of > deadline_at` → reported `expired`. Never stored, no `datetime.now` |
| `clear` / `rebuild` / `quest_summaries` | `src/state_machine/projection.py` | names and signatures kept; `quest_summaries` values are now `dict[assignment_id, AssignmentRecord]` |
| New tests (12) | `tests/state_machine/test_quest_reader.py` | 3 copied unchanged, 7 edge cases (one parametrized ×2), 1 seeded aggregate cross-check, 1 divergence regression |
| Audit log | `docs/audit/week4-fix3-quest-reader.md` | this file |

Not touched: `src/state_machine/learner.py`, `rebuild_learner_state`, every
other `src/` file, `test_active_state_path.py` and every other existing test.

Known out-of-scope issues (details in "Uncertainty"):
- `rebuild_learner_state(...).completed_quests` is a distinct set of quest
  ids; the projection lists one entry per completed assignment. Pinned by a
  regression test.
- Time-based expiry is read-time only. No `QuestExpired` event is written
  when a deadline passes without a lesson.
- Status precedence (`completed > expired`) is not pinned by a repo test,
  because the aggregate can't produce a history that needs it.

## Goal

`LearnerQuestProjection` (the quest reader) must agree with the `Learner`
aggregate's `quest_instances` per assignment:

- Repeated assignments of the same `quest_id` stay separate, keyed by
  `assignment_id`, each with its own `status`, `progress` and `deadline_at`.
- `QuestExpired` events set that assignment's status to `expired`.
- `get_summary(learner_id, as_of=...)` reports an overdue, not-completed
  assignment as `expired` even when no event (no new lesson) triggered a
  `QuestExpired`. A completed assignment never becomes expired.
- Existing callers of `get_summary(learner_id)` and of the
  `assigned_quests` / `completed_quests` / `quest_progress` fields keep
  working (see "Users").

## Files I will touch

| File | Status | Change |
|---|---|---|
| `docs/audit/week4-fix3-quest-reader.md` | new | this audit log |
| `tests/state_machine/test_quest_reader.py` | new | 12 tests (3 copied unchanged in Step 4, 7 in Step 6, 2 in Step 7) |
| `src/state_machine/projection.py` | modified in Steps 5–6 (+91 / −41 total) | imports; new `AssignmentRecord`, `_STATUS_RANK`, `_stronger_status`; `LearnerQuestSummary`; `LearnerQuestProjection.__init__/apply/get_summary` (Step 6: `as_of`). `clear` / `rebuild` unchanged |

Not to be touched: `tests/state_machine/test_active_state_path.py` and every
other existing test. `src/state_machine/learner.py` is not expected to change;
if it has to, that will be logged here first.

## Users of `LearnerQuestProjection` / `LearnerQuestSummary` / `quest_summaries` / `get_summary`

Command:

```bash
grep -rn "LearnerQuestProjection\|LearnerQuestSummary\|quest_summaries\|get_summary" src tests
```

Output (exit 0):

```
tests/state_machine/test_quest.py:7:    LearnerQuestProjection,
tests/state_machine/test_quest.py:178:    """LearnerQuestProjection and rebuild_learner_state deterministically fold facts."""
tests/state_machine/test_quest.py:198:        projection = LearnerQuestProjection()
tests/state_machine/test_quest.py:200:        summary = projection.get_summary(learner.id)
tests/state_machine/test_quest.py:257:        rebuilt = LearnerQuestProjection()
tests/state_machine/test_replay_no_decide.py:14:    LearnerQuestProjection,
tests/state_machine/test_replay_no_decide.py:92:        quest_proj = LearnerQuestProjection()
tests/state_machine/test_replay_no_decide.py:94:        quest_summary = quest_proj.get_summary(learner_id)
src/state_machine/projection.py:131:class LearnerQuestSummary:
src/state_machine/projection.py:139:class LearnerQuestProjection:
src/state_machine/projection.py:143:        self.quest_summaries: dict[Any, dict[str, Any]] = {}
src/state_machine/projection.py:149:            if learner_id not in self.quest_summaries:
src/state_machine/projection.py:150:                self.quest_summaries[learner_id] = {
src/state_machine/projection.py:155:            self.quest_summaries[learner_id]["assigned"].add(domain_event.quest_id)
src/state_machine/projection.py:156:            self.quest_summaries[learner_id]["progress"].setdefault(
src/state_machine/projection.py:161:            if learner_id not in self.quest_summaries:
src/state_machine/projection.py:162:                self.quest_summaries[learner_id] = {
src/state_machine/projection.py:167:            self.quest_summaries[learner_id]["progress"][domain_event.assignment_id] = (
src/state_machine/projection.py:172:            if learner_id not in self.quest_summaries:
src/state_machine/projection.py:173:                self.quest_summaries[learner_id] = {
src/state_machine/projection.py:178:            self.quest_summaries[learner_id]["completed"].add(domain_event.quest_id)
src/state_machine/projection.py:180:    def get_summary(self, learner_id: Any) -> LearnerQuestSummary:
src/state_machine/projection.py:182:        data = self.quest_summaries.get(
src/state_machine/projection.py:185:        return LearnerQuestSummary(
src/state_machine/projection.py:193:        self.quest_summaries.clear()
src/state_machine/projection.py:197:        staged = LearnerQuestProjection()
src/state_machine/projection.py:200:        self.quest_summaries = staged.quest_summaries
tests/state_machine/test_active_state_path.py:3:Covers: LearnerStreakProjection (arrival order / freezes), LearnerQuestProjection
tests/state_machine/test_active_state_path.py:15:    LearnerQuestProjection,
tests/state_machine/test_active_state_path.py:112:    projection = LearnerQuestProjection()
tests/state_machine/test_active_state_path.py:115:    summary = projection.get_summary(learner.id)
tests/state_machine/test_active_state_path.py:133:    projection = LearnerQuestProjection()
tests/state_machine/test_active_state_path.py:137:    before_deadline = projection.get_summary(
tests/state_machine/test_active_state_path.py:140:    after_deadline = projection.get_summary(
tests/state_machine/test_active_state_path.py:156:    projection = LearnerQuestProjection()
tests/state_machine/test_active_state_path.py:159:    summary = projection.get_summary(learner.id)
```

(Grep ran before `test_quest_reader.py` existed, so the new file is not listed.)

| Location | Kind | What it uses / asserts |
|---|---|---|
| `src/state_machine/projection.py:130-136` | definition | `LearnerQuestSummary(assigned_quests, completed_quests, quest_progress)`, frozen dataclass |
| `src/state_machine/projection.py:139-200` | definition | `LearnerQuestProjection`: `quest_summaries`, `apply`, `get_summary(learner_id)`, `clear`, `rebuild` |
| `tests/state_machine/test_quest.py:177-206` (`test_quest_projection_and_state_rebuild`) | test, passes | `rebuild(events)`, `get_summary(id)`; one default assignment → `assigned_quests == ("lesson_starter_v1",)`, `completed_quests == ("lesson_starter_v1",)`, `quest_progress == {"quest:lesson_starter_v1": 3}` |
| `tests/state_machine/test_quest.py:257-258` (`test_quest_expiry_is_durable_across_restart_and_rebuild`) | test, passes | only constructs and calls `rebuild(restored_events)` on events that include a `QuestExpired`; asserts nothing on the projection |
| `tests/state_machine/test_replay_no_decide.py:92-96` (`test_replay_does_not_call_decision_functions`) | test, passes | `rebuild(events)`, `get_summary(id)`; same `completed_quests` / `quest_progress` assertions as `test_quest.py`. Also checks that rebuild calls no decision function |
| `tests/state_machine/test_active_state_path.py:112-161` | test, **fails** | the 3 target tests: `summary.assignments[...]` with `.status/.progress/.deadline_at`, `get_summary(id, as_of=...)`, non-collapsed `assigned_quests == (QUEST, QUEST)` |

Nothing else in `src/` reads the quest projection: no API, service,
leaderboard or other projection. `quest_summaries` is only read inside the
class itself. So the constraints on the fix are:

1. Keep `get_summary(learner_id)` working with no `as_of`.
2. Keep `assigned_quests`, `completed_quests` and `quest_progress` on the
   summary with the values the two passing tests expect.
3. Keep `apply` / `clear` / `rebuild`, and keep `rebuild` free of decision
   calls.
4. `quest_summaries` is a public attribute name but has no reader outside the
   class; its internal shape can change.

## Before

`src/state_machine/projection.py:130-200` (current code, condensed):

```python
@dataclass(frozen=True)
class LearnerQuestSummary:
    assigned_quests: tuple[str, ...]
    completed_quests: tuple[str, ...]
    quest_progress: dict[str, int]

class LearnerQuestProjection:
    def apply(self, domain_event):
        if isinstance(domain_event, Learner.QuestAssigned):
            ...["assigned"].add(domain_event.quest_id)          # a set of quest ids
            ...["progress"].setdefault(domain_event.assignment_id, 0)
        elif isinstance(domain_event, Learner.QuestProgressed):
            ...["progress"][domain_event.assignment_id] = domain_event.current_step
        elif isinstance(domain_event, Learner.QuestCompleted):
            ...["completed"].add(domain_event.quest_id)         # a set of quest ids

    def get_summary(self, learner_id) -> LearnerQuestSummary:
        return LearnerQuestSummary(
            assigned_quests=tuple(sorted(data["assigned"])),
            completed_quests=tuple(sorted(data["completed"])),
            quest_progress=dict(data.get("progress", {})),
        )
```

Problems I found by reading the code (the test run confirms 1, 3 and 4):

1. **No per-assignment record.** There is no `assignments` field, so status
   and deadline are not exposed at all. `QuestAssigned.deadline_at` and
   `assigned_at` are dropped.
2. **Assignment identity collapses.** `assigned` and `completed` are sets of
   `quest_id`, so two assignments of the same quest become one entry.
   (`quest_progress` is keyed by `assignment_id` and already stays separate.)
3. **`QuestExpired` is ignored.** `apply` has no branch for it.
4. **No time-based expiry.** `get_summary` has no `as_of` parameter, so an
   overdue assignment with no later lesson can never read as expired.

For reference, the aggregate (`src/state_machine/learner.py:362-417`) keeps
`quest_instances[assignment_id]` with `quest_id`, `definition_version`,
`status` (`assigned` → `completed` | `expired`), `assigned_at`, `deadline_at`,
`completed_at` and `progress`. Event payloads:

| Event | Fields |
|---|---|
| `QuestAssigned` | `assignment_id, quest_id, quest_definition_version, assigned_at, deadline_at` |
| `QuestProgressed` | `completion_id, assignment_id, quest_id, current_step, target_step` |
| `QuestCompleted` | `completion_id, assignment_id, quest_id, completed_at` |
| `QuestExpired` | `assignment_id, quest_id, expired_at` |

## After (Step 5)

Only `src/state_machine/projection.py` changed. `git diff --stat`:
`1 file changed, 73 insertions(+), 39 deletions(-)`. Nothing else in `src/`
changed.

### Imports

Before:

```python
from dataclasses import dataclass
...
from src.state_machine.learner import Learner
```

After:

```python
from dataclasses import dataclass, field, replace
from datetime import datetime
...
from src.state_machine.learner import Learner, _as_datetime
```

`_as_datetime` is a private helper in `learner.py`, imported as instructed.
`projection.py` already imported from `learner.py`, so there is no new
import cycle.

### New: `AssignmentRecord`, `_STATUS_RANK`, `_stronger_status`

Before: did not exist.

After:

```python
@dataclass(frozen=True)
class AssignmentRecord:
    assignment_id: str
    quest_id: str
    status: str = "assigned"
    progress: int = 0
    assigned_at: datetime | None = None
    deadline_at: datetime | None = None
    completed_at: datetime | None = None

# Higher rank wins, so arrival order and redelivery cannot change the status.
_STATUS_RANK = {"assigned": 0, "expired": 1, "completed": 2}

def _stronger_status(current: str, incoming: str) -> str:
    return incoming if _STATUS_RANK[incoming] > _STATUS_RANK[current] else current
```

The defaults let a record be created lazily from any quest event, which all
carry `assignment_id` and `quest_id`.

### `LearnerQuestSummary`

Before:

```python
@dataclass(frozen=True)
class LearnerQuestSummary:
    assigned_quests: tuple[str, ...]
    completed_quests: tuple[str, ...]
    quest_progress: dict[str, int]
```

After:

```python
@dataclass(frozen=True)
class LearnerQuestSummary:
    assigned_quests: tuple[str, ...]
    completed_quests: tuple[str, ...]
    quest_progress: dict[str, int]
    assignments: dict[str, AssignmentRecord] = field(default_factory=dict)
```

Existing positional/keyword construction with three fields still works.

### `LearnerQuestProjection`

Before: see "Before" above (sets of `quest_id` for assigned/completed,
`progress[assignment_id] = current_step` last-write-wins, no `QuestExpired`).

After (`apply` and `get_summary`; `clear` and `rebuild` are unchanged and
still swap/clear `quest_summaries`):

```python
    def __init__(self) -> None:
        self.quest_summaries: dict[Any, dict[str, AssignmentRecord]] = {}

    def apply(self, domain_event: Any) -> None:
        if not isinstance(domain_event, (Learner.QuestAssigned, Learner.QuestProgressed,
                                         Learner.QuestCompleted, Learner.QuestExpired)):
            return
        records = self.quest_summaries.setdefault(domain_event.originator_id, {})
        assignment_id = domain_event.assignment_id
        # Progress, completion or expiry may arrive before the assignment.
        record = records.get(assignment_id) or AssignmentRecord(
            assignment_id=assignment_id, quest_id=domain_event.quest_id
        )
        if isinstance(domain_event, Learner.QuestAssigned):
            record = replace(record, quest_id=domain_event.quest_id,
                             assigned_at=_as_datetime(domain_event.assigned_at),
                             deadline_at=_as_datetime(domain_event.deadline_at))
        elif isinstance(domain_event, Learner.QuestProgressed):
            record = replace(record, progress=max(record.progress, domain_event.current_step))
        elif isinstance(domain_event, Learner.QuestCompleted):
            completed_at = _as_datetime(domain_event.completed_at)
            if record.completed_at is not None and completed_at is not None:
                completed_at = min(record.completed_at, completed_at)
            record = replace(record, status=_stronger_status(record.status, "completed"),
                             completed_at=completed_at or record.completed_at)
        else:
            record = replace(record, status=_stronger_status(record.status, "expired"))
        records[assignment_id] = record

    def get_summary(self, learner_id: Any) -> LearnerQuestSummary:
        records = self.quest_summaries.get(learner_id, {})
        ordered = sorted(records.values(), key=lambda r: (r.quest_id, r.assignment_id))
        return LearnerQuestSummary(
            assigned_quests=tuple(r.quest_id for r in ordered),
            completed_quests=tuple(r.quest_id for r in ordered if r.status == "completed"),
            quest_progress={r.assignment_id: r.progress for r in ordered},
            assignments={r.assignment_id: r for r in ordered},
        )
```

(Condensed formatting; the file has the full layout.)

Rules, as implemented:

| Event | Effect on the record |
|---|---|
| `QuestAssigned` | sets `quest_id`, `assigned_at`, `deadline_at`. Leaves status, progress and `completed_at` alone, so a late assignment cannot undo progress or completion that arrived first. |
| `QuestProgressed` | `progress = max(progress, current_step)` |
| `QuestCompleted` | status → `completed` (by precedence); `completed_at` = earliest non-`None` seen |
| `QuestExpired` | status → `expired` unless already `completed` |

Every rule is a max/min/overwrite-with-the-same-value, so the result does not
depend on arrival order and redelivery is a no-op. Scratch check below.

Summary fields: one entry per assignment, sorted by `(quest_id,
assignment_id)`. `assigned_quests` lists every assignment's `quest_id`
(so a repeated quest appears twice); `completed_quests` lists those with
status `completed`; `quest_progress` and `assignments` are keyed by
`assignment_id`.

Behaviour changes for existing readers:
- `assigned_quests` / `completed_quests` no longer collapse a repeated
  `quest_id`. With one assignment per quest (all existing tests), output is
  the same as before.
- `quest_progress` can no longer go down if a lower `current_step` arrives
  late. Without reordering, output is the same as before.
- `quest_summaries` values are now `dict[assignment_id, AssignmentRecord]`
  instead of the old `{"assigned", "completed", "progress"}` dict. No reader
  outside the class (per the grep).

Not implemented in Step 5 (by instruction): `get_summary(..., as_of=...)`.
Added in Step 6, below.

## After (Step 6): `as_of`

Only `LearnerQuestProjection.get_summary` changed (+18 / −2 on top of
Step 5). No other `src/` change. The projection needed no fixes for the new
tests: they all passed on the first run.

Before (end of Step 5):

```python
    def get_summary(self, learner_id: Any) -> LearnerQuestSummary:
        """Returns the summary of quest assignments for a learner."""
        records = self.quest_summaries.get(learner_id, {})
        ordered = sorted(
            records.values(), key=lambda record: (record.quest_id, record.assignment_id)
        )
        return LearnerQuestSummary(...)
```

After:

```python
    def get_summary(
        self, learner_id: Any, as_of: datetime | None = None
    ) -> LearnerQuestSummary:
        """Returns the summary of quest assignments for a learner.

        With ``as_of``, an assigned record whose deadline is strictly before
        ``as_of`` is reported as expired. Without it, status is event-derived only.
        """
        records = self.quest_summaries.get(learner_id, {})
        ordered = sorted(
            records.values(), key=lambda record: (record.quest_id, record.assignment_id)
        )
        cutoff = _as_datetime(as_of)
        if cutoff is not None:
            ordered = [
                replace(record, status="expired")
                if record.status == "assigned"
                and record.deadline_at is not None
                and cutoff > record.deadline_at
                else record
                for record in ordered
            ]
        return LearnerQuestSummary(...)   # unchanged
```

Rules:
- `as_of=None` (the default): no time-based expiry. Existing callers are
  unchanged.
- Only `status == "assigned"` records can become expired. `completed` and
  event-`expired` records are returned as stored.
- Strict `as_of > deadline_at`, matching the aggregate's
  `occurred > deadline` (`learner.py:177`). `as_of == deadline_at` stays
  assigned.
- `deadline_at is None` (a record created from progress/completion/expiry
  before its `QuestAssigned`) is never time-expired.
- The expiry is computed on read with `dataclasses.replace`. It never writes
  back to `quest_summaries`, so a later call without `as_of` still shows
  `assigned`.
- `as_of` goes through `_as_datetime`, so a naive `datetime` is treated as
  UTC (same as the aggregate) and comparing it with the aware `deadline_at`
  cannot raise. An ISO string would also be accepted, though the type hint
  says `datetime`.
- No `datetime.now` / `utcnow`: `grep -n "datetime.now\|utcnow"
  src/state_machine/projection.py` returns nothing (exit 1).
- `completed_quests` and `quest_progress` don't depend on `as_of`; only
  `assignments[...].status` can change.

## Tests

New file: `tests/state_machine/test_quest_reader.py`

| # | Test | Origin | Current result |
|---|---|---|---|
| 1 | `test_quest_projection_keeps_repeated_assignments_separate` | copied unchanged | FAIL: `AttributeError: 'LearnerQuestSummary' object has no attribute 'assignments'` |
| 2 | `test_quest_projection_marks_overdue_assignment_expired_without_new_lesson` | copied unchanged | FAIL: `TypeError: LearnerQuestProjection.get_summary() got an unexpected keyword argument 'as_of'` |
| 3 | `test_quest_projection_applies_expiry_events` | copied unchanged | FAIL: `AttributeError: 'LearnerQuestSummary' object has no attribute 'assignments'` |

(Results above are the baseline. After Step 5: tests 1 and 3 PASS; test 2
still fails with the same `as_of` `TypeError`. After Step 6: all 3 PASS.)

Step 6 edge-case tests (all PASS):

| # | Test | What it checks |
|---|---|---|
| 4 | `test_as_of_equal_to_deadline_is_still_assigned` | `as_of == deadline` → `assigned`; `deadline + 1µs` → `expired`; a later call without `as_of` still shows `assigned` (no write-back) |
| 5 | `test_progress_arriving_before_its_assignment` | 3 `QuestProgressed` applied before `QuestAssigned`: record exists with progress 3, status `assigned`, `deadline_at None`, and `as_of=2030` does not expire it. After the late `QuestAssigned`: progress still 3, `assigned_at` and `deadline_at` filled in |
| 6 | `test_same_events_delivered_twice_give_same_summary` | `_two_assignments` events, `events + events` vs `events`, with and without `as_of` |
| 7a | `test_every_arrival_order_of_one_assignment_gives_same_summary[_completed_assignment-completed-3]` | all 5! = 120 orders of `QuestAssigned, QuestProgressed ×3, QuestCompleted` give one summary; status matches the aggregate's `quest_instances` |
| 7b | `...[_expired_assignment-expired-1]` | all 3! = 6 orders of `QuestAssigned, QuestProgressed, QuestExpired` give one summary; status matches the aggregate |
| 8 | `test_two_learners_do_not_affect_each_other` | events of `_two_assignments` and `_expired_assignment` interleaved into one projection, both using assignment id `a1`; each summary equals its solo projection, with and without `as_of` |
| 9 | `test_learner_with_no_quests_returns_empty_summary` | learner with only lesson events, and a learner never seen: all summary fields empty, with and without `as_of` |

New helpers: `QUEST_EVENTS`, `_quest_events` (keeps only the 4 quest event
types), `_project`, `_completed_assignment`, `_expired_assignment`. Imports
added: `timedelta`, `itertools.permutations`, `pytest`,
`LearnerQuestSummary`. The 3 copied tests and their helpers were not edited.

The permutation tests permute only the quest events, as instructed. The
projection ignores every other event type, so including them would not change
the result.

Step 7 tests (both PASS):

| # | Test | What it checks |
|---|---|---|
| 10 | `test_projection_matches_aggregate_on_random_histories` | seeded cross-check, `SEED = 20261024`, 300 sequences. For every assignment, `(status, progress, completed_at)` from the projection (`as_of=None`) equals `learner.quest_instances[assignment_id]`, and both have the same assignment ids. It also asserts the run reached all three statuses. **0/300 mismatches.** |
| 11 | `test_rebuild_completed_quests_is_distinct_but_projection_is_per_assignment` | two assignments of the same quest, both completed: `rebuild_learner_state(events).completed_quests == (QUEST,)` while the projection gives `(QUEST, QUEST)`. Asserts today's behaviour. The comment marks it as a known divergence, out of scope |

New helpers: `SEED`, `SEQUENCES`, `BASE`, `_random_ops`, `_run_ops`,
`_mismatches`. Imports added: `random`, `rebuild_learner_state`.

Copied content: the three test bodies, the section comment above them, and the
helpers `QUEST`, `_events`, `_lesson_at` and `_two_assignments` are
byte-for-byte the same as `test_active_state_path.py:20, 23-24, 31-32,
88-162`. Checked with `diff` (both diffs empty, output below). Only the module
docstring and imports differ: imports are cut down to what the file uses
(`datetime`, `timezone`, `Learner`, `LearnerQuestProjection`). `_complete`
was not copied because the quest tests do not use it. The original file was
not edited.

## Exact commands and output

All commands use `.venv/bin/python` (the system `python` has no
`eventsourcing`). `-p no:cacheprovider` keeps pytest from writing
`.pytest_cache`.

### Step 3: baseline of the active-path and existing quest tests

```bash
.venv/bin/python -m pytest tests/state_machine/test_active_state_path.py tests/state_machine/test_quest.py tests/state_machine/test_replay_no_decide.py tests/state_machine/test_live_vs_replay.py -v -p no:cacheprovider
```

```
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0 -- /home/sampadram/Sampadram/IITH/SEM 7/DATGC/project/gamification-engine/.venv/bin/python
rootdir: /home/sampadram/Sampadram/IITH/SEM 7/DATGC/project/gamification-engine
configfile: pyproject.toml
collecting ... collected 28 items

tests/state_machine/test_active_state_path.py::test_streak_projection_matches_aggregate_for_late_oct1 PASSED [  3%]
tests/state_machine/test_active_state_path.py::test_streak_projection_counts_freeze_acquired_after_latest_activity PASSED [  7%]
tests/state_machine/test_active_state_path.py::test_streak_projection_is_idempotent_when_events_are_delivered_twice PASSED [ 10%]
tests/state_machine/test_active_state_path.py::test_quest_projection_keeps_repeated_assignments_separate FAILED [ 14%]
tests/state_machine/test_active_state_path.py::test_quest_projection_marks_overdue_assignment_expired_without_new_lesson FAILED [ 17%]
tests/state_machine/test_active_state_path.py::test_quest_projection_applies_expiry_events FAILED [ 21%]
tests/state_machine/test_active_state_path.py::test_fresh_lesson_tagged_to_completed_quest_is_accepted PASSED [ 25%]
tests/state_machine/test_active_state_path.py::test_resent_completion_tagged_to_completed_quest_is_still_a_duplicate PASSED [ 28%]
tests/state_machine/test_quest.py::test_quest_assignment_is_idempotent PASSED [ 32%]
tests/state_machine/test_quest.py::test_quest_completion_lifecycle PASSED [ 35%]
tests/state_machine/test_quest.py::test_explicit_assignment_can_complete_after_same_quest_completed PASSED [ 39%]
tests/state_machine/test_quest.py::test_quest_expiry_is_independent_per_assignment PASSED [ 42%]
tests/state_machine/test_quest.py::test_quest_projection_and_state_rebuild PASSED [ 46%]
tests/state_machine/test_quest.py::test_unassigned_learner_does_not_complete_quest PASSED [ 50%]
tests/state_machine/test_quest.py::test_quest_expiry_is_durable_across_restart_and_rebuild PASSED [ 53%]
tests/state_machine/test_replay_no_decide.py::test_replay_does_not_call_decision_functions PASSED [ 57%]
tests/state_machine/test_replay_no_decide.py::test_rebuild_creates_zero_new_events PASSED [ 60%]
tests/state_machine/test_live_vs_replay.py::test_rebuilding_b_keeps_a_projection PASSED [ 64%]
tests/state_machine/test_live_vs_replay.py::test_late_oct1_live_streak_matches_replay PASSED [ 67%]
tests/state_machine/test_live_vs_replay.py::test_freeze_after_latest_activity_survives_rebuild PASSED [ 71%]
tests/state_machine/test_live_vs_replay.py::test_reversed_seven_day_history_awards_badge_live_and_on_replay PASSED [ 75%]
tests/state_machine/test_live_vs_replay.py::test_two_assignments_of_same_quest_progress_independently PASSED [ 78%]
tests/state_machine/test_live_vs_replay.py::test_activity_before_assignment_does_not_count PASSED [ 82%]
tests/state_machine/test_live_vs_replay.py::test_assigned_quest_live_state_equals_replay_with_shuffled_lessons PASSED [ 85%]
tests/state_machine/test_live_vs_replay.py::test_live_state_equals_replay_and_ignores_arrival_order[days0-None] PASSED [ 89%]
tests/state_machine/test_live_vs_replay.py::test_live_state_equals_replay_and_ignores_arrival_order[days1-None] PASSED [ 92%]
tests/state_machine/test_live_vs_replay.py::test_live_state_equals_replay_and_ignores_arrival_order[days2-2026-10-02] PASSED [ 96%]
tests/state_machine/test_live_vs_replay.py::test_live_state_equals_replay_and_ignores_arrival_order[days3-2026-10-05] PASSED [100%]

=================================== FAILURES ===================================
__________ test_quest_projection_keeps_repeated_assignments_separate ___________

    def test_quest_projection_keeps_repeated_assignments_separate():
        learner = _two_assignments()
        assert learner.quest_instances["a1"]["status"] == "completed"
        assert learner.quest_instances["a2"]["status"] == "assigned"
    
        projection = LearnerQuestProjection()
        for event in _events(learner):
            projection.apply(event)
        summary = projection.get_summary(learner.id)
    
>       assert set(summary.assignments) == {"a1", "a2"}
                   ^^^^^^^^^^^^^^^^^^^
E       AttributeError: 'LearnerQuestSummary' object has no attribute 'assignments'

tests/state_machine/test_active_state_path.py:117: AttributeError
__ test_quest_projection_marks_overdue_assignment_expired_without_new_lesson ___

    def test_quest_projection_marks_overdue_assignment_expired_without_new_lesson():
        learner = _two_assignments()
        projection = LearnerQuestProjection()
        for event in _events(learner):
            projection.apply(event)
    
>       before_deadline = projection.get_summary(
            learner.id, as_of=datetime(2026, 10, 11, tzinfo=timezone.utc)
        )
E       TypeError: LearnerQuestProjection.get_summary() got an unexpected keyword argument 'as_of'

tests/state_machine/test_active_state_path.py:137: TypeError
_________________ test_quest_projection_applies_expiry_events __________________

    def test_quest_projection_applies_expiry_events():
        learner = Learner()
        learner.assign_quest(
            QUEST, assignment_id="a1", assigned_at="2026-10-01T00:00:00Z",
            deadline_at="2026-10-03T00:00:00Z",
        )
        _lesson_at(learner, "late", "2026-10-09T10:00:00Z")  # past deadline -> expiry event
        projection = LearnerQuestProjection()
        for event in _events(learner):
            projection.apply(event)
        summary = projection.get_summary(learner.id)
        assert learner.quest_instances["a1"]["status"] == "expired"
>       assert summary.assignments["a1"].status == "expired"
               ^^^^^^^^^^^^^^^^^^^
E       AttributeError: 'LearnerQuestSummary' object has no attribute 'assignments'

tests/state_machine/test_active_state_path.py:161: AttributeError
=========================== short test summary info ============================
FAILED tests/state_machine/test_active_state_path.py::test_quest_projection_keeps_repeated_assignments_separate
FAILED tests/state_machine/test_active_state_path.py::test_quest_projection_marks_overdue_assignment_expired_without_new_lesson
FAILED tests/state_machine/test_active_state_path.py::test_quest_projection_applies_expiry_events
========================= 3 failed, 25 passed in 0.23s =========================
```

Summary: 28 collected, **3 failed, 25 passed**. The only failures are the 3
quest-projection tests. All of `test_quest.py`, `test_replay_no_decide.py` and
`test_live_vs_replay.py` pass, and so do the 3 streak and 2 ingestion tests
in `test_active_state_path.py`.

Note on the failures: each test stops at its first missing-API line, so the
assertions after it (`assigned_quests == (QUEST, QUEST)`, the `as_of`
expiry, `completed_quests == ()`) have not run yet. More failures may show up
behind them once `assignments` / `as_of` exist (see "Uncertainty").

### Step 4: copy check and new file

```bash
diff <(sed -n 88,162p tests/state_machine/test_active_state_path.py) <(sed -n 22,96p tests/state_machine/test_quest_reader.py) && echo IDENTICAL_TESTS
diff <(sed -n 23,24p tests/state_machine/test_active_state_path.py; sed -n 31,32p tests/state_machine/test_active_state_path.py) <(sed -n 14,15p tests/state_machine/test_quest_reader.py; sed -n 18,19p tests/state_machine/test_quest_reader.py) && echo IDENTICAL_HELPERS
```

```
IDENTICAL_TESTS
IDENTICAL_HELPERS
```

```bash
.venv/bin/python -m pytest tests/state_machine/test_quest_reader.py -v -p no:cacheprovider
```

(Filtered to PASSED/FAILED/`E`/summary lines. The tracebacks are the same as
in Step 3, with the file name changed.)

```
tests/state_machine/test_quest_reader.py::test_quest_projection_keeps_repeated_assignments_separate FAILED [ 33%]
tests/state_machine/test_quest_reader.py::test_quest_projection_marks_overdue_assignment_expired_without_new_lesson FAILED [ 66%]
tests/state_machine/test_quest_reader.py::test_quest_projection_applies_expiry_events FAILED [100%]
E       AttributeError: 'LearnerQuestSummary' object has no attribute 'assignments'
E       TypeError: LearnerQuestProjection.get_summary() got an unexpected keyword argument 'as_of'
E       AttributeError: 'LearnerQuestSummary' object has no attribute 'assignments'
FAILED tests/state_machine/test_quest_reader.py::test_quest_projection_keeps_repeated_assignments_separate
FAILED tests/state_machine/test_quest_reader.py::test_quest_projection_marks_overdue_assignment_expired_without_new_lesson
FAILED tests/state_machine/test_quest_reader.py::test_quest_projection_applies_expiry_events
============================== 3 failed in 0.06s ===============================
```

### Full suite after Step 4

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider
```

```
FAILED tests/state_machine/test_active_state_path.py::test_quest_projection_keeps_repeated_assignments_separate
FAILED tests/state_machine/test_active_state_path.py::test_quest_projection_marks_overdue_assignment_expired_without_new_lesson
FAILED tests/state_machine/test_active_state_path.py::test_quest_projection_applies_expiry_events
FAILED tests/state_machine/test_quest_reader.py::test_quest_projection_keeps_repeated_assignments_separate
FAILED tests/state_machine/test_quest_reader.py::test_quest_projection_marks_overdue_assignment_expired_without_new_lesson
FAILED tests/state_machine/test_quest_reader.py::test_quest_projection_applies_expiry_events
6 failed, 130 passed, 9 skipped in 1.55s
```

Compared with the end of Fix 2 (3 failed, 130 passed, 9 skipped): the 3 new
failures are the copies in `test_quest_reader.py`. Passed and skipped counts
are unchanged.

### Git (read-only)

```bash
git status --short
```

```
?? tests/state_machine/test_quest_reader.py
```

(Run before this audit file was written; it is now also untracked.) HEAD is
`0b59873`. `src/` is clean.

### Step 5: after rewriting the quest reader

```bash
.venv/bin/python -m pytest tests/state_machine/test_quest_reader.py -v -p no:cacheprovider
```

```
collecting ... collected 3 items

tests/state_machine/test_quest_reader.py::test_quest_projection_keeps_repeated_assignments_separate PASSED [ 33%]
tests/state_machine/test_quest_reader.py::test_quest_projection_marks_overdue_assignment_expired_without_new_lesson FAILED [ 66%]
tests/state_machine/test_quest_reader.py::test_quest_projection_applies_expiry_events PASSED [100%]

=================================== FAILURES ===================================
__ test_quest_projection_marks_overdue_assignment_expired_without_new_lesson ___

    def test_quest_projection_marks_overdue_assignment_expired_without_new_lesson():
        learner = _two_assignments()
        projection = LearnerQuestProjection()
        for event in _events(learner):
            projection.apply(event)
    
>       before_deadline = projection.get_summary(
            learner.id, as_of=datetime(2026, 10, 11, tzinfo=timezone.utc)
        )
E       TypeError: LearnerQuestProjection.get_summary() got an unexpected keyword argument 'as_of'

tests/state_machine/test_quest_reader.py:71: TypeError
=========================== short test summary info ============================
FAILED tests/state_machine/test_quest_reader.py::test_quest_projection_marks_overdue_assignment_expired_without_new_lesson
========================= 1 failed, 2 passed in 0.05s ==========================
```

(Header lines `platform` / `rootdir` / `configfile` are the same as in Step 3.)

```bash
.venv/bin/python -m pytest tests/state_machine/test_quest.py tests/state_machine/test_replay_no_decide.py tests/state_machine/test_live_vs_replay.py -v -p no:cacheprovider
```

```
collecting ... collected 20 items

tests/state_machine/test_quest.py::test_quest_assignment_is_idempotent PASSED [  5%]
tests/state_machine/test_quest.py::test_quest_completion_lifecycle PASSED [ 10%]
tests/state_machine/test_quest.py::test_explicit_assignment_can_complete_after_same_quest_completed PASSED [ 15%]
tests/state_machine/test_quest.py::test_quest_expiry_is_independent_per_assignment PASSED [ 20%]
tests/state_machine/test_quest.py::test_quest_projection_and_state_rebuild PASSED [ 25%]
tests/state_machine/test_quest.py::test_unassigned_learner_does_not_complete_quest PASSED [ 30%]
tests/state_machine/test_quest.py::test_quest_expiry_is_durable_across_restart_and_rebuild PASSED [ 35%]
tests/state_machine/test_replay_no_decide.py::test_replay_does_not_call_decision_functions PASSED [ 40%]
tests/state_machine/test_replay_no_decide.py::test_rebuild_creates_zero_new_events PASSED [ 45%]
tests/state_machine/test_live_vs_replay.py::test_rebuilding_b_keeps_a_projection PASSED [ 50%]
tests/state_machine/test_live_vs_replay.py::test_late_oct1_live_streak_matches_replay PASSED [ 55%]
tests/state_machine/test_live_vs_replay.py::test_freeze_after_latest_activity_survives_rebuild PASSED [ 60%]
tests/state_machine/test_live_vs_replay.py::test_reversed_seven_day_history_awards_badge_live_and_on_replay PASSED [ 65%]
tests/state_machine/test_live_vs_replay.py::test_two_assignments_of_same_quest_progress_independently PASSED [ 70%]
tests/state_machine/test_live_vs_replay.py::test_activity_before_assignment_does_not_count PASSED [ 75%]
tests/state_machine/test_live_vs_replay.py::test_assigned_quest_live_state_equals_replay_with_shuffled_lessons PASSED [ 80%]
tests/state_machine/test_live_vs_replay.py::test_live_state_equals_replay_and_ignores_arrival_order[days0-None] PASSED [ 85%]
tests/state_machine/test_live_vs_replay.py::test_live_state_equals_replay_and_ignores_arrival_order[days1-None] PASSED [ 90%]
tests/state_machine/test_live_vs_replay.py::test_live_state_equals_replay_and_ignores_arrival_order[days2-2026-10-02] PASSED [ 95%]
tests/state_machine/test_live_vs_replay.py::test_live_state_equals_replay_and_ignores_arrival_order[days3-2026-10-05] PASSED [100%]

============================== 20 passed in 0.13s ==============================
```

All 20 stay green.

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider
```

```
FAILED tests/state_machine/test_active_state_path.py::test_quest_projection_marks_overdue_assignment_expired_without_new_lesson
FAILED tests/state_machine/test_quest_reader.py::test_quest_projection_marks_overdue_assignment_expired_without_new_lesson
2 failed, 134 passed, 9 skipped in 1.49s
```

Compared with the end of Step 4 (6 failed, 130 passed, 9 skipped): 4 tests
moved from failed to passed (repeated-assignments and expiry-events, in both
files). Nothing that passed before fails now. Skips unchanged.

Scratch order/redelivery check (not a repo test):

```bash
```

```
quest events: ['QuestAssigned', 'QuestProgressed', 'QuestProgressed', 'QuestProgressed', 'QuestCompleted', 'QuestAssigned', 'QuestExpired']
5040 permutations: 0 differ; 500 shuffled+redelivered: 0 differ
{'a1': ('completed', 3), 'a2': ('expired', 0)}
```

Scenario: `a1` assigned and completed with 3 lessons; `a2` assigned with a
1-day deadline and expired by a late lesson. All 7! orders of the 7 quest
events, and 500 random shuffles with 4 extra redelivered events each, give the
same summary.

```bash
git status --short
git diff --stat
```

```
 M src/state_machine/projection.py
?? docs/audit/week4-fix3-quest-reader.md
?? tests/state_machine/test_quest_reader.py
 src/state_machine/projection.py | 112 ++++++++++++++++++++++++++--------------
 1 file changed, 73 insertions(+), 39 deletions(-)
```

### Step 6: `as_of` and edge cases

```bash
.venv/bin/python -m pytest tests/state_machine/test_quest_reader.py -v -p no:cacheprovider
```

```
collecting ... collected 10 items

tests/state_machine/test_quest_reader.py::test_quest_projection_keeps_repeated_assignments_separate PASSED [ 10%]
tests/state_machine/test_quest_reader.py::test_quest_projection_marks_overdue_assignment_expired_without_new_lesson PASSED [ 20%]
tests/state_machine/test_quest_reader.py::test_quest_projection_applies_expiry_events PASSED [ 30%]
tests/state_machine/test_quest_reader.py::test_as_of_equal_to_deadline_is_still_assigned PASSED [ 40%]
tests/state_machine/test_quest_reader.py::test_progress_arriving_before_its_assignment PASSED [ 50%]
tests/state_machine/test_quest_reader.py::test_same_events_delivered_twice_give_same_summary PASSED [ 60%]
tests/state_machine/test_quest_reader.py::test_every_arrival_order_of_one_assignment_gives_same_summary[_completed_assignment-completed-3] PASSED [ 70%]
tests/state_machine/test_quest_reader.py::test_every_arrival_order_of_one_assignment_gives_same_summary[_expired_assignment-expired-1] PASSED [ 80%]
tests/state_machine/test_quest_reader.py::test_two_learners_do_not_affect_each_other PASSED [ 90%]
tests/state_machine/test_quest_reader.py::test_learner_with_no_quests_returns_empty_summary PASSED [100%]

============================== 10 passed in 0.06s ==============================
```

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider
```

```
........................................................................ [ 94%]
........                                                                 [100%]
143 passed, 9 skipped in 1.50s
```

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider tests/state_machine/test_active_state_path.py
```

```
........                                                                 [100%]
8 passed in 0.13s
```

No failures are left. Compared with the end of Step 5 (2 failed, 134 passed,
9 skipped): the 2 `as_of` copies now pass, plus 7 new tests (6 functions, one
parametrized ×2) = 143. Skips unchanged. All 3 originals in
`test_active_state_path.py` pass, so nothing is still failing there.

Events each permutation case covers (`.venv/bin/python -c ...`):

```
_completed_assignment ['QuestAssigned', 'QuestProgressed', 'QuestProgressed', 'QuestProgressed', 'QuestCompleted']
_expired_assignment ['QuestAssigned', 'QuestProgressed', 'QuestExpired']
```

Scratch mutation check (not a repo test). It monkeypatches the projection
with known-bad rules and runs the new tests directly:

```bash
```

```
unmutated                          failing: []
status: last write wins            failing: []
as_of: >= instead of >             failing: ['deadline_boundary']
QuestAssigned resets progress      failing: ['perm[completed]', 'perm[expired]', 'progress_first']
```

- The boundary test catches `>=`.
- The permutation and progress-first tests catch a `QuestAssigned` that
  overwrites progress.
- **No test catches last-write-wins status.** See "Uncertainty".

```bash
git status --short
git diff --stat
```

```
 M src/state_machine/projection.py
?? docs/audit/week4-fix3-quest-reader.md
?? tests/state_machine/test_quest_reader.py
 src/state_machine/projection.py | 132 +++++++++++++++++++++++++++-------------
 1 file changed, 91 insertions(+), 41 deletions(-)
```

### Step 7: aggregate cross-check and divergence regression

No `src/` changes in this step.

#### Generator (`_random_ops`)

Each sequence:
- 1–3 `assign_quest` calls with distinct ids `a0..a2`. Rules v1 registers
  only `lesson_starter_v1`, so every assignment uses that quest id, and any
  sequence with 2 or more assignments repeats it.
- `assigned_at` = 2026-10-01 + 0–6 days (hour resolution).
- `deadline_at`: 25% `None` (aggregate default, +7 days), otherwise
  `assigned_at` + 1–96 hours.
- 0–8 `complete_lesson` calls (`occurred_at`, `timezone="UTC"`). Each lesson
  picks a random assignment's window:
  - 10% exactly on its deadline
  - 45% uniformly inside the window
  - 45% uniformly in [Sep 30, Oct 15], which lands before assignment, inside
    or after deadlines
- All ops are shuffled together. That puts lessons before some or all
  `assign_quest` calls, and in no particular time order.

**First version was too weak.** The first generator drew every non-deadline
lesson uniformly. It passed, but only **5 of 582** assignments reached
`completed`, because 3 in-window lessons were rare. I added the 45%
"inside a window" branch. After that, 39 assignments complete, and the test
still passes. Only the test generator changed.

#### Coverage (scratch script, final generator)

```bash
```

```
assignments                              576
lesson_x_assign: after_deadline          704
lesson_x_assign: before_assigned         435
lesson_x_assign: exactly_deadline        127
lesson_x_assign: inside_window           920
lessons                                  1142
seq_default_deadline                     115
seq_lesson_before_first_assign_call      175
seq_lessons_out_of_time_order            196
seq_with_1_assignments                   118
seq_with_2_assignments                   88
seq_with_3_assignments                   94
status_assigned                          322
status_completed                         39
status_expired                           215
mutation 'ignore QuestExpired': caught -> 141/300 sequences disagree. Shortest: ([('assign', 'a0', datetime.datetime(2026, 10, 3, 0, 0, tzinfo=datetime.timezone.utc), datetime.datetime(2026, 10, 5, 7, 0
```

(`lesson_x_assign` counts each lesson against each assignment of its
sequence, so the 4 rows add up to more than `lessons`.)

Sensitivity check (inline scratch, monkeypatching `apply`):

```
drop completed_at: caught -> 36/300 sequences disagree
ignore QuestCompleted: caught -> 36/300 sequences disagree
```

So the cross-check fails if the reader ignores expiry, ignores completion or
loses `completed_at`.

#### Result

**No mismatch.** All 300 sequences agree on assignment ids, status, progress
and `completed_at`. There is no shortest failing sequence to record, and the
aggregate was not touched.

#### Commands and output

```bash
.venv/bin/python -m pytest tests/state_machine/test_quest_reader.py -v -p no:cacheprovider
```

(Filtered to PASSED/FAILED/summary lines.)

```
tests/state_machine/test_quest_reader.py::test_quest_projection_keeps_repeated_assignments_separate PASSED [  8%]
tests/state_machine/test_quest_reader.py::test_quest_projection_marks_overdue_assignment_expired_without_new_lesson PASSED [ 16%]
tests/state_machine/test_quest_reader.py::test_quest_projection_applies_expiry_events PASSED [ 25%]
tests/state_machine/test_quest_reader.py::test_as_of_equal_to_deadline_is_still_assigned PASSED [ 33%]
tests/state_machine/test_quest_reader.py::test_progress_arriving_before_its_assignment PASSED [ 41%]
tests/state_machine/test_quest_reader.py::test_same_events_delivered_twice_give_same_summary PASSED [ 50%]
tests/state_machine/test_quest_reader.py::test_every_arrival_order_of_one_assignment_gives_same_summary[_completed_assignment-completed-3] PASSED [ 58%]
tests/state_machine/test_quest_reader.py::test_every_arrival_order_of_one_assignment_gives_same_summary[_expired_assignment-expired-1] PASSED [ 66%]
tests/state_machine/test_quest_reader.py::test_two_learners_do_not_affect_each_other PASSED [ 75%]
tests/state_machine/test_quest_reader.py::test_learner_with_no_quests_returns_empty_summary PASSED [ 83%]
tests/state_machine/test_quest_reader.py::test_projection_matches_aggregate_on_random_histories PASSED [ 91%]
tests/state_machine/test_quest_reader.py::test_rebuild_completed_quests_is_distinct_but_projection_is_per_assignment PASSED [100%]
============================== 12 passed in 0.15s ==============================
```

(That run used the first generator. The rerun after strengthening it gave
`12 passed in 0.14s`.)

## Final verification

### Test counts

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider
```

```
........................................................................ [ 93%]
..........                                                               [100%]
145 passed, 9 skipped in 1.59s
```

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider tests/state_machine/test_quest_reader.py tests/state_machine/test_active_state_path.py tests/state_machine/test_quest.py tests/state_machine/test_replay_no_decide.py tests/state_machine/test_live_vs_replay.py
```

```
40 passed in 0.32s
```

| Scope | Passed | Failed | Skipped |
|---|---|---|---|
| Full suite | **145** | **0** | **9** |
| Quest-related files (above) | 40 | 0 | 0 |

How the counts add up: at the end of Fix 2 the suite was 130 passed, 3
failed, 9 skipped (133 run + 9 skipped). The 3 quest tests in
`test_active_state_path.py` now pass (133), plus 12 new tests in
`test_quest_reader.py` = 145. The 9 skips are unchanged. No test is marked
`xfail`, and no previously passing test fails.

### Git (read-only)

```bash
git status --short
git diff --stat
git stash list
git log --oneline -1
```

```
 M src/state_machine/projection.py
?? docs/audit/week4-fix3-quest-reader.md
?? tests/state_machine/test_quest_reader.py
---
 src/state_machine/projection.py | 132 +++++++++++++++++++++++++++-------------
 1 file changed, 91 insertions(+), 41 deletions(-)
---
(no stashes)
0b59873 fix(m2): make streak reader and aggregate freeze balance order-independent
```

Changed or created by this fix, relative to `0b59873`:

| File | Status |
|---|---|
| `src/state_machine/projection.py` | modified (+91 / −41) |
| `tests/state_machine/test_quest_reader.py` | new (364 lines) |
| `docs/audit/week4-fix3-quest-reader.md` | new |

Unrelated local changes: **none.** The working tree was clean at the start
of this fix. A `.pytest_cache/` directory exists but is ignored by git and
does not appear in `git status`. The scratch scripts are in the session
scratchpad outside the repo. Nothing was committed, staged or stashed.

### Suggested commit (not made)

Files:

```
src/state_machine/projection.py
tests/state_machine/test_quest_reader.py
docs/audit/week4-fix3-quest-reader.md
```

Message (no trailer):

```
fix(m2): keep quest reader per assignment and agree with the aggregate

LearnerQuestProjection kept sets of quest ids, ignored QuestExpired and
dropped assigned_at/deadline_at, so two assignments of the same quest
collapsed into one and the reader could not report status or deadlines.

Key the reader by assignment_id with a frozen AssignmentRecord
(quest_id, status, progress, assigned_at, deadline_at, completed_at).
All four quest events create a record lazily; progress keeps the
highest step, completed_at the earliest value, and status uses
completed > expired > assigned, so arrival order and redelivery cannot
change the result. LearnerQuestSummary gains an assignments field;
assigned_quests and completed_quests are now one entry per assignment.

get_summary takes an optional as_of: an assigned record whose deadline
is strictly before as_of is reported as expired. This is read-time
only; nothing is stored and datetime.now is never called.

Add tests/state_machine/test_quest_reader.py (12 tests: the three
copied quest-reader tests, edge cases, a seeded 300-sequence
cross-check against Learner.quest_instances, and a regression pinning
the known completed_quests divergence from rebuild_learner_state).
Details in docs/audit/week4-fix3-quest-reader.md.

Out of scope: rebuild_learner_state still returns a distinct set of
completed quest ids.
```

## Assumptions

- **`assigned_quests` semantics change. Decided (Step 5):** one entry per
  assignment, sorted by `(quest_id, assignment_id)`. The existing passing
  tests use a single assignment, so they still get `("lesson_starter_v1",)`.
- **`completed_quests`. Decided (Step 5):** one entry per assignment with
  status `completed`, same order. A quest completed by two assignments
  appears twice.
- **`quest_progress` stays keyed by `assignment_id`.** Both the existing
  tests (`{"quest:lesson_starter_v1": 3}`, the default assignment id) and the
  new test (`{"a1": 3, "a2": 1}`) already agree with that.
- **`as_of` default. Done (Step 6):** with no `as_of`, `get_summary` reports
  only event-derived status (no time-based expiry). Test 3 relies only on
  the `QuestExpired` event, not on `as_of`.
- **Deadline comparison. Done (Step 6):** strict `as_of > deadline_at`, only
  for `status == "assigned"`, matching the aggregate. Pinned by test 4.
- `deadline_at` on the summary is a timezone-aware `datetime`, as the test
  compares it with `datetime(2026, 10, 12, tzinfo=timezone.utc)`. The
  aggregate already parses it with `_as_datetime`.
- Cross-check: ground truth is `learner.quest_instances` (the aggregate) with
  `as_of=None`. Compared fields are the assignment-id set, `status`,
  `progress` and `completed_at`, as asked. `assigned_at` / `deadline_at` are
  covered by the copied test and test 5, not by the cross-check.
- Tests run with `.venv/bin/python` and `-p no:cacheprovider`. The system
  `python` has no `eventsourcing`.

## Uncertainty

- **`completed_quests` divergence (known, out of scope).**
  `rebuild_learner_state(...).completed_quests` is
  `tuple(sorted({quest_id ...}))`, a distinct set. The projection's
  `completed_quests` has one entry per completed assignment. With two
  completed assignments of `lesson_starter_v1`, rebuild gives `(QUEST,)` and
  the projection gives `(QUEST, QUEST)`. Pinned by test 11 so a later change
  to either side is visible. Callers that need "which quests were ever
  completed" should dedupe, or use `rebuild_learner_state`. Callers that need
  per-assignment detail should use `assignments`.
- **Time-based expiry is read-time only.** `get_summary(..., as_of=...)`
  reports an overdue `assigned` record as `expired` in the returned summary
  only. Nothing is stored and no `QuestExpired` event is written. The
  aggregate's `quest_instances` keeps saying `assigned` until a lesson
  arrives after the deadline. So `as_of` views and the aggregate (and the
  cross-check, which uses `as_of=None`) intentionally differ for overdue,
  untouched assignments. A caller that wants durable expiry needs a separate
  command that emits `QuestExpired`.
- **Cross-check limits.** One seed, 300 sequences, 1–3 assignments, 0–8
  lessons, hour resolution (plus exact-deadline and uniform-in-window
  instants). Only rules `v1`, which has a single quest id, so "different
  quests" is never exercised. Only 39 of 576 assignments complete. There is
  no duplicate delivery or reordering of events in the cross-check itself;
  tests 6–7 cover that separately.
- **Status precedence is not pinned by any repo test (Step 6).** The
  aggregate skips any assignment whose status is not `assigned`
  (`learner.py:170`), so it never emits both `QuestCompleted` and
  `QuestExpired` for one assignment. Every real single-assignment history
  has at most one status event, so the permutation tests can't tell
  `completed > expired` from last-write-wins (mutation check above). The rule
  only matters for histories the aggregate can't produce today (e.g. two
  writers). Pinning it would need a test with a synthetic pair of events.
  Not added, because it wasn't in the requested list.
- **Hidden assertions (baseline note, resolved).** At baseline each copied
  test stopped at its first missing attribute. All of their assertions run
  and pass now.
- **Can `deadline_at` be empty/None? Resolved (Step 5).**
  `Learner.assign_quest` (`src/state_machine/learner.py:352`) defaults it to
  `assigned_at + 7 days`, so every `QuestAssigned` event carries a deadline.
  A record created lazily (progress/completion/expiry before its
  `QuestAssigned`) has `deadline_at=None` until the assignment arrives, so the
  `as_of` logic treats `None` as "no deadline" (done in Step 6, pinned by
  `test_progress_arriving_before_its_assignment`).
- **Aggregate expiry boundary (resolved in Step 6).** The aggregate expires
  when `occurred > deadline` (strict, `learner.py:177`). `as_of` uses the same
  strict comparison, pinned by `test_as_of_equal_to_deadline_is_still_assigned`.
- **`completed_at` on multiple `QuestCompleted`.** The aggregate emits at most
  one per assignment. The reader keeps the earliest non-`None` value so the
  result stays order-independent if two ever arrived. Not covered by a repo
  test.
- **Lazy record before `QuestAssigned`.** Its `assigned_at` / `deadline_at`
  are `None` and it is already listed in `assigned_quests`. Covered since
  Step 6 by `test_progress_arriving_before_its_assignment` and the
  permutation tests.
- **Duplicate-delivery test scope.** Test 6 duplicates the whole list once.
  The Step 5 scratch check also covered random partial redelivery. Neither
  duplicates an event with a freeze or reward side effect, because the quest
  reader has none.
- **Duplicate delivery and ordering (baseline note, resolved in Step 6).**
  Covered by `test_same_events_delivered_twice_give_same_summary` and
  `test_every_arrival_order_of_one_assignment_gives_same_summary`. A late
  `QuestProgressed` with a lower `current_step` cannot lower progress,
  because progress keeps the highest step. The permutation test delivers
  steps 1–3 in every order. `completed` / `expired` being terminal is **not**
  pinned by a repo test (see the "Status precedence" bullet).
- **`quest_summaries` internal shape.** No reader outside the class, per the
  grep, but it is a public attribute name. Changing its shape is assumed safe.
