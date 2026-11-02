# Week 6 / M4 / Step 4 — quest expiry sweep

Status: **Step 4 complete (prompt 4): edge cases added, full suite green, expiry documented.** (Prompt 1: read + failing tests.)
Git rule in force: no state-changing git commands (none were run).

## 1. Changes made

- Created this file and `tests/interventions/test_quest_expiry.py` (16 tests). Nothing under `src/` changed.

## 2. Before / after behaviour

No behaviour change. Current behaviour (read, not run beyond the existing suite): an overdue quest becomes `expired` only inside `complete_lesson`
when a later completion arrives; a learner who never completes again keeps an `assigned` quest in the aggregate forever.

## 3. What I read

### 3.1 `QuestExpired` in `src/state_machine/learner.py`

- **Emitted** only from the quest progress loop in `complete_lesson`, for each instance with `status == "assigned"`, after the `occurred < assigned_at` skip:
  `if occurred is not None and deadline is not None and occurred > deadline: self._expire_quest(assignment_id, q_id, occurred.isoformat()); continue`
  (line ~210). So `expired_at` today is the **completion's `occurred_at`**, not the deadline, and the check is strict (`occurred > deadline`).
  A completion without `occurred_at` can never expire a quest.
- **Applied** by `@event("QuestExpired") _expire_quest(self, assignment_id, quest_id, expired_at: str)` (line ~583): a pure fold that only sets
  `quest_instances[assignment_id]["status"] = "expired"`. `expired_at` is **not stored on the instance**; it exists only on the event.
- Event schema `src/schemas/events/quest_expired.schema.json` requires `assignment_id`, `quest_id`, `expired_at` (string, date-time). Nothing else in `src/` reads `expired_at`.
- Instances store `deadline_at` as an aware `datetime` (via `_as_datetime`), so the sweep can compare directly to an aware `as_of`.

### 3.2 `src/state_machine/projection.py`

- `LearnerQuestProjection.apply` handles `QuestAssigned` (stores `assigned_at`, `deadline_at`), `QuestProgressed`, `QuestCompleted`, and
  `QuestExpired`; the last just does `status = _stronger_status(status, "expired")` with rank `assigned 0 < expired 1 < completed 2`.
  **`expired_at` is never read**, and expiry may arrive before the assignment (a record is created on demand). A completed quest is never downgraded to expired.
- `get_summary(learner_id, as_of=None)`: without `as_of` the status is event-derived only; with `as_of`, an `assigned` record whose `deadline_at` is strictly before
  `as_of` (`cutoff > deadline_at`) is **reported** as expired even if no event exists. So the read side already treats "strictly past the deadline" as expired, which matches the sweep's strict rule.
- `rebuild_learner_state` / `SQLiteLearnerProjection`: `rebuild_learner_state` folds only `XPAwarded`, `StreakUpdated`, `FreezeAcquired`, `LessonCompleted`, `QuestCompleted`
  (completed quest ids) and `BadgeAwarded`. It ignores `QuestAssigned` and `QuestExpired` entirely, and `SQLiteLearnerProjection` persists that same replay state.
  So a new `QuestExpired` with a deadline-based `expired_at` cannot change any of those replay results.
- Conclusion: emitting `QuestExpired` from a sweep (with `expired_at = deadline_at`) is invisible to all projections except as a status confirmation, which they already derive.

### 3.3 Existing tests that assert an expired status or `expired_at`

**No existing test asserts an `expired_at` value** (`grep expired_at tests` finds nothing). Tests asserting an `expired` *status*:
- `tests/state_machine/test_quest.py`: `expires-first` quest `status == "expired"` (line ~171, test name in that block: the expiry-on-later-completion test); persisted test around lines 232-255
  (one late completion → exactly one `QuestExpired`, none progressed/completed; restored learner still `expired`).
- `tests/state_machine/test_quest_reader.py`: `test_quest_projection_marks_overdue_assignment_expired_without_new_lesson` (read-side expiry via `as_of`, completed never expired);
  the lazy-expiry test at ~line 95-103 (`a1` expired in aggregate and projection); `_expired_assignment` fixture (~141, used at 163, 205, 226, 243); a status-set assertion at ~343.
- `tests/state_machine/test_persisted_reload_evidence.py`: scenario S4 — `q-expired` expires on the c2 completion; expected event counts (`QuestExpired: 1`), event order and versions,
  and per-`as_of` statuses (lines ~393-426, 455-500). **This pins the lazy path's event position and count.** The aggregate "never expires by time" comment at ~line 458 (`# the aggregate never expires by time`).
- `tests/ingestion/test_completed_quest_lesson.py`: `test_lesson_tagged_to_expired_quest_is_accepted_without_completed_quest_evidence` (~177-196): quest `expired` after a late tagged lesson.
- `tests/interventions/test_remediation_quest.py`: `test_remediation_completion_after_deadline_does_not_complete_and_expires_lazily` (Step 3, pins lazy expiry).
- (Other `expired` hits in tests/notifications and tests/ingestion/test_dedup.py are about TTL / cache expiry, unrelated.)

The lazy path must stay as is: these tests, notably S4's event ordering and the Step 3 lazy-expiry test, depend on it.

## 4. Tests added (`tests/interventions/test_quest_expiry.py`, 16)

`Learner.expire_due_quests` (11, in-memory aggregates):
1. `test_sweep_expires_an_overdue_quest_with_expired_at_equal_to_the_deadline` — returns `["remediation_quest:att-1"]`, status expired, one `QuestExpired` with `expired_at == deadline.isoformat()`.
2. `test_sweep_at_exactly_the_deadline_expires_nothing`.
3. `test_sweep_one_microsecond_after_the_deadline_expires` (extra).
4. `test_second_sweep_returns_empty_and_emits_nothing`.
5. `test_completed_quest_is_untouched`.
6. `test_already_expired_quest_is_untouched` (extra; expired lazily first).
7. `test_sweep_covers_rule_set_quests_too` — `lesson_starter_v1`.
8. `test_only_the_due_quest_is_expired`.
9. `test_expired_assignment_ids_are_returned_sorted_and_each_uses_its_own_deadline` (extra; ids `["a","b"]`, each `expired_at` is its own deadline).
10. `test_naive_as_of_raises_value_error_and_emits_nothing`.
11. `test_swept_quest_is_not_progressed_or_completed_by_a_later_remediation_completion` — completion dated inside the original window, so only the `expired` status can stop it.

`run_quest_expiry` (5, real `LearnerApplication` on tmp_path SQLite; quest assigned and saved via the aggregate):
12. `test_run_quest_expiry_expires_saves_and_returns_the_ids` — event count +1, stored status expired.
13. `test_run_quest_expiry_with_nothing_due_saves_nothing` — `[]`, event count unchanged.
14. `test_run_quest_expiry_twice_is_idempotent` (extra).
15. `test_run_quest_expiry_with_a_naive_clock_raises_and_saves_nothing`.
16. `test_expiry_survives_replay_in_a_fresh_application` (extra).

## 5. Command and output

```
$ python -m pytest tests/interventions/test_quest_expiry.py -v
16 failed in 0.25s
  11 x AttributeError: 'Learner' object has no attribute 'expire_due_quests'
   5 x ImportError: cannot import name 'run_quest_expiry' from 'src.interventions.service'
```
(All 16 listed `FAILED`; the two error texts are exact, counts taken from `--tb=line`.) Every failure is a missing feature: the Learner method and the service function. Test setup
(assigning quests, lazy expiry in test 6, completing a quest in test 5) ran successfully before the missing call, so the setups themselves are valid.

## 6. Assumptions / unsure

- Return type: a plain `list[str]` of assignment ids, sorted ascending (test 9).
- "Naive `as_of`" is checked before anything is emitted, even when no quest is due (test 10 has a due-in-principle assigned quest; the brief says it emits nothing).
- The sweep takes `as_of` strictly: `deadline_at < as_of` (tests 2, 3).
- `expired_at` on the event is `deadline_at.isoformat()`; the lazy path still uses the completion's `occurred_at.isoformat()`, so the two paths now stamp different things. Only the event carries it.
- `run_quest_expiry` for a learner id that does not exist is not specified or tested.
- Test 16 uses the aggregate's replayed `quest_instances` only; it does not check the projections in §3.2 (they ignore `expired_at`).
- Sweep order of emitted events is not asserted beyond the returned list being sorted (the in-memory event order for multiple expiries is untested).
- I did not decide how the sweep should be triggered (cron, startup, per-request); the brief only defines the function and a service wrapper.

---

# Prompt 2 — implementation

## 7. Changes made

- `src/state_machine/learner.py`: new method `Learner.expire_due_quests`, placed just above the existing `@event("QuestExpired") _expire_quest`.
- `src/interventions/service.py`: new function `run_quest_expiry`.
- Not touched: the lazy expiry path in `complete_lesson`, `_expire_quest`, ingestion, rule sets, `policy.py`, `quest_definitions.py`, any test. No new event type.

## 8. Before / after

- `Learner.expire_due_quests`: before — did not exist; after:
  ```python
  def expire_due_quests(self, as_of: datetime) -> list[str]:
      if as_of.tzinfo is None or as_of.utcoffset() is None:
          raise ValueError("as_of must be timezone-aware")
      expired: list[str] = []
      for assignment_id in sorted(self.assigned_quest_ids):
          instance = self.quest_instances[assignment_id]
          deadline = instance["deadline_at"]
          if instance["status"] != "assigned" or deadline is None or not deadline < as_of:
              continue
          self._expire_quest(assignment_id, str(instance["quest_id"]), deadline.isoformat())
          expired.append(assignment_id)
      return expired
  ```
  It reuses `_expire_quest` (the `QuestExpired` event), iterates ids in sorted order, uses the strict `deadline < as_of`, stamps `expired_at` with the quest's own deadline,
  and raises before emitting anything for a naive `as_of`.
- `run_quest_expiry` (new): load learner by id → `expire_due_quests(clock())` → `application.save(learner)` only if the list is non-empty → return the list.
- `complete_lesson` (lazy expiry) and `_expire_quest`: unchanged.

## 9. Commands and output

```
$ python -m pytest tests/interventions -v
98 passed in 0.71s      (82 earlier + 16 quest-expiry; all PASSED)

$ python -m pytest tests/state_machine tests/ingestion -q
241 passed in 3.10s
```
Before this prompt: all 16 quest-expiry tests failed (§5). The full suite and the integration marker were not re-run in this prompt.

## 10. Assumptions / unsure

- A `deadline_at` of None is skipped (cannot occur via `assign_quest`, which always sets a deadline; guard kept for safety).
- `as_of` is compared directly to the stored aware `deadline_at`; this relies on `quest_instances` holding aware datetimes (true via `_as_datetime`, which makes naive values UTC).
- The sweep stamps `expired_at` with the deadline while the lazy path still stamps the completion's `occurred_at`; both exist side by side.
- If a lazy-expired and a swept event could both exist for one quest: not possible, since both require `status == "assigned"` and the first sets `expired`.
- `run_quest_expiry` for an unknown learner id raises whatever `repository.get` raises; unspecified and untested.
- Still undecided: what triggers the sweep (scheduler, startup, per request).

---

# Prompt 3 — lazy expiry stamps the deadline

## 11. Changes made

- Test first: appended `test_lazy_expiry_records_the_deadline_as_expired_at` to `tests/interventions/test_quest_expiry.py`.
- `src/state_machine/learner.py`, `complete_lesson` quest loop, one line.
- `tests/interventions/test_remediation_quest.py`: the Step 3 lazy-expiry test updated (see §13).
- No test outside `tests/interventions` was edited; no pre-M4 test failed.

## 12. Before / after

`complete_lesson`, expiry call in the quest progress loop:
- before: `self._expire_quest(assignment_id, q_id, occurred.isoformat())`
- after:  `self._expire_quest(assignment_id, q_id, deadline.isoformat())`

The condition (`occurred > deadline`), the `continue`, and everything else are unchanged. `deadline` is the instance's stored aware `deadline_at`, non-None inside that branch.
Both expiry paths now record `expired_at = deadline_at`. Old streams still replay: `expired_at` is on the event only and the fold ignores it (§3.1-3.2), so already-stored events keep their original `occurred_at`-based value.

## 13. Step 3 test: what it pinned

The brief said `test_remediation_completion_after_deadline_does_not_complete_and_expires_lazily` pins the old value. **It did not**: it asserted only that
`QuestExpired` was emitted, status `expired`, no progress/completion, XP earned; it never read `expired_at`. So it passed before and after the change.
I still updated it so it now pins the new behaviour:
- before: `assert "QuestExpired" in names` (no `expired_at` check); comment "Records today's lazy expiry … sweep is Step 4 and is not tested here."
- after: additionally `assert expired[0].expired_at == (ASSIGNED + timedelta(days=7)).isoformat()  # the deadline`; comment now says expiry is stamped with the deadline, as the sweep does.

## 14. Commands and output

```
$ python -m pytest tests/interventions/test_quest_expiry.py -q      (new test, before the code change)
1 failed, 16 passed
FAILED test_lazy_expiry_records_the_deadline_as_expired_at
   expired_at was the completion's time '2026-09-08T01:00:00+00:00' (deadline + 1 h), not the deadline

$ python -m pytest -q                                               (after the one-line change)
757 passed, 2 skipped, 1 deselected in 23.82s

$ python -m pytest tests/interventions -q                           (after the Step 3 test update)
99 passed in 0.78s
$ python -m pytest -q
757 passed, 2 skipped, 1 deselected in 23.43s
$ python -m pytest -m integration -q
1 passed, 759 deselected in 4.78s
```
Counts: full suite 740 (end of Step 3) + 16 expiry tests + 1 new lazy-expiry test = 757, all green. No xfail was needed and nothing was reverted.

## 15. Assumptions / unsure

- Replay of existing event streams is unaffected because `_expire_quest` ignores `expired_at`; there is no migration of already-stored `QuestExpired` events (they keep the old completion-time value), so a stream can now contain both kinds of `expired_at`.
- Anything reading `expired_at` from events (nothing in `src/` does today) would see mixed semantics for historical vs new events.
- `docs/event-contracts.md` currently says nothing about `expired_at`; the contract text about expiry (Step 3 section, "lazy expiry marks it expired when the completion is processed") is not yet updated for the sweep or the deadline stamp.
- The brief's premise that the Step 3 test pinned the old value was wrong (§13); worth knowing in case another test was expected to change.

---

# Prompt 4 — edge cases and finish Step 4

## 16. Changes made

- Tests only (no source change, no fix needed): 5 added to `tests/interventions/test_quest_expiry.py`.
- `docs/event-contracts.md`: the lazy-expiry sentence in the remediation-quest subsection replaced; new subsection "Quest expiry, M4"; changelog line dated 2026-11-02.
- Traceability matrix, README, AT-17: not touched.

## 17. Edge-case tests and first-run result (run before any fix)

| # | Test | Passed first run? |
|---|------|------|
| 1a | `test_quest_readers_agree_with_the_aggregate_after_a_sweep` — `LearnerQuestProjection` status/deadline equal the aggregate's; the event's `expired_at` equals that deadline; `rebuild_learner_state` and `SQLiteLearnerProjection.state` are identical before and after the sweep | yes |
| 1b | `test_quest_reader_reports_expired_with_or_without_as_of` (extra) | yes |
| 2 | `test_replay_after_a_sweep_has_identical_quest_instances_with_other_quests` (one swept, one not due; fresh app) | yes |
| 3 | `test_full_m4_path_expiry_does_not_reset_the_14_day_window` (assign → sweep at deadline+1s → second attempt at +10 d → `no_action`/`remediated_within_window`, no second quest) | yes |
| 4 | `test_full_m4_path_assigns_again_after_14_days` (second attempt at exactly +14 d → assign; new quest `remediation_quest:att-2`, first stays expired) | yes |

**What "readers agree" can and cannot show (item 1):** none of the readers carries `expired_at`. `AssignmentRecord` has no such field, the aggregate instance does not store it, and
`rebuild_learner_state` / `SQLiteLearnerProjection` fold only XP, streak, freezes, activity dates, completed quest ids and badges. So the test checks what exists:
status and `deadline_at` agree across the aggregate and `LearnerQuestProjection`; the event's `expired_at` equals that deadline; and both replay readers are unchanged by a sweep
(expiry cannot alter them). It cannot assert a reader-held `expired_at`.

**Fixes:** none.

## 18. Commands and exact output

```
$ python -m pytest tests/interventions/test_quest_expiry.py -q     (edge cases, before any fix)
22 passed in 0.27s

$ python -m pytest -q
........................................................................ [ 84%]
........................................................................ [ 94%]
............................................                             [100%]
762 passed, 2 skipped, 1 deselected in 22.42s

$ python -m pytest -m integration -q
.                                                                        [100%]
1 passed, 764 deselected in 4.36s
```
(Only the last progress rows of the full run are reproduced.) Nothing failed, so no regression to report. 757 (end of prompt 3) + 5 new = 762.

## 19. Summary of Step 4

- `Learner.expire_due_quests(as_of)` reuses the `QuestExpired` event: strict `deadline_at < as_of`, sorted order, `expired_at = deadline_at`, naive `as_of` raises, never touches completed/expired quests.
- `run_quest_expiry(app, learner_id, *, clock)` saves only if something expired.
- Lazy expiry in `complete_lesson` now stamps the deadline too.
- Contract documented; nothing schedules the sweep.

## 20. Final test counts

- `tests/interventions`: 104 passed (19 ingestion, 18 policy, 15 service, 20 quest, 10 end-to-end, 22 expiry).
- Full suite: 762 passed, 2 skipped, 1 deselected. Integration marker: 1 passed.
  (`python -m pytest tests/interventions -q` after this prompt: 104 passed in 0.77s.)

## 21. Open questions / unsure

- Nothing triggers `run_quest_expiry`; a scheduler or per-request hook is a later decision, as is iterating over all learners.
- Historical `QuestExpired` events keep their completion-time `expired_at`, so streams hold mixed values; harmless today because nothing reads it.
- No reader exposes `expired_at`; if a read model ever needs it, `AssignmentRecord` and the projection would need a field (design change, not made).
- The `docs/event-contracts.md` sentence "like reconciliation" refers to `src/leaderboard/reconciliation.py`, which I did not open; I only confirmed it exists.
- The docs state the window is unaffected by expiry (tested); they do not discuss a learner who completes the remediation quest, which also does not reset the window (untested).
- The decision-time `deadline_at` and a later hand-assigned quest with a different deadline are covered only by the generic sweep tests.
- Still unspecified: `run_quest_expiry` for an unknown learner id.
