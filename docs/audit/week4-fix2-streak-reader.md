# Week 4 Fix 2: Streak reader disagrees with the aggregate and the pure fold

Status: **done. Not committed.** The streak reader, the pure fold and the
`Learner` aggregate now agree on `new_streak` and `freezes_remaining` for any
arrival order and for duplicate delivery. The aggregate bug the cross-check
found (`freezes_remaining` going stale after an earlier-date correction
consumes a freeze) is fixed in Step 5. `test_streak_reader.py` passes 14/14.
The only failures in the full suite are the 3 quest tests in
`test_active_state_path.py`, which already existed.

The numbered sections below are a chronological log. Earlier sections keep the
output from that point in time. "Final verification" has the current state.

## Summary

| What | Where | Change |
|---|---|---|
| New pure helper `fold_streak_and_freezes(streak_events, freeze_events) -> (new_streak, freezes_remaining)` | `src/state_machine/projection.py` | Highest revision per `activity_date` wins (first seen on equal revisions). Streak = the winner for the latest date. Freezes = distinct `freeze_id` minus `freezes_consumed` of the winners. No events gives `(0, 0)`. The docstring says order independence holds when each (activity_date, revision) pair is unique, which the aggregate guarantees. |
| `LearnerStreakProjection` | `src/state_machine/projection.py` | Now an incremental form of the same fold. Per learner it keeps the winners and one `FreezeAcquired` per `freeze_id`. Handles `StreakUpdated` and `FreezeAcquired`. Recomputes the summary through the helper after each event. Public interface unchanged. |
| `rebuild_learner_state` | `src/state_machine/projection.py` | Streak and freeze values now come from the helper. XP, activity_dates, quests and badges are untouched. |
| `Learner._update_streak` | `src/state_machine/learner.py` | `freezes_remaining` is recomputed from `freeze_acquisitions` and `streak_outcomes` after every outcome, not only for events on or after the latest date. The redundant earlier assignment was removed. `new_streak` / `last_activity_date` handling is unchanged (Step 5). |
| New tests (14) | `tests/state_machine/test_streak_reader.py` | 3 copied, 2 order/duplicate tests, 6 edge cases, 1 seeded cross-check, 1 minimal aggregate regression, 1 aggregate invariant |
| Audit log | `docs/audit/week4-fix2-streak-reader.md` | this file |

Final suite counts (details in "Final verification"):

| Scope | Result |
|---|---|
| Excluding `test_active_state_path.py` (to compare with the 111 passed / 9 skipped baseline) | **125 passed, 0 failed, 9 skipped**. 111 baseline + 14 new. All pass. |
| Full suite | **130 passed, 3 failed, 9 skipped**. The 3 failures are the quest-projection tests in `test_active_state_path.py`, which already existed. |

Known out-of-scope issues:
- `XPAwarded` is not deduplicated in `rebuild_learner_state`: `xp_total`
  doubles (300 to 600) when events are delivered twice.
- `LearnerQuestProjection` lacks the `assignments` / `as_of` API that the 3
  quest tests in `test_active_state_path.py` expect.

## Goal

`LearnerStreakProjection` (the streak reader that serves live state) must give
the same `new_streak` and `freezes_remaining` as the `Learner` aggregate and as
`rebuild_learner_state()`, no matter:

- what order the events arrive in (late completion for an earlier day, freeze
  acquired after the last activity, any permutation), or
- whether events are delivered more than once.

`rebuild_learner_state()` must also give the same `freezes_remaining` when the
same events are delivered twice.

## Files changed

| File | Status | Change |
|---|---|---|
| `src/state_machine/projection.py` | modified (+62 / −22) | new `fold_streak_and_freezes`; `LearnerStreakProjection.__init__/apply/clear/rebuild`; `rebuild_learner_state` (streak/freeze part and docstring only) |
| `src/state_machine/learner.py` | modified (+4 / −8) | `Learner._update_streak` only (Step 5) |
| `tests/state_machine/test_streak_reader.py` | new | 14 tests |
| `docs/audit/week4-fix2-streak-reader.md` | new | this audit log |

Not touched: `tests/state_machine/test_active_state_path.py` (the three streak
tests were copied, not moved), the quest tests, `Learner._acquire_freeze` and
every other file.

## Users of `streak_summaries` / `LearnerStreakProjection`

Command:

```bash
grep -rn "streak_summaries\|LearnerStreakProjection" src tests
```

Output:

```
src/state_machine/projection.py:52:class LearnerStreakProjection:
src/state_machine/projection.py:56:        self.streak_summaries: dict[Any, LearnerStreakSummary] = {}
src/state_machine/projection.py:62:            self.streak_summaries[learner_id] = LearnerStreakSummary(
src/state_machine/projection.py:69:        self.streak_summaries.clear()
src/state_machine/projection.py:73:        staged = LearnerStreakProjection()
src/state_machine/projection.py:76:        self.streak_summaries = staged.streak_summaries
tests/state_machine/test_streak_updates.py:5:    LearnerStreakProjection,
tests/state_machine/test_streak_updates.py:187:    """LearnerStreakProjection reconstructs exact streak and freeze summary after wipe."""
tests/state_machine/test_streak_updates.py:202:        projection = LearnerStreakProjection()
tests/state_machine/test_streak_updates.py:205:        summary = projection.streak_summaries[learner.id]
tests/state_machine/test_replay_no_decide.py:13:    LearnerStreakProjection,
tests/state_machine/test_replay_no_decide.py:86:        streak_proj = LearnerStreakProjection()
tests/state_machine/test_replay_no_decide.py:88:        summary = streak_proj.streak_summaries[learner_id]
tests/state_machine/test_active_state_path.py:3:Covers: LearnerStreakProjection (arrival order / freezes), LearnerQuestProjection
tests/state_machine/test_active_state_path.py:16:    LearnerStreakProjection,
tests/state_machine/test_active_state_path.py:46:    projection = LearnerStreakProjection()
tests/state_machine/test_active_state_path.py:49:    summary = projection.streak_summaries[learner.id]
tests/state_machine/test_active_state_path.py:65:    projection = LearnerStreakProjection()
tests/state_machine/test_active_state_path.py:68:    summary = projection.streak_summaries[learner.id]
tests/state_machine/test_active_state_path.py:81:    projection = LearnerStreakProjection()
tests/state_machine/test_active_state_path.py:84:    summary = projection.streak_summaries[learner.id]
```

| Location | Kind | Role |
|---|---|---|
| `src/state_machine/projection.py:52-76` | definition | the class itself: `__init__`, `apply`, `clear`, `rebuild` |
| `tests/state_machine/test_streak_updates.py:187-205` | test | rebuild after wipe |
| `tests/state_machine/test_replay_no_decide.py:86-88` | test | replay does not call decision functions |
| `tests/state_machine/test_active_state_path.py:46,65,81` | test | the three tests copied into the new file |

Nothing else in `src/` reads `streak_summaries`. No API, service or other
projection uses it. The fix only has to keep the field's shape
(`dict[learner_id, LearnerStreakSummary]`) and the `apply/clear/rebuild`
interface for the two other test files above.

## Before

`src/state_machine/projection.py:58-65`:

```python
    def apply(self, domain_event: Any) -> None:
        """Updates projection from StreakUpdated facts."""
        if isinstance(domain_event, Learner.StreakUpdated):
            learner_id = domain_event.originator_id
            self.streak_summaries[learner_id] = LearnerStreakSummary(
                new_streak=domain_event.new_streak,
                freezes_remaining=domain_event.freezes_remaining,
            )
```

Problems I found in the code (the test run confirms them):

1. **Last write wins by arrival order.** It ignores `activity_date` and
   `revision`. A late `StreakUpdated` for 2026-10-01 (rev 2, streak 1)
   replaces the 2026-10-02 result (streak 2).
2. **`FreezeAcquired` is ignored.** It copies `freezes_remaining` from the
   event payload. That is a snapshot taken when the streak was computed, so a
   freeze acquired later never shows up.
3. **`rebuild_learner_state` is not idempotent for freezes.**
   `src/state_machine/projection.py:190-192` counts every `FreezeAcquired`
   event (`sum(1 for ...)`) and does not dedupe by `freeze_id`, so duplicated
   delivery doubles the balance.

For reference, the aggregate (`src/state_machine/learner.py:265-308`) uses the
highest `revision` per `activity_date`, the latest `activity_date` for the
streak, and `len(freeze_acquisitions) - sum(freezes_consumed of winners)` for
freezes. `freeze_acquisitions` is keyed by `freeze_id`.

Events emitted by the late-Oct1 + freeze scenario (`.venv/bin/python`, fields
trimmed):

```
Created {}
LessonCompleted {'activity_date': '2026-10-01'}
XPAwarded {}
StreakUpdated {'new_streak': 1, 'freezes_remaining': 0, 'activity_date': '2026-10-01', 'freezes_consumed': 0, 'revision': 1}
LessonCompleted {'activity_date': '2026-10-02'}
XPAwarded {}
StreakUpdated {'new_streak': 2, 'freezes_remaining': 0, 'activity_date': '2026-10-02', 'freezes_consumed': 0, 'revision': 1}
LessonCompleted {'activity_date': '2026-10-01'}
XPAwarded {}
StreakUpdated {'new_streak': 1, 'freezes_remaining': 0, 'activity_date': '2026-10-01', 'freezes_consumed': 0, 'revision': 2}
FreezeAcquired {'freeze_id': 'f1', 'activity_date': '2026-10-03'}
```

## After

This section covers the reader fix in `src/state_machine/projection.py`
(+58 / −22 at that point; +62 / −22 after the final docstring change). The
aggregate change in `src/state_machine/learner.py` is covered in Step 5.

### New: `fold_streak_and_freezes` (pure helper)

Before: did not exist. The winner logic was inline in `rebuild_learner_state`.

After:

```python
def fold_streak_and_freezes(
    streak_events: Iterable[Any], freeze_events: Iterable[Any]
) -> tuple[int, int]:
    winners: dict[str, Any] = {}
    for event in streak_events:
        prior = winners.get(event.activity_date)
        if prior is None or event.revision > prior.revision:
            winners[event.activity_date] = event
    freeze_ids = {event.freeze_id for event in freeze_events}
    new_streak = winners[max(winners)].new_streak if winners else 0
    consumed = sum(event.freezes_consumed for event in winners.values())
    return new_streak, len(freeze_ids) - consumed
```

- Per `activity_date`, the highest `revision` wins. The strict `>` keeps the
  first one seen on a tie.
- Docstring (final wording): the result is independent of order and duplicate
  delivery for histories where each (activity_date, revision) pair is unique,
  which the `Learner` aggregate guarantees. If two different events shared a
  pair, arrival order would decide between them. Docstring only, no code
  change.
- `new_streak` comes from the winner for `max(activity_date)`. Dates are ISO
  `YYYY-MM-DD` strings, so string `max` is chronological.
- `freezes_remaining` = number of distinct `freeze_id` minus the sum of
  `freezes_consumed` over the winners.
- No events gives `(0, 0)`.

### `LearnerStreakProjection`

Before:

```python
    def __init__(self) -> None:
        self.streak_summaries: dict[Any, LearnerStreakSummary] = {}

    def apply(self, domain_event: Any) -> None:
        if isinstance(domain_event, Learner.StreakUpdated):
            learner_id = domain_event.originator_id
            self.streak_summaries[learner_id] = LearnerStreakSummary(
                new_streak=domain_event.new_streak,
                freezes_remaining=domain_event.freezes_remaining,
            )

    def clear(self) -> None:
        self.streak_summaries.clear()

    def rebuild(self, domain_events):
        staged = LearnerStreakProjection()
        for domain_event in domain_events:
            staged.apply(domain_event)
        self.streak_summaries = staged.streak_summaries
```

After:

```python
    def __init__(self) -> None:
        self.streak_summaries: dict[Any, LearnerStreakSummary] = {}
        self._winners: dict[Any, dict[str, Any]] = {}   # learner -> date -> StreakUpdated
        self._freezes: dict[Any, dict[str, Any]] = {}   # learner -> freeze_id -> FreezeAcquired

    def apply(self, domain_event: Any) -> None:
        if isinstance(domain_event, Learner.StreakUpdated):
            learner_id = domain_event.originator_id
            winners = self._winners.setdefault(learner_id, {})
            prior = winners.get(domain_event.activity_date)
            if prior is None or domain_event.revision > prior.revision:
                winners[domain_event.activity_date] = domain_event
        elif isinstance(domain_event, Learner.FreezeAcquired):
            learner_id = domain_event.originator_id
            self._freezes.setdefault(learner_id, {}).setdefault(
                domain_event.freeze_id, domain_event
            )
        else:
            return
        new_streak, freezes_remaining = fold_streak_and_freezes(
            self._winners.get(learner_id, {}).values(),
            self._freezes.get(learner_id, {}).values(),
        )
        self.streak_summaries[learner_id] = LearnerStreakSummary(
            new_streak=new_streak, freezes_remaining=freezes_remaining
        )

    def clear(self) -> None:
        self.streak_summaries.clear()
        self._winners.clear()
        self._freezes.clear()

    def rebuild(self, domain_events):
        staged = LearnerStreakProjection()
        for domain_event in domain_events:
            staged.apply(domain_event)
        self.streak_summaries = staged.streak_summaries
        self._winners = staged._winners
        self._freezes = staged._freezes
```

Unchanged: class name, `streak_summaries` type, `LearnerStreakSummary`,
`current_streak`, and the `clear()` / `rebuild()` signatures.

The per-learner state stores the winning event objects themselves (they carry
`revision`, `new_streak` and `freezes_consumed`) and one event per `freeze_id`
(a set of ids in effect). That lets `apply` call the same helper as
`rebuild_learner_state`, so the two paths use the same code. The cost is
O(distinct dates + distinct freezes) per event, per learner.

### `rebuild_learner_state` (streak/freeze part only)

Before:

```python
    streak = 0
    freezes = sum(
        1 for event in events if isinstance(event, Learner.FreezeAcquired)
    )
    streak_events = [
        event for event in events if isinstance(event, Learner.StreakUpdated)
    ]
    winners: dict[str, Any] = {}
    for event in streak_events:
        prior = winners.get(event.activity_date)
        if prior is None or event.revision > prior.revision:
            winners[event.activity_date] = event
    if winners:
        last_streak = winners[max(winners)]
        streak = last_streak.new_streak
        freezes -= sum(event.freezes_consumed for event in winners.values())
```

After:

```python
    streak, freezes = fold_streak_and_freezes(
        (event for event in events if isinstance(event, Learner.StreakUpdated)),
        (event for event in events if isinstance(event, Learner.FreezeAcquired)),
    )
```

The docstring line about freezes was updated to say "distinct freeze ids".
XP, `activity_dates`, quests and badges logic were not touched.

Behaviour change: duplicated `FreezeAcquired` events (same `freeze_id`) are
now counted once. Without duplicates the result is the same as before.

## Tests

New file: `tests/state_machine/test_streak_reader.py`

| # | Test | Origin | What it checks |
|---|---|---|---|
| 1 | `test_streak_projection_matches_aggregate_for_late_oct1` | copied unchanged | late rev-2 Oct1 does not override the Oct2 streak |
| 2 | `test_streak_projection_counts_freeze_acquired_after_latest_activity` | copied unchanged | a freeze acquired after the last activity is counted |
| 3 | `test_streak_projection_is_idempotent_when_events_are_delivered_twice` | copied unchanged | `events + events` through the reader gives (1, 1) |
| 4 | `test_streak_projection_is_identical_for_every_arrival_order` | new (4a) | all 24 permutations of the 4 streak facts give one identical summary |
| 5 | `test_rebuild_learner_state_ignores_duplicated_events` | new (4b) | `rebuild_learner_state(events + events).freezes_remaining == rebuild_learner_state(events).freezes_remaining` |
| 6–11 | edge cases | Step 3 | see the table in "Step 3" |
| 12 | `test_aggregate_fold_and_projection_agree_on_random_histories` | Step 3 | seeded cross-check, 300 sequences. Red until Step 5, now green. |
| 13 | `test_minimal_cross_check_failure_freeze_consumed_by_earlier_date_correction` | Step 3 | minimal aggregate regression. Red until Step 5, now green. |
| 14 | `test_aggregate_freezes_remaining_matches_its_stored_outcomes` | Step 5 | aggregate invariant over the same 300 sequences. Red until Step 5, now green. |

Copied tests: the three test bodies and their helpers `_events`/`_complete` are
byte-for-byte the same as in `test_active_state_path.py:23-28, 38-85`. Only the
imports were cut down to what the file uses. The original file was not edited.

## Exact commands and output

```bash
.venv/bin/python -m pytest tests/state_machine/test_streak_reader.py -v
```

```
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0
collected 5 items

test_streak_reader.py::test_streak_projection_matches_aggregate_for_late_oct1 FAILED [ 20%]
test_streak_reader.py::test_streak_projection_counts_freeze_acquired_after_latest_activity FAILED [ 40%]
test_streak_reader.py::test_streak_projection_is_idempotent_when_events_are_delivered_twice FAILED [ 60%]
test_streak_reader.py::test_streak_projection_is_identical_for_every_arrival_order FAILED [ 80%]
test_streak_reader.py::test_rebuild_learner_state_ignores_duplicated_events FAILED [100%]

FAILED ...::test_streak_projection_matches_aggregate_for_late_oct1
    assert summary.new_streak == 2
    E  assert 1 == 2   (LearnerStreakSummary(new_streak=1, freezes_remaining=0))
FAILED ...::test_streak_projection_counts_freeze_acquired_after_latest_activity
    assert summary.freezes_remaining == 1
    E  assert 0 == 1   (LearnerStreakSummary(new_streak=2, freezes_remaining=0))
FAILED ...::test_streak_projection_is_idempotent_when_events_are_delivered_twice
    assert (summary.new_streak, summary.freezes_remaining) == (1, 1)
    E  assert (1, 0) == (1, 1)
FAILED ...::test_streak_projection_is_identical_for_every_arrival_order
    assert len(results) == 1
    E  {LearnerStreakSummary(new_streak=1, freezes_remaining=0),
    E   LearnerStreakSummary(new_streak=2, freezes_remaining=0)}
FAILED ...::test_rebuild_learner_state_ignores_duplicated_events
    assert twice.freezes_remaining == once.freezes_remaining
    E  assert 2 == 1
    E  twice = LearnerReplayState(xp_total=600, new_streak=2, freezes_remaining=2, ...)
    E  once  = LearnerReplayState(xp_total=300, new_streak=2, freezes_remaining=1, ...)
============================== 5 failed in 0.06s ===============================
```

(Paths shortened to `test_streak_reader.py`. The full verbatim output was
captured during the run.)

### Why each one fails

| # | Cause |
|---|---|
| 1 | Problem 1: the Oct1 rev-2 event arrives last and overwrites the Oct2 streak 2 with 1. |
| 2 | Problem 2: `FreezeAcquired` is ignored, and the payload of the last `StreakUpdated` says 0. |
| 3 | Problem 2: same cause. Duplication is not what breaks it here. Even single delivery gives `freezes_remaining=0`. |
| 4 | Problem 1: the outcome depends on which `StreakUpdated` comes last (streak 1 vs 2). Freezes are 0 in every order (Problem 2), so this test only shows the streak divergence. |
| 5 | Problem 3: `rebuild_learner_state` counts `FreezeAcquired` twice. |

## Fix step: commands and output

### Baseline full suite (before editing `src/`)

```bash
.venv/bin/python -m pytest -q
```

```
FAILED tests/state_machine/test_active_state_path.py::test_streak_projection_matches_aggregate_for_late_oct1
FAILED tests/state_machine/test_active_state_path.py::test_streak_projection_counts_freeze_acquired_after_latest_activity
FAILED tests/state_machine/test_active_state_path.py::test_streak_projection_is_idempotent_when_events_are_delivered_twice
FAILED tests/state_machine/test_active_state_path.py::test_quest_projection_keeps_repeated_assignments_separate
FAILED tests/state_machine/test_active_state_path.py::test_quest_projection_marks_overdue_assignment_expired_without_new_lesson
FAILED tests/state_machine/test_active_state_path.py::test_quest_projection_applies_expiry_events
FAILED tests/state_machine/test_streak_reader.py::test_streak_projection_matches_aggregate_for_late_oct1
FAILED tests/state_machine/test_streak_reader.py::test_streak_projection_counts_freeze_acquired_after_latest_activity
FAILED tests/state_machine/test_streak_reader.py::test_streak_projection_is_idempotent_when_events_are_delivered_twice
FAILED tests/state_machine/test_streak_reader.py::test_streak_projection_is_identical_for_every_arrival_order
FAILED tests/state_machine/test_streak_reader.py::test_rebuild_learner_state_ignores_duplicated_events
11 failed, 113 passed, 9 skipped in 1.40s
```

### New test file (after fix)

```bash
.venv/bin/python -m pytest tests/state_machine/test_streak_reader.py -v
```

```
tests/state_machine/test_streak_reader.py::test_streak_projection_matches_aggregate_for_late_oct1 PASSED [ 20%]
tests/state_machine/test_streak_reader.py::test_streak_projection_counts_freeze_acquired_after_latest_activity PASSED [ 40%]
tests/state_machine/test_streak_reader.py::test_streak_projection_is_idempotent_when_events_are_delivered_twice PASSED [ 60%]
tests/state_machine/test_streak_reader.py::test_streak_projection_is_identical_for_every_arrival_order PASSED [ 80%]
tests/state_machine/test_streak_reader.py::test_rebuild_learner_state_ignores_duplicated_events PASSED [100%]

============================== 5 passed in 0.05s ===============================
```

### Full suite (after fix)

```bash
.venv/bin/python -m pytest -q
```

```
FAILED tests/state_machine/test_active_state_path.py::test_quest_projection_keeps_repeated_assignments_separate
FAILED tests/state_machine/test_active_state_path.py::test_quest_projection_marks_overdue_assignment_expired_without_new_lesson
FAILED tests/state_machine/test_active_state_path.py::test_quest_projection_applies_expiry_events
3 failed, 121 passed, 9 skipped in 1.36s
```

What changed: 8 tests now pass (5 in the new file, plus the 3 original copies
in `test_active_state_path.py`). Nothing that passed before fails now.

The 3 remaining failures were already failing and are outside this fix. They
need a quest read model API that `LearnerQuestProjection` does not have yet:

```
E       AttributeError: 'LearnerQuestSummary' object has no attribute 'assignments'
E       TypeError: LearnerQuestProjection.get_summary() got an unexpected keyword argument 'as_of'
E       AttributeError: 'LearnerQuestSummary' object has no attribute 'assignments'
```

## Step 3: edge cases and cross-check

No `src/` changes in this step. No test exposed a bug in the reader
(`LearnerStreakProjection`) or the helper (`fold_streak_and_freezes`).

### Tests added to `tests/state_machine/test_streak_reader.py`

| Test | Scenario | Result |
|---|---|---|
| `test_lower_revision_arriving_after_higher_is_ignored` | Oct1, Oct3, then a late Oct2 lesson. Oct3 gets rev 1 (streak 1) and rev 2 (streak 3). Rev 1 is delivered last. | PASS: reader stays at (3, 0) |
| `test_two_learners_do_not_affect_each_other` | Two learners' events interleaved into one projection. Both use freeze id `shared-id`. | PASS: each summary equals its solo projection: (2, 1) and (1, 2) |
| `test_freeze_acquired_before_any_activity` | Freeze on Sep30 before any lesson, then a lesson on Oct1. All three views compared at both points. | PASS: (0, 1), then (1, 1) |
| `test_gap_covered_by_freeze_keeps_streak` | Oct1, freeze, Oct3 | PASS: (2, 0) in all three views |
| `test_gap_not_covered_resets_streak_to_one` | Oct1, Oct2, Oct4, no freeze | PASS: (1, 0) in all three views |
| `test_empty_history` | `fold([], [])`, `rebuild_learner_state([])`, empty projection, `Learner()` with only a Created event | PASS: (0, 0), and the projection has no entry |
| `test_aggregate_fold_and_projection_agree_on_random_histories` | 300 seeded sequences (seed `20261024`). Each has 1–10 ops; 75% `complete_lesson`, 25% `acquire_freeze`, each on a random day Oct 1–10, so late and out-of-order days are common. | **FAIL: 3/300 disagree** |
| `test_minimal_cross_check_failure_freeze_consumed_by_earlier_date_correction` | The shrunk sequence below, as a fixed regression test | **FAIL** (aggregate bug) |

The projection side of `_three_views` uses `LearnerStreakSummary(0, 0)` when
a learner has no summary. That happens when there are no `StreakUpdated` or
`FreezeAcquired` events.

### Cross-check result

All 3 failures have the same shape: **fold == projection, and the aggregate
differs in `freezes_remaining` only** (the aggregate is 1 higher each time).

| Original sequence (from the seeded run) | Shrunk to | (aggregate, fold, projection) |
|---|---|---|
| 10 ops, starting `lesson 10-03, freeze 10-10, freeze 10-07, …` | `lesson 10-05, lesson 10-03, freeze 10-02, lesson 10-01` | ((1,1),(1,0),(1,0)) |
| `lesson 10-06, 10-04, 10-08, freeze 10-01, lesson 10-01, 10-01, 10-06, 10-02` | `lesson 10-04, lesson 10-08, freeze 10-01, lesson 10-02` | ((1,1),(1,0),(1,0)) |
| `lesson 10-06, 10-04, freeze 10-03, lesson 10-09, lesson 10-02` | `lesson 10-06, lesson 10-04, freeze 10-03, lesson 10-02` | ((1,1),(1,0),(1,0)) |

Shrinking method: greedily drop one op at a time while the mismatch still
reproduces, until no single op can be removed. Script:
`/tmp/claude-1000/-home-sampadram-Sampadram-IITH-SEM-7-DATGC-project-gamification-engine/e3a7c790-d9c1-4f01-937a-38fe02d04b9b/scratchpad/shrink.py`
(scratch, not part of the repo).

### Minimal failing sequence

```python
learner = Learner()
learner.complete_lesson("c0", "lesson-1", activity_date="2026-10-05")
learner.complete_lesson("c1", "lesson-1", activity_date="2026-10-03")
learner.acquire_freeze("f2", activity_date="2026-10-02")
learner.complete_lesson("c3", "lesson-1", activity_date="2026-10-01")
```

Events emitted (trimmed):

```
LessonCompleted {'activity_date': '2026-10-05'}
StreakUpdated {'new_streak': 1, 'freezes_remaining': 0, 'activity_date': '2026-10-05', 'freezes_consumed': 0, 'revision': 1}
LessonCompleted {'activity_date': '2026-10-03'}
StreakUpdated {'new_streak': 1, 'freezes_remaining': 0, 'activity_date': '2026-10-03', 'freezes_consumed': 0, 'revision': 1}
FreezeAcquired {'freeze_id': 'f2', 'activity_date': '2026-10-02'}
LessonCompleted {'activity_date': '2026-10-01'}
StreakUpdated {'new_streak': 1, 'freezes_remaining': 1, 'activity_date': '2026-10-01', 'freezes_consumed': 0, 'revision': 1}
StreakUpdated {'new_streak': 2, 'freezes_remaining': 0, 'activity_date': '2026-10-03', 'freezes_consumed': 1, 'revision': 2}
```

| View | (new_streak, freezes_remaining) |
|---|---|
| `calculate_streak` over the full history (ground truth): Oct1 (1,1,0), Oct3 (2,0,1), Oct5 (1,0,0) | **(1, 0)** |
| `rebuild_learner_state(events)` | (1, 0) |
| `LearnerStreakProjection` | (1, 0) |
| `Learner` aggregate | **(1, 1)**, wrong |

The aggregate's own `streak_outcomes` are correct
(`{Oct5: (1,0,0), Oct3: (2,0,1), Oct1: (1,1,0)}`). Only its
`freezes_remaining` field is stale.

### Root cause (aggregate, fixed in Step 5)

`src/state_machine/learner.py`, `Learner._update_streak` (around lines
265–308). `freezes_remaining` is only recomputed when the event's
`activity_date >= self.last_activity_date`:

```python
        if (
            self.last_activity_date is None
            or activity_date >= self.last_activity_date
        ):
            self.new_streak = new_streak
            self.freezes_remaining = freezes_remaining
            self.last_activity_date = activity_date
        ...
        if (
            self.last_activity_date is not None
            and activity_date >= self.last_activity_date
        ):
            self.freezes_remaining = len(self.freeze_acquisitions) - sum(
                outcome[2] for outcome in self.streak_outcomes.values()
            )
```

Walkthrough:
1. `last_activity_date` is Oct5 after the first lesson.
2. `_acquire_freeze` does `freezes_remaining += 1`, which gives 1.
3. The late Oct1 lesson makes Oct2 a gap between Oct1 and Oct3, so
   `complete_lesson` emits an Oct3 rev-2 correction with
   `freezes_consumed=1`.
4. Oct3 < Oct5, so neither branch runs. The consumed freeze is recorded in
   `streak_outcomes` but never subtracted from `freezes_remaining`.

This is the edge case I flagged as uncertain in the fix step. It is now
confirmed. Because the event log is correct, every reader built from events
(the fold and the projection) is correct. Only the in-memory aggregate field
drifts. A likely fix is to recompute `freezes_remaining` from
`freeze_acquisitions` and `streak_outcomes` on every `StreakUpdated`, not
only for the latest date. Not done, as instructed.

Impact: anything that reads `learner.freezes_remaining` straight from the
aggregate can over-report freezes after an out-of-order completion. I haven't
checked whether `complete_lesson`'s decisions read this field: it passes
`freeze_acquisitions` to `calculate_streak`, not `freezes_remaining`, so
decisions look unaffected. That is not verified.

### Step 3 commands and output

```bash
.venv/bin/python -m pytest tests/state_machine/test_streak_reader.py -v
```

```
tests/state_machine/test_streak_reader.py::test_streak_projection_matches_aggregate_for_late_oct1 PASSED [  7%]
tests/state_machine/test_streak_reader.py::test_streak_projection_counts_freeze_acquired_after_latest_activity PASSED [ 15%]
tests/state_machine/test_streak_reader.py::test_streak_projection_is_idempotent_when_events_are_delivered_twice PASSED [ 23%]
tests/state_machine/test_streak_reader.py::test_streak_projection_is_identical_for_every_arrival_order PASSED [ 30%]
tests/state_machine/test_streak_reader.py::test_rebuild_learner_state_ignores_duplicated_events PASSED [ 38%]
tests/state_machine/test_streak_reader.py::test_lower_revision_arriving_after_higher_is_ignored PASSED [ 46%]
tests/state_machine/test_streak_reader.py::test_two_learners_do_not_affect_each_other PASSED [ 53%]
tests/state_machine/test_streak_reader.py::test_freeze_acquired_before_any_activity PASSED [ 61%]
tests/state_machine/test_streak_reader.py::test_gap_covered_by_freeze_keeps_streak PASSED [ 69%]
tests/state_machine/test_streak_reader.py::test_gap_not_covered_resets_streak_to_one PASSED [ 76%]
tests/state_machine/test_streak_reader.py::test_empty_history PASSED     [ 84%]
tests/state_machine/test_streak_reader.py::test_aggregate_fold_and_projection_agree_on_random_histories FAILED [ 92%]
tests/state_machine/test_streak_reader.py::test_minimal_cross_check_failure_freeze_consumed_by_earlier_date_correction FAILED [100%]
E       AssertionError: 3/300 sequences disagree (aggregate, fold, projection). Shortest: ([('lesson', '2026-10-06'), ('lesson', '2026-10-04'), ('freeze', '2026-10-03'), ('lesson', '2026-10-09'), ('lesson', '2026-10-02')], ((1, 1), (1, 0), (1, 0)))
E       AssertionError: ((1, 1), (1, 0))
E       assert (1, 1) == (1, 0)
========================= 2 failed, 11 passed in 0.15s =========================
```

(The second `E` line of the random test, which lists all 3 failures, is
truncated by pytest. The 3 sequences are in the table above.)

```bash
.venv/bin/python -m pytest -q
```

```
FAILED tests/state_machine/test_active_state_path.py::test_quest_projection_keeps_repeated_assignments_separate
FAILED tests/state_machine/test_active_state_path.py::test_quest_projection_marks_overdue_assignment_expired_without_new_lesson
FAILED tests/state_machine/test_active_state_path.py::test_quest_projection_applies_expiry_events
FAILED tests/state_machine/test_streak_reader.py::test_aggregate_fold_and_projection_agree_on_random_histories
FAILED tests/state_machine/test_streak_reader.py::test_minimal_cross_check_failure_freeze_consumed_by_earlier_date_correction
5 failed, 127 passed, 9 skipped in 1.46s
```

Compared with the end of the fix step (3 failed / 121 passed): 8 new tests.
6 pass and 2 fail because of the aggregate bug. Nothing that passed before
fails now.

## Assumptions

- Cross-check: dates come from a 10-day window (Oct 1–10), so gaps,
  same-day repeats and late days all happen often. Sequences are 1–10 ops.
  Freeze dates are random and can be before, between or after the lessons.
  Ground truth for diagnosis is `calculate_streak` over the aggregate's
  final `activity_dates` and `freeze_acquisitions`.
- No test is marked `xfail` or skipped. The aggregate-bug tests stayed plain
  failures until Step 5 fixed the bug.

- **The permutation test permutes only `StreakUpdated` and `FreezeAcquired`.**
  The scenario has 11 events, and 11! ≈ 40M runs is not practical. The current
  reader ignores every other event type, so any arrival order of the full list
  reduces to one of these 24 orders. If the fix makes the reader consume more
  event types (for example `LessonCompleted`), this test has to widen its
  filter.
- Test 4 checks only that every order gives the same summary, as asked. It does
  not also check that the result equals the aggregate (2, 1). Tests 1–2 cover
  agreement with the aggregate. Adding an equality check would make test 4
  stricter.
- "Same events delivered twice" means the same event objects (same `id`,
  `originator_version`, `freeze_id`). It does not mean two different
  acquisitions that share a `freeze_id`.
- Tests run with `.venv/bin/python`. The system `python` has no `eventsourcing`
  module.

## Known uncertainty and out-of-scope issues

- **Aggregate `freezes_remaining` going stale: FIXED (Step 5).** Found by
  the Step 3 cross-check. `Learner._update_streak` used to recompute
  `freezes_remaining` only for events on or after `last_activity_date`, so a
  freeze consumed by an earlier-date correction was never subtracted. It now
  recomputes after every outcome. All 3 pinning tests pass. Before the fix,
  the bug looked like it did not affect decisions (`complete_lesson` passes
  `freeze_acquisitions` to `calculate_streak`, not `freezes_remaining`). Any
  external reader of `learner.freezes_remaining` that saw the old value is
  now corrected.
- **Order independence assumes a unique (activity_date, revision) pair.** The
  fold keeps the first event seen when revisions are equal. The aggregate
  always issues `revision = previous + 1` per date, so the pair is unique and
  order does not matter. A history that broke this (for example, events from
  two writers for the same learner) would let arrival order decide. This is
  stated in the `fold_streak_and_freezes` docstring. It is not tested, because
  the aggregate cannot produce such a history.
- **`XPAwarded` is not deduplicated in `rebuild_learner_state` (out of
  scope).** `xp_total` sums every `XPAwarded` event, so duplicate delivery
  doubles it (300 to 600 in test 5). It's not asserted anywhere and was left
  unchanged by instruction. A fix would dedupe by `reward_id`.
- **Duplicate `StreakUpdated` with a freeze consumed.** Highest-revision-wins
  plus summing `freezes_consumed` over winners only makes duplicates harmless
  in theory. The duplicate tests (3, 5) only use histories with
  `freezes_consumed == 0`, and the cross-check does not duplicate events. A
  duplicated history that consumes a freeze is not tested.
- **Negative freeze balance is possible.** If `freezes_consumed` ever exceeds
  the distinct freeze ids, the helper returns a negative balance. It does not
  clamp, matching the previous `rebuild_learner_state` behaviour.
- **Cross-check coverage.** It uses one seed, 300 sequences, a 10-day window,
  1–10 ops each, and `rule_version` v1 only. It does not cover timezones,
  `occurred_at`-derived dates, or multiple rule versions.
- **Quest tests in `test_active_state_path.py`.** Three quest-projection tests
  fail both before and after this fix (missing `assignments` / `as_of` API).
  They are unrelated to the streak reader.
- **Workspace state differs from what was expected** (see "Final
  verification"). `README.md` shows no modification in `git status`, and
  `test_active_state_path.py` is tracked (committed in `5c77444`), not an
  untracked stray file. There are no other local changes.

## Final verification

Run after the `fold_streak_and_freezes` docstring change, which was the last
edit to `src/`.

### Test counts

```bash
.venv/bin/python -m pytest -q
```

```
FAILED tests/state_machine/test_active_state_path.py::test_quest_projection_keeps_repeated_assignments_separate
FAILED tests/state_machine/test_active_state_path.py::test_quest_projection_marks_overdue_assignment_expired_without_new_lesson
FAILED tests/state_machine/test_active_state_path.py::test_quest_projection_applies_expiry_events
3 failed, 130 passed, 9 skipped in 1.50s
```

```bash
.venv/bin/python -m pytest -q --ignore=tests/state_machine/test_active_state_path.py
```

```
125 passed, 9 skipped in 1.44s
```

```bash
.venv/bin/python -m pytest -q tests/state_machine/test_streak_reader.py
```

```
14 passed in 0.18s
```

How the counts add up:

| Scope | Passed | Failed | Skipped | Breakdown |
|---|---|---|---|---|
| Excluding `test_active_state_path.py` | 125 | 0 | 9 | The 122 that passed at the end of Step 3 (111 baseline + 11 new), plus the 2 formerly red aggregate tests, plus the new invariant test. That equals 111 baseline + 14 new. |
| Full suite | 130 | 3 | 9 | 125 + the 5 passing tests in `test_active_state_path.py` (3 streak tests now fixed, plus 2 ingestion tests). The 3 failures are its quest-projection tests, which already existed. |

The 9 skips are unchanged from the baseline. No baseline test regressed. No
test is marked `xfail`.

### Git (read-only)

```bash
git status --short
```

```
 M src/state_machine/learner.py
 M src/state_machine/projection.py
?? docs/audit/week4-fix2-streak-reader.md
?? tests/state_machine/test_streak_reader.py
```

```bash
git diff --stat
```

```
 src/state_machine/learner.py    | 12 ++----
 src/state_machine/projection.py | 84 ++++++++++++++++++++++++++++++-----------
 2 files changed, 66 insertions(+), 30 deletions(-)
```

```bash
git stash list
git log --oneline -1
```

```
(no stashes)
5c77444 fix(m2): accept new lessons tagged to an already-completed quest
```

Changed or created relative to `5c77444`:

| File | Status |
|---|---|
| `src/state_machine/learner.py` | modified (+4 / −8) |
| `src/state_machine/projection.py` | modified (+62 / −22) |
| `tests/state_machine/test_streak_reader.py` | new |
| `docs/audit/week4-fix2-streak-reader.md` | new |

Other local changes that already existed: **none.** `README.md` is tracked
and clean (no diff), even though it was expected to be modified already.
Nothing was committed, staged or stashed during this work.

### Suggested commit (not made)

Files:

```
src/state_machine/learner.py
src/state_machine/projection.py
tests/state_machine/test_streak_reader.py
docs/audit/week4-fix2-streak-reader.md
```

Message (no trailer; the author adds their own):

```
fix(m2): make streak reader and aggregate freeze balance order-independent

LearnerStreakProjection overwrote its summary with whichever
StreakUpdated arrived last and ignored FreezeAcquired, so a late
correction for an earlier day or a freeze acquired after the last
activity produced the wrong streak or freeze balance.

Add a pure fold_streak_and_freezes() helper: per activity_date the
highest revision wins (first seen on ties), the streak comes from the
latest date's winner, and freezes are distinct freeze_ids minus the
winners' freezes_consumed. rebuild_learner_state now uses it, so
redelivered FreezeAcquired events are no longer double-counted, and
LearnerStreakProjection is rewritten as an incremental form of the same
fold.

A seeded aggregate/fold/projection cross-check then showed that
Learner._update_streak only refreshed freezes_remaining for events on
or after the latest activity date, so a freeze consumed by an
earlier-date correction was never subtracted. Recompute it from
freeze_acquisitions and streak_outcomes after every outcome.

Add tests/state_machine/test_streak_reader.py (14 tests: copied reader
tests, permutation and duplicate delivery, edge cases, the seeded
cross-check, a minimal regression and an aggregate invariant). Details
in docs/audit/week4-fix2-streak-reader.md.

Out of scope: XPAwarded is still not deduplicated in
rebuild_learner_state.
```

## Step 5: aggregate freezes_remaining fix

Status: **pinned (tests red). No `src/` changes yet.**

### Cause

`Learner.freezes_remaining` is a cached field on the aggregate. Its correct
value can always be derived from two things the aggregate already stores:
the freezes it has acquired (`freeze_acquisitions`, one entry per
`freeze_id`), and the freezes each dated outcome consumed (index 2 of every
tuple in `streak_outcomes`).

`Learner._update_streak` (`src/state_machine/learner.py`) applies every
`StreakUpdated` event. It always writes the new outcome into
`streak_outcomes`, but it only refreshes `freezes_remaining` when the event's
`activity_date` is on or after `last_activity_date`. Both places that touch
`freezes_remaining` sit behind that guard.

`complete_lesson` can emit a `StreakUpdated` *correction* for a date earlier
than the latest activity. This happens when a late lesson opens a gap that an
already-acquired freeze now covers. That correction records
`freezes_consumed = 1` in `streak_outcomes`, but because its date is before
`last_activity_date` the guard skips the refresh. The consumed freeze is
stored but never subtracted. `freezes_remaining` keeps the value from the last
in-order event (or from `_acquire_freeze`'s `+= 1`) and over-reports by the
number of freezes those corrections consumed.

Only the cached field is wrong. The event log and `streak_outcomes` are
correct, which is why `rebuild_learner_state` and `LearnerStreakProjection`
(both derived from events) give the right answer.

### Pinning test

Added to `tests/state_machine/test_streak_reader.py`:

`test_aggregate_freezes_remaining_matches_its_stored_outcomes`: for each of the
300 sequences from the cross-check (same `SEED = 20261024`, same
`_random_ops` / `_run_ops`), assert

```python
learner.freezes_remaining == len(learner.freeze_acquisitions) - sum(
    outcome[2] for outcome in learner.streak_outcomes.values()
)
```

This invariant only reads the aggregate's own state. It does not depend on
the fold or the projection, so it isolates the stale cache from any reader.

### Command and output

```bash
.venv/bin/python -m pytest tests/state_machine/test_streak_reader.py -v
```

```
tests/state_machine/test_streak_reader.py::test_streak_projection_matches_aggregate_for_late_oct1 PASSED [  7%]
tests/state_machine/test_streak_reader.py::test_streak_projection_counts_freeze_acquired_after_latest_activity PASSED [ 14%]
tests/state_machine/test_streak_reader.py::test_streak_projection_is_idempotent_when_events_are_delivered_twice PASSED [ 21%]
tests/state_machine/test_streak_reader.py::test_streak_projection_is_identical_for_every_arrival_order PASSED [ 28%]
tests/state_machine/test_streak_reader.py::test_rebuild_learner_state_ignores_duplicated_events PASSED [ 35%]
tests/state_machine/test_streak_reader.py::test_lower_revision_arriving_after_higher_is_ignored PASSED [ 42%]
tests/state_machine/test_streak_reader.py::test_two_learners_do_not_affect_each_other PASSED [ 50%]
tests/state_machine/test_streak_reader.py::test_freeze_acquired_before_any_activity PASSED [ 57%]
tests/state_machine/test_streak_reader.py::test_gap_covered_by_freeze_keeps_streak PASSED [ 64%]
tests/state_machine/test_streak_reader.py::test_gap_not_covered_resets_streak_to_one PASSED [ 71%]
tests/state_machine/test_streak_reader.py::test_empty_history PASSED     [ 78%]
tests/state_machine/test_streak_reader.py::test_aggregate_fold_and_projection_agree_on_random_histories FAILED [ 85%]
tests/state_machine/test_streak_reader.py::test_minimal_cross_check_failure_freeze_consumed_by_earlier_date_correction FAILED [ 92%]
tests/state_machine/test_streak_reader.py::test_aggregate_freezes_remaining_matches_its_stored_outcomes FAILED [100%]
E       AssertionError: 3/300 sequences disagree (aggregate, fold, projection). Shortest: ([('lesson', '2026-10-06'), ('lesson', '2026-10-04'), ('freeze', '2026-10-03'), ('lesson', '2026-10-09'), ('lesson', '2026-10-02')], ((1, 1), (1, 0), (1, 0)))
E       assert not [([('lesson', '2026-10-03'), ('freeze', '2026-10-10'), ('freeze', '2026-10-07'), ('lesson', '2026-10-09'), ('lesson', '2026-10-05'), ('lesson', '2026-10-03'), ...], ((1, 3), (1, 2), (1, 2))), ([('lesson', '2026-10-06'), ('lesson', '2026-10-04'), ('lesson', '2026-10-08'), ('freeze', '2026-10-01'), ('lesson', '2026-10-01'), ('lesson', '2026-10-01'), ...], ((1, 1), (1, 0), (1, 0))), ([('lesson', '2026-10-06'), ('lesson', '2026-10-04'), ('freeze', '2026-10-03'), ('lesson', '2026-10-09'), ('lesson', '2026-10-02')], ((1, 1), (1, 0), (1, 0)))]
E       AssertionError: ((1, 1), (1, 0))
E       assert (1, 1) == (1, 0)
E         
E         At index 1 diff: 1 != 0
E         
E         Full diff:
E           (
E               1,
E         -     0,...
E         
E         ...Full output truncated (4 lines hidden), use '-vv' to show
E       AssertionError: 3/300 sequences have a stale freezes_remaining (ops, actual, expected). Shortest: ([('lesson', '2026-10-06'), ('lesson', '2026-10-04'), ('freeze', '2026-10-03'), ('lesson', '2026-10-09'), ('lesson', '2026-10-02')], 1, 0)
E       assert not [([('lesson', '2026-10-03'), ('freeze', '2026-10-10'), ('freeze', '2026-10-07'), ('lesson', '2026-10-09'), ('lesson', '2026-10-05'), ('lesson', '2026-10-03'), ...], 3, 2), ([('lesson', '2026-10-06'), ('lesson', '2026-10-04'), ('lesson', '2026-10-08'), ('freeze', '2026-10-01'), ('lesson', '2026-10-01'), ('lesson', '2026-10-01'), ...], 1, 0), ([('lesson', '2026-10-06'), ('lesson', '2026-10-04'), ('freeze', '2026-10-03'), ('lesson', '2026-10-09'), ('lesson', '2026-10-02')], 1, 0)]
FAILED tests/state_machine/test_streak_reader.py::test_aggregate_fold_and_projection_agree_on_random_histories
FAILED tests/state_machine/test_streak_reader.py::test_minimal_cross_check_failure_freeze_consumed_by_earlier_date_correction
FAILED tests/state_machine/test_streak_reader.py::test_aggregate_freezes_remaining_matches_its_stored_outcomes
========================= 3 failed, 11 passed in 0.20s =========================
```

(Filtered to PASSED/FAILED/`E` lines; the tracebacks are left out.)

### Why the new test is red for the same reason

| Sequence (prefix) | Cross-check (aggregate, fold, projection) | New test (actual, expected) |
|---|---|---|
| `lesson 10-03, freeze 10-10, freeze 10-07, …` (10 ops) | ((1,3),(1,2),(1,2)) | (3, 2) |
| `lesson 10-06, 10-04, 10-08, freeze 10-01, …` (8 ops) | ((1,1),(1,0),(1,0)) | (1, 0) |
| `lesson 10-06, 10-04, freeze 10-03, lesson 10-09, 10-02` (5 ops) | ((1,1),(1,0),(1,0)) | (1, 0) |

- The new test fails on exactly the same 3 of 300 sequences as the cross-check.
- In each one, `freezes_remaining` is too high by exactly 1, which is the
  freeze consumed by an earlier-date correction.
- The expected value (from the aggregate's own stored outcomes) equals the
  fold and projection value every time.
- `new_streak` is not involved. The streak part of all three views agrees in
  every failure.

So all three red tests show the one bug described above: a stale cached
`freezes_remaining`. There is no second bug.

### Fix

Status: **fixed. All 3 previously red tests are green. No tests were changed,
and no `xfail` was added.**

Only `Learner._update_streak` in `src/state_machine/learner.py` changed.
Nothing else in `src/` changed in this step. `_acquire_freeze` is untouched.

`git diff --stat src/state_machine/learner.py`: `1 file changed, 4 insertions(+), 8 deletions(-)`

Before:

```python
        if (
            self.last_activity_date is None
            or activity_date >= self.last_activity_date
        ):
            self.new_streak = new_streak
            self.freezes_remaining = freezes_remaining
            self.last_activity_date = activity_date
        if new_streak == 1 and previous_streak > 1:
            self.streak_before_break = previous_streak
        self.streak_revisions[activity_date] = revision
        self.streak_outcomes[activity_date] = (...)
        if (
            self.last_activity_date is not None
            and activity_date >= self.last_activity_date
        ):
            self.freezes_remaining = len(self.freeze_acquisitions) - sum(
                outcome[2] for outcome in self.streak_outcomes.values()
            )
```

After:

```python
        if (
            self.last_activity_date is None
            or activity_date >= self.last_activity_date
        ):
            self.new_streak = new_streak
            self.last_activity_date = activity_date
        if new_streak == 1 and previous_streak > 1:
            self.streak_before_break = previous_streak
        self.streak_revisions[activity_date] = revision
        self.streak_outcomes[activity_date] = (...)
        # Recompute on every outcome: an earlier-date correction can consume a freeze.
        self.freezes_remaining = len(self.freeze_acquisitions) - sum(
            outcome[2] for outcome in self.streak_outcomes.values()
        )
```

Diff:

```diff
@@ -286,7 +286,6 @@ class Learner(Aggregate):
             or activity_date >= self.last_activity_date
         ):
             self.new_streak = new_streak
-            self.freezes_remaining = freezes_remaining
             self.last_activity_date = activity_date
         if new_streak == 1 and previous_streak > 1:
             self.streak_before_break = previous_streak
@@ -299,13 +298,10 @@ class Learner(Aggregate):
             f"streak:{self.id}:{activity_date}:{revision}",
             supersedes,
         )
-        if (
-            self.last_activity_date is not None
-            and activity_date >= self.last_activity_date
-        ):
-            self.freezes_remaining = len(self.freeze_acquisitions) - sum(
-                outcome[2] for outcome in self.streak_outcomes.values()
-            )
+        # Recompute on every outcome: an earlier-date correction can consume a freeze.
+        self.freezes_remaining = len(self.freeze_acquisitions) - sum(
+            outcome[2] for outcome in self.streak_outcomes.values()
+        )
```

Decisions:
- The guarded block still sets `new_streak` and `last_activity_date` only for
  events on or after the latest date. That is unchanged.
- **Removed the earlier `self.freezes_remaining = freezes_remaining`.** It was
  always overwritten later in the same handler by the unconditional recompute,
  so it had no effect. The tests pass without it (below). The `freezes_remaining`
  handler parameter is still part of the persisted `StreakUpdated` event and is
  still read by other consumers. Only this one assignment was dropped.
- **`_acquire_freeze` (`freezes_remaining += 1`) is kept unchanged.** It adds
  one entry to `freeze_acquisitions` and adds 1 to the balance, so it keeps the
  invariant `freezes_remaining == len(freeze_acquisitions) - Σ consumed` as
  long as the balance was correct before. The fix makes sure it is correct
  after every `StreakUpdated`. `acquire_freeze` rejects a repeated `freeze_id`
  before the event is emitted, so the `+= 1` cannot double-count. No test
  shows it is wrong. Extra check (scratch script, not a repo test): for the
  same 300 seeded sequences, I asserted the invariant after **every**
  operation, not just at the end:

  ```bash
  .venv/bin/python -I /tmp/claude-1000/-home-sampadram-Sampadram-IITH-SEM-7-DATGC-project-gamification-engine/e3a7c790-d9c1-4f01-937a-38fe02d04b9b/scratchpad/acquire_check.py
  ```

  ```
  invariant checked after 1703 ops (428 acquire_freeze calls): 0 violations
  ```

  The script also confirms that re-acquiring an existing `freeze_id` returns
  `False` and leaves `freezes_remaining` unchanged.

Tests changed: **none.** No `xfail` was added anywhere.

### Commands and output after the fix

```bash
.venv/bin/python -m pytest tests/state_machine/test_streak_reader.py -v
```

```
tests/state_machine/test_streak_reader.py::test_streak_projection_matches_aggregate_for_late_oct1 PASSED [  7%]
tests/state_machine/test_streak_reader.py::test_streak_projection_counts_freeze_acquired_after_latest_activity PASSED [ 14%]
tests/state_machine/test_streak_reader.py::test_streak_projection_is_idempotent_when_events_are_delivered_twice PASSED [ 21%]
tests/state_machine/test_streak_reader.py::test_streak_projection_is_identical_for_every_arrival_order PASSED [ 28%]
tests/state_machine/test_streak_reader.py::test_rebuild_learner_state_ignores_duplicated_events PASSED [ 35%]
tests/state_machine/test_streak_reader.py::test_lower_revision_arriving_after_higher_is_ignored PASSED [ 42%]
tests/state_machine/test_streak_reader.py::test_two_learners_do_not_affect_each_other PASSED [ 50%]
tests/state_machine/test_streak_reader.py::test_freeze_acquired_before_any_activity PASSED [ 57%]
tests/state_machine/test_streak_reader.py::test_gap_covered_by_freeze_keeps_streak PASSED [ 64%]
tests/state_machine/test_streak_reader.py::test_gap_not_covered_resets_streak_to_one PASSED [ 71%]
tests/state_machine/test_streak_reader.py::test_empty_history PASSED     [ 78%]
tests/state_machine/test_streak_reader.py::test_aggregate_fold_and_projection_agree_on_random_histories PASSED [ 85%]
tests/state_machine/test_streak_reader.py::test_minimal_cross_check_failure_freeze_consumed_by_earlier_date_correction PASSED [ 92%]
tests/state_machine/test_streak_reader.py::test_aggregate_freezes_remaining_matches_its_stored_outcomes PASSED [100%]
============================== 14 passed in 0.18s ==============================
```

```bash
.venv/bin/python -m pytest -q
```

```
FAILED tests/state_machine/test_active_state_path.py::test_quest_projection_keeps_repeated_assignments_separate
FAILED tests/state_machine/test_active_state_path.py::test_quest_projection_marks_overdue_assignment_expired_without_new_lesson
FAILED tests/state_machine/test_active_state_path.py::test_quest_projection_applies_expiry_events
3 failed, 130 passed, 9 skipped in 1.49s
```

```bash
.venv/bin/python -m pytest -q --ignore=tests/state_machine/test_active_state_path.py
```

```
125 passed, 9 skipped in 1.42s
```

Against the stated baseline (111 passed / 9 skipped, excluding
`test_active_state_path.py`): 125 = 111 + 14 new tests, all passing. The 9
skips are unchanged, and no test regressed. The only failures left in the full
suite are the 3 quest-projection tests that already existed.

### Git (read-only) after Step 5

```
 M src/state_machine/learner.py
 M src/state_machine/projection.py
?? docs/audit/week4-fix2-streak-reader.md
?? tests/state_machine/test_streak_reader.py
```

`src/state_machine/learner.py` is now part of the change. "Final
verification" has the final file list and commit message.
