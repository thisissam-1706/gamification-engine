# Week 4 Fix 5: Persisted save/reload evidence for the state-machine readers

Status: **done. Finding F1 fixed. Not committed.**

- `tests/state_machine/test_persisted_reload_evidence.py` has 11 tests (S1–S5,
  D ×5, E1), and **all 11 pass**.
- Every hand-computed value matched the code on the first run.
- S5 found F1: `BadgeAwarded.qualifying_action_ids` was decided as a `tuple`
  but read back from the event store as a `list`. It is fixed with a
  two-line change in `src/state_machine/learner.py` (see "Finding F1").
- Suite excluding `tests/leaderboard` and `tests/notifications`: **175 passed,
  0 failed, 4 skipped**.

Week 3 review, deliverable 1: show that every state-machine reader gives the
same, hand-checked answer after a real on-disk SQLite save, a full restart
and a reload.

HEAD at planning time: `3e4b097 fix(m7): harden exp_007 primary metric and
document the real assignment hash`. The working tree was clean.

## Goal

For each scenario in one new file,
`tests/state_machine/test_persisted_reload_evidence.py`:

1. open a `LearnerApplication` on a SQLite file in `tmp_path`;
2. run real aggregate commands (`complete_lesson`, `acquire_freeze`,
   `assign_quest`) and `save`;
3. record the exact state and the per-type count of stored events;
4. close everything and drop every in-memory object;
5. open a **new** `LearnerApplication` on the same file and read the state
   again through every reader;
6. assert the post-restart state equals **both** the pre-restart state and a
   hand-written expected state.

## Readers in scope

| # | Reader | Where | What it returns |
|---|---|---|---|
| R1 | `Learner` aggregate via `app.repository.get(id)` | `src/state_machine/learner.py` | handler-folded fields: `new_streak`, `freezes_remaining`, `last_activity_date`, `streak_outcomes`, `streak_revisions`, `xp_total`, `completed_lesson_count`, `activity_dates`, `freeze_ids`, `quest_instances`, `quest_progress`, `completed_quest_ids`, `badges_issued`, `streak_before_break` |
| R2 | `LearnerStreakProjection` | `src/state_machine/projection.py` | `LearnerStreakSummary(new_streak, freezes_remaining)` |
| R3 | `LearnerQuestProjection.get_summary(id, as_of=...)` | `src/state_machine/projection.py` | `LearnerQuestSummary` with per-assignment `AssignmentRecord`s |
| R4 | `LearnerCompletionProjection` | `src/state_machine/projection.py` | `completed_lesson_counts[id]` |
| R5 | `rebuild_learner_state(events)` | `src/state_machine/projection.py` | `LearnerReplayState(xp_total, new_streak, freezes_remaining, activity_dates, completed_quests, badges_issued)` |
| R6 | `SQLiteLearnerProjection(db).state(id)` | `src/state_machine/projection.py` | the replay state stored as JSON in the `learner_projection` table. This is itself an on-disk read model, so after the restart it is read back **without** rebuilding |

`LearnerApplication` (`learner.py:453`) is a plain `eventsourcing.Application`
subclass with no overrides. With eventsourcing 9.5.5 and no
`AGGREGATE_CACHE_MAXSIZE` set, `Repository.cache` is `None`
(`.venv/.../eventsourcing/application.py:232-234`). So every
`repository.get` replays from the event store, even within the same app
object. That matters for the "same-app" rows below: those tests do read
from SQLite, but in the same process, connection and app object, without a
close/reopen.

## 1. What is already proven, and what is not

### Tests that really close and reopen the SQLite file

I used an AST scan for test functions that construct `LearnerApplication`
two or more times. These six are the only ones:

| Test | What is written | What is read after reopening | Readers |
|---|---|---|---|
| `tests/state_machine/test_sqlite_spike.py:26` `test_learner_state_survives_a_sqlite_application_restart` | one undated `complete_lesson` | `completed_lesson_count`, `completed_lesson_ids`, `lesson_completion_counts`, `xp_total`, `awarded_reward_ids`, `applied_rule_versions` | R1 only |
| `tests/ingestion/test_dedup.py:49` `test_resend_after_ingestion_window_cannot_issue_a_second_xp_reward` | one undated completion | the same completion resent → `False`, `save == []`, stored event count unchanged, 1 `XPAwarded` | R1 + raw event counts |
| `tests/ingestion/test_dedup.py:100` `test_retry_after_persistence_before_acknowledgement_has_one_reward` | one undated completion | the same completion retried → `False`, `save == []`, 1 `LessonCompleted`, 1 `XPAwarded`, `xp_total` | R1 + raw event counts |
| `tests/ingestion/test_ingestion_service.py:246` `test_concurrent_duplicate_completion_is_acknowledged_as_one_duplicate` | two forked writers, same completion | 1 `LessonCompleted`, 1 `XPAwarded`, `xp_total == 100`, evidence row | R1 + raw event counts |
| `tests/ingestion/test_ingestion_service.py:292` `test_concurrent_different_completions_retry_without_lost_update` | two forked writers, different completions | 2 + 2 events, `xp_total == 200` | R1 + raw event counts |
| `tests/state_machine/test_quest.py:221` `test_quest_expiry_is_durable_across_restart_and_rebuild` | one assignment, one late lesson → `QuestExpired` | `quest_instances[...]["status"] == "expired"`, `quest_progress == 0`, total event count unchanged. A `LearnerQuestProjection` is rebuilt **but nothing on it is asserted** | R1 (quest status/progress) + total count |

### Same-app persisted replay (reads SQLite, but no restart)

| Test(s) | Readers exercised on stored events |
|---|---|
| `test_streak_updates.py` (8 tests), `test_late_event_streak.py` (2), `test_streak_contract.py` (t2–t5, t7), `test_out_of_order.py`, `test_badge_idempotency.py`, `test_replay_checksum.py` | R1 via same-app `repository.get`; R5. Streak values, freezes, late corrections, badges |
| `test_streak_updates.py:186` `test_streak_projection_rebuild` | R2 on stored events |
| `test_replay_no_decide.py:20` | R1, R2, R3, R4, R5 on stored events, decision functions patched to fail |
| `test_quest.py` (assignment, lifecycle) | R1 (quest progress/status) |
| `test_projection_rebuild.py` | R4: clear and rebuild from stored events |
| `test_atomic_rebuild.py` (2 tests) | R6 staging/swap, but always read on the **same** `sqlite3` connection that wrote it |
| `test_rule_versioning.py:10` | R5 `xp_total` from the stored `XPAwarded`. (The test changes `XP_AMOUNTS["v1"]`, which nothing reads: `xp_amount_for` uses `RULESETS`. The "rule change" doesn't affect anything, so the test is weaker than its docstring suggests. Not a reload issue; noted only.) |
| `test_active_state_path.py` ingestion tests | R1 via same-app `repository.get` |

### In memory only (no persistence)

`test_streak_reader.py` (14 tests) and `test_quest_reader.py` (12 tests) build
events with `learner.collect_events()`. They never touch SQLite.

### What is NOT proven across a real restart

| Gap | Why it matters |
|---|---|
| **Streak state.** No restart test reads `new_streak`, `freezes_remaining`, `last_activity_date`, `streak_outcomes` or `streak_revisions` | these are folded by `_update_streak`, which recomputes `freezes_remaining` on every outcome (the Fix 2 change). Only a same-app replay has exercised that |
| **Freeze consumption** after a restart, in order or through a late correction | the Fix 2 aggregate bug lived exactly here |
| **Late events / superseding outcomes** (revision > 1, `supersedes` set) after a restart | R2/R5 choose by highest revision, and R1 depends on handler order |
| **R2, R3, R4, R5** after a restart: never asserted. R3 is rebuilt once with no assertion | the deliverable asks for every reader |
| **R6** read back through a new connection after the app closed | today the read model is only read on the connection that wrote it |
| **Quest states other than expired** after a restart: completed (with `completed_at`), progressed, still-assigned | the only restart quest test has one expired assignment with progress 0 |
| **Read-time expiry (`as_of`)** after a restart | `deadline_at` must survive as an aware datetime for `as_of` comparisons |
| **Resending a whole input batch** after a restart (lessons + freezes + quest assignments) | proven only for a single undated completion |
| **Per-type event counts and event order** after a restart | only totals or 1–2 types are checked today |
| Badges after a restart (`BadgeAwarded.qualifying_action_ids` is a tuple) | the JSON round-trip may turn it into a list. Covered by S5 (added at review) |

## 2. Proposed scenarios for `tests/state_machine/test_persisted_reload_evidence.py`

### Shared protocol (every scenario)

```text
db  = tmp_path / "events.db"
env = {"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": str(db)}

# Phase 1: write
app1 = LearnerApplication(env=env)
learner = Learner(); run the scenario's command batch; app1.save(learner)
learner_id = learner.id
events1 = list(app1.events.get(learner_id))
proj1 = SQLiteLearnerProjection(str(db)); proj1.rebuild(str(learner_id), events1)
pre = snapshot(app1.repository.get(learner_id), events1, proj1.state(str(learner_id)))
proj1.close(); app1.close()
del learner, app1, proj1, events1; gc.collect()

# Phase 2: reopen and read
app2 = LearnerApplication(env=env)
events2 = list(app2.events.get(learner_id))
proj2 = SQLiteLearnerProjection(str(db))          # no rebuild: read what is on disk
post = snapshot(app2.repository.get(learner_id), events2, proj2.state(str(learner_id)))

assert pre == EXPECTED
assert post == EXPECTED
assert post == pre
```

`snapshot(...)` returns a plain dict with:

- **R1:** the aggregate fields listed above (`streak_outcomes` as a dict of
  tuples, `quest_instances` as a dict of dicts);
- **`event_counts`:** a `Counter` of `type(e).__name__` over the stored
  events;
- **`event_sequence`:** the list of `(type(e).__name__, e.originator_version)`;
- **R2:** `(new_streak, freezes_remaining)` from a `LearnerStreakProjection`
  rebuilt on the stored events;
- **R3:** `get_summary(id, as_of=t)` for each `as_of` the scenario lists
  (including `None`);
- **R4:** `completed_lesson_counts[id]`;
- **R5:** `rebuild_learner_state(events)` as a tuple;
- **R6:** the JSON dict from `SQLiteLearnerProjection.state(id)`.

`EXPECTED` is written out literally per scenario. The only runtime-filled
value is the learner id inside the `"streak:{id}:{date}:{rev}"` strings.

Also asserted in every scenario:

- the file at `db` exists after phase 1;
- `app2 is not app1`, by construction.

(The `weakref` check from the first draft was dropped at review.)

### Scenario S1: streak with a freeze consumed (in order)

Commands (`lesson_id="lesson-1"` throughout):

| # | Command |
|---|---|
| 1 | `complete_lesson("a1", ..., activity_date="2026-09-01")` |
| 2 | `complete_lesson("a2", ..., activity_date="2026-09-02")` |
| 3 | `acquire_freeze("f1", activity_date="2026-09-02")` |
| 4 | `complete_lesson("a4", ..., activity_date="2026-09-04")` (Sep 3 missing) |

### Scenario S2: late lesson supersedes later streak outcomes

| # | Command |
|---|---|
| 1 | `complete_lesson("b1", ..., activity_date="2026-09-01")` |
| 2 | `complete_lesson("b3", ..., activity_date="2026-09-03")` |
| 3 | `complete_lesson("b4", ..., activity_date="2026-09-04")` |
| 4 | `complete_lesson("b2", ..., activity_date="2026-09-02")` (late) |

### Scenario S3: freeze consumed by an earlier-date correction (Fix 2 regression, persisted)

| # | Command |
|---|---|
| 1 | `complete_lesson("c0", ..., activity_date="2026-10-05")` |
| 2 | `complete_lesson("c1", ..., activity_date="2026-10-03")` |
| 3 | `acquire_freeze("f2", activity_date="2026-10-02")` |
| 4 | `complete_lesson("c3", ..., activity_date="2026-10-01")` |

This is the minimal sequence from Fix 2. Before that fix the aggregate
reported `freezes_remaining == 1` here. This scenario shows the corrected value
survives a restart.

### Scenario S4: quests in assigned, progressed, completed and expired states

Quest `lesson_starter_v1` (target 3). Lessons are given `occurred_at` with
**no** `timezone` and no `activity_date`, so `activity_date` is `None`: no
streak events, which keeps the quest counts isolated.

| # | Command |
|---|---|
| 1 | `assign_quest(QUEST, assignment_id="q-done",     assigned_at="2026-10-01T00:00:00Z", deadline_at="2026-10-08T00:00:00Z")` |
| 2 | `assign_quest(QUEST, assignment_id="q-expired",  assigned_at="2026-10-01T00:00:00Z", deadline_at="2026-10-02T00:00:00Z")` |
| 3 | `assign_quest(QUEST, assignment_id="q-progress", assigned_at="2026-10-04T00:00:00Z", deadline_at="2026-10-20T00:00:00Z")` |
| 4 | `assign_quest(QUEST, assignment_id="q-assigned", assigned_at="2026-10-10T00:00:00Z", deadline_at="2026-10-15T00:00:00Z")` |
| 5 | `complete_lesson("c1", "lesson-1", occurred_at="2026-10-01T10:00:00Z")` |
| 6 | `complete_lesson("c2", "lesson-1", occurred_at="2026-10-03T10:00:00Z")` |
| 7 | `complete_lesson("c3", "lesson-1", occurred_at="2026-10-05T10:00:00Z")` |
| 8 | `complete_lesson("c4", "lesson-1", occurred_at="2026-10-06T10:00:00Z")` |

R3 is read with `as_of` ∈ {`None`, `2026-10-14T00:00Z`, `2026-10-15T00:00Z`
(exactly the q-assigned deadline), `2026-10-16T00:00Z`, `2026-10-21T00:00Z`}.

### Scenario S5: 7-day consecutive streak that awards a badge (added at review)

| # | Command |
|---|---|
| 1–7 | `complete_lesson("e{k}", "lesson-1", activity_date="2026-09-0{k}")` for k = 1..7 |

Extra assertions on the badge's `qualifying_action_ids`, taken from three
places:

- **in memory:** `learner.pending_events` before `app1.save`, i.e. the value
  the aggregate decided;
- **phase 1 stored:** `app1.events.get(...)`, already decoded from SQLite;
- **phase 2 stored:** `app2.events.get(...)` after the restart.

The test asserts that the phase-2 value equals the in-memory value **and has
the same type**. If the type changes (tuple → list), the test stays failing and
the result is reported as a finding. It is not to be fixed in this step.

### Scenario D: resend the whole input batch after the restart

Parametrized over S1–S5. After phase 2:

1. replay the scenario's **entire** command list, in the same order, on the
   reloaded aggregate (`app2.repository.get(learner_id)`);
2. `app2.save(reloaded)`;
3. close, then reopen a third time (`app3`) and take a snapshot.

Asserts:

- every resent command returns `False`;
- `app2.save(...) == []`;
- `event_counts` and `event_sequence` are unchanged;
- the `app3` snapshot equals the scenario's `EXPECTED`.

### E1: subprocess reload, S3 only (approved)

After phase 1 of S3, run a separate Python process
(`sys.executable -c <script>`, repo root on `PYTHONPATH`, **not** `-I`). It
opens the same SQLite file and prints a JSON snapshot. The test compares that
JSON with S3's hand-written expected values (converted to JSON form) and with
the in-process pre-restart snapshot. No object, module or connection from
phase 1 exists in that process.

E2 (two learners in one file) was dropped at review.

## 3. Expected values (worked out by hand, not by running the code)

Rules used:

- **`calculate_streak`** (`rules.py`): dates are processed in ascending order.
  A gap of `n` missing days is bridged only if at least `n` unconsumed
  freezes were acquired on or before the later date. Freezes are consumed in
  `(acquired_date, freeze_id)` order. Each date gets
  `(streak, remaining = total freezes − consumed so far, used_for_date)`.
- **`complete_lesson` emission** (`learner.py:121-144`):
  - the lesson's own date is always emitted, with revision `old + 1`, or 1 if
    new;
  - a **later** date whose outcome changed is re-emitted with revision
    `old + 1`, `previous_streak = old[3]` and `supersedes = old[4]`;
  - an **earlier** date with an existing outcome is never re-emitted;
  - `previous_streak` for the lesson's own date is the aggregate's
    `new_streak` before the call.
- **`_update_streak`:** moves `new_streak` / `last_activity_date` only when the
  date is ≥ `last_activity_date`. It recomputes `freezes_remaining =
  len(freeze_acquisitions) − Σ consumed` over stored outcomes.
- **`acquire_freeze`** emits only `FreezeAcquired`, never `StreakUpdated`.
- **Badges:** threshold 7, so none of these scenarios issue a badge. XP is 100
  per accepted lesson (rules v1).
- **R2/R5:** per date, the highest revision wins. The streak comes from the
  latest date's winner. Freezes = distinct freeze ids − Σ consumed of the
  winners.

Notation: outcome tuple = `(new_streak, freezes_remaining, freezes_consumed,
previous_streak, outcome_id, supersedes)`, where `outcome_id =
"streak:{id}:{date}:{rev}"`.

### S1 expected

Step by step:

| Step | `calculate_streak` over all dates | Emitted `StreakUpdated` |
|---|---|---|
| a1 (09-01) | 01:(1,0,0) | 09-01 rev1: (1, 0, 0), prev 0 |
| a2 (09-02) | 01:(1,0,0) 02:(2,0,0) | 09-02 rev1: (2, 0, 0), prev 1 (09-01 is earlier with an existing outcome → skipped) |
| f1 | (no streak calculation) | none; `FreezeAcquired(f1, 2026-09-02)` |
| a4 (09-04) | 01:(1,1,0) 02:(2,1,0) 04: gap 1, f1 (acquired 09-02 ≤ 09-04) consumed → (3,0,1) | 09-04 rev1: (3, 0, 1), prev 2. 01 and 02 are earlier → not re-emitted, even though their "remaining" would now read 1 |

| Item | Expected |
|---|---|
| `event_counts` | `Created 1, LessonCompleted 3, XPAwarded 3, StreakUpdated 3, FreezeAcquired 1` (total 11) |
| R1 `new_streak` / `freezes_remaining` / `last_activity_date` | 3 / 0 / `"2026-09-04"` |
| R1 `streak_revisions` | `{"2026-09-01": 1, "2026-09-02": 1, "2026-09-04": 1}` |
| R1 `streak_outcomes` | 09-01: `(1, 0, 0, 0, "streak:{id}:2026-09-01:1", None)`; 09-02: `(2, 0, 0, 1, "streak:{id}:2026-09-02:1", None)`; 09-04: `(3, 0, 1, 2, "streak:{id}:2026-09-04:1", None)` |
| R1 other | `xp_total 300`, `completed_lesson_count 3`, `activity_dates {"2026-09-01","2026-09-02","2026-09-04"}`, `freeze_ids {"f1"}`, `freeze_acquisitions {"f1": "2026-09-02"}`, `streak_before_break None`, `badges_issued {}`, no quests |
| R2 | `(3, 0)`: latest date 09-04 → 3; 1 freeze id − Σ consumed 1 = 0 |
| R3 (`as_of=None`) | empty summary: `assigned_quests ()`, `completed_quests ()`, `quest_progress {}`, `assignments {}` |
| R4 | 3 |
| R5 | `xp_total 300, new_streak 3, freezes_remaining 0, activity_dates ("2026-09-01","2026-09-02","2026-09-04"), completed_quests (), badges_issued {}` |
| R6 | `{"xp_total": 300, "new_streak": 3, "freezes_remaining": 0, "activity_dates": ["2026-09-01","2026-09-02","2026-09-04"], "completed_quests": [], "badges_issued": {}}` |

### S2 expected

| Step | `calculate_streak` | Emitted `StreakUpdated` |
|---|---|---|
| b1 (09-01) | 01:1 | 09-01 rev1: (1,0,0), prev 0 |
| b3 (09-03) | 01:1, 03: gap 1, no freezes → 1 | 09-03 rev1: (1,0,0), prev 1 |
| b4 (09-04) | 01:1, 03:1, 04:2 | 09-04 rev1: (2,0,0), prev 1 |
| b2 (09-02, late) | 01:1, 02:2, 03:3, 04:4 | 09-02 rev1: (2,0,0), prev 2 (`new_streak` before the call). 09-03 rev2: (3,0,0), prev 1 (old[3]), supersedes `"streak:{id}:2026-09-03:1"`. 09-04 rev2: (4,0,0), prev 1 (old[3]), supersedes `"streak:{id}:2026-09-04:1"`. 09-01 is earlier → skipped |

Handler effect of b2: 09-02 and 09-03 are earlier than `last_activity_date`
(09-04), so `new_streak` doesn't move for them. 09-04 rev2 sets
`new_streak = 4`.

| Item | Expected |
|---|---|
| `event_counts` | `Created 1, LessonCompleted 4, XPAwarded 4, StreakUpdated 6` (total 15) |
| R1 `new_streak` / `freezes_remaining` / `last_activity_date` | 4 / 0 / `"2026-09-04"` |
| R1 `streak_revisions` | `{"2026-09-01": 1, "2026-09-02": 1, "2026-09-03": 2, "2026-09-04": 2}` |
| R1 `streak_outcomes` | 09-01: `(1, 0, 0, 0, "streak:{id}:2026-09-01:1", None)`; 09-02: `(2, 0, 0, 2, "streak:{id}:2026-09-02:1", None)`; 09-03: `(3, 0, 0, 1, "streak:{id}:2026-09-03:2", "streak:{id}:2026-09-03:1")`; 09-04: `(4, 0, 0, 1, "streak:{id}:2026-09-04:2", "streak:{id}:2026-09-04:1")` |
| R1 other | `xp_total 400`, `completed_lesson_count 4`, `activity_dates` Sep 1–4, `freeze_ids set()`, `streak_before_break None` (the streak never drops to 1 from above 1), `badges_issued {}` |
| R2 | `(4, 0)`: winners 01 r1, 02 r1, 03 r2, 04 r2; latest 04 → 4 |
| R3 | empty summary |
| R4 | 4 |
| R5 | `xp_total 400, new_streak 4, freezes_remaining 0, activity_dates ("2026-09-01","2026-09-02","2026-09-03","2026-09-04"), completed_quests (), badges_issued {}` |
| R6 | `{"xp_total": 400, "new_streak": 4, "freezes_remaining": 0, "activity_dates": ["2026-09-01","2026-09-02","2026-09-03","2026-09-04"], "completed_quests": [], "badges_issued": {}}` |

### S3 expected

| Step | `calculate_streak` | Emitted `StreakUpdated` | R1 `freezes_remaining` after |
|---|---|---|---|
| c0 (10-05) | 05:(1,0,0) | 10-05 rev1: (1,0,0), prev 0 | 0 |
| c1 (10-03) | 03:(1,0,0), 05: gap 1, no freezes → (1,0,0) | 10-03 rev1: (1,0,0), prev 1. 10-05 unchanged → not re-emitted | 0 |
| f2 (acq. 10-02) | — | none; `FreezeAcquired(f2, 2026-10-02)` | 1 (`+= 1`) |
| c3 (10-01) | 01:(1,1,0); 03: gap 1 (10-02), f2 acquired 10-02 ≤ 10-03 → consumed → (2,0,1); 05: gap 1 (10-04), no freeze left → (1,0,0) | 10-01 rev1: (1,1,0), prev 1. 10-03 rev2: (2,0,1), prev 1 (old[3]), supersedes `"streak:{id}:2026-10-03:1"`. 10-05 unchanged → not re-emitted | after 10-01: 1 − 0 = 1; after 10-03 rev2: 1 − 1 = **0** |

| Item | Expected |
|---|---|
| `event_counts` | `Created 1, LessonCompleted 3, XPAwarded 3, FreezeAcquired 1, StreakUpdated 4` (total 12) |
| R1 `new_streak` / `freezes_remaining` / `last_activity_date` | 1 / **0** / `"2026-10-05"` |
| R1 `streak_revisions` | `{"2026-10-01": 1, "2026-10-03": 2, "2026-10-05": 1}` |
| R1 `streak_outcomes` | 10-01: `(1, 1, 0, 1, "streak:{id}:2026-10-01:1", None)`; 10-03: `(2, 0, 1, 1, "streak:{id}:2026-10-03:2", "streak:{id}:2026-10-03:1")`; 10-05: `(1, 0, 0, 0, "streak:{id}:2026-10-05:1", None)` |
| R1 other | `xp_total 300`, `completed_lesson_count 3`, `activity_dates {"2026-10-01","2026-10-03","2026-10-05"}`, `freeze_ids {"f2"}`, `freeze_acquisitions {"f2": "2026-10-02"}`, `streak_before_break None`, `badges_issued {}` |
| R2 | `(1, 0)`: winners 05 r1 (consumed 0), 03 r2 (consumed 1), 01 r1 (consumed 0); latest 05 → 1; 1 − 1 = 0 |
| R3 | empty summary |
| R4 | 3 |
| R5 | `xp_total 300, new_streak 1, freezes_remaining 0, activity_dates ("2026-10-01","2026-10-03","2026-10-05"), completed_quests (), badges_issued {}` |
| R6 | `{"xp_total": 300, "new_streak": 1, "freezes_remaining": 0, "activity_dates": ["2026-10-01","2026-10-03","2026-10-05"], "completed_quests": [], "badges_issued": {}}` |

This matches the ground truth recorded in Fix 2 (`calculate_streak` over the
full history gives (1, 0)).

### S4 expected

The quest loop visits assignments in sorted id order (`q-assigned`, `q-done`,
`q-expired`, `q-progress`). For each `status == "assigned"` assignment:
skip if `occurred < assigned_at`; emit `QuestExpired` if `occurred >
deadline`; otherwise `QuestProgressed(step + 1)`, plus `QuestCompleted` at 3.

| Lesson | q-assigned (10-10 → 10-15) | q-done (10-01 → 10-08) | q-expired (10-01 → 10-02) | q-progress (10-04 → 10-20) |
|---|---|---|---|---|
| c1 10-01T10Z | before assigned → skip | progress 1 | progress 1 | before assigned → skip |
| c2 10-03T10Z | skip | progress 2 | 10-03 > 10-02 → **QuestExpired** (`expired_at 2026-10-03T10:00:00+00:00`) | skip |
| c3 10-05T10Z | skip | progress 3 → **QuestCompleted** (`completed_at 2026-10-05T10:00:00+00:00`) | expired → skip | progress 1 |
| c4 10-06T10Z | skip | completed → skip | skip | progress 2 |

| Item | Expected |
|---|---|
| `event_counts` | `Created 1, QuestAssigned 4, LessonCompleted 4, XPAwarded 4, QuestProgressed 6, QuestCompleted 1, QuestExpired 1` (total 21); `StreakUpdated` 0 |
| R1 `quest_instances` | `q-done`: status `completed`, progress 3, `assigned_at 2026-10-01T00:00Z`, `deadline_at 2026-10-08T00:00Z`, `completed_at 2026-10-05T10:00Z`. `q-expired`: `expired`, progress 1, deadline 10-02T00:00Z, `completed_at None`. `q-progress`: `assigned`, progress 2, deadline 10-20T00:00Z. `q-assigned`: `assigned`, progress 0, deadline 10-15T00:00Z. All `quest_id` `lesson_starter_v1`, `definition_version` `"v1"`, all datetimes aware UTC |
| R1 `quest_progress` | `{"q-assigned": 0, "q-done": 3, "q-expired": 1, "q-progress": 2}` |
| R1 `completed_quest_ids` | `{"quest_completion:q-done", "quest-completed:q-done", "quest-completed:lesson_starter_v1"}` |
| R1 other | `xp_total 400`, `completed_lesson_count 4`, `activity_dates set()`, `new_streak 0`, `freezes_remaining 0` |
| R2 | no summary entry for the learner (no streak or freeze events). The snapshot records `(0, 0)` via `.get(id)` with a default. **Assumption, see below** |
| R4 | 4 |
| R5 | `xp_total 400, new_streak 0, freezes_remaining 0, activity_dates (), completed_quests ("lesson_starter_v1",), badges_issued {}` |
| R6 | `{"xp_total": 400, "new_streak": 0, "freezes_remaining": 0, "activity_dates": [], "completed_quests": ["lesson_starter_v1"], "badges_issued": {}}` |

R3, `LearnerQuestProjection.get_summary`. Every case has `assigned_quests ==
(QUEST,)*4`, `quest_progress == {"q-assigned": 0, "q-done": 3, "q-expired": 1,
"q-progress": 2}` and `completed_quests == (QUEST,)` (none of these depend on
`as_of`). Status per assignment:

| `as_of` | q-assigned (deadline 10-15) | q-done | q-expired | q-progress (deadline 10-20) |
|---|---|---|---|---|
| `None` | assigned | completed | expired | assigned |
| 2026-10-14T00:00Z (before) | assigned | completed | expired | assigned |
| 2026-10-15T00:00Z (exactly the deadline) | **assigned** (strict `>`) | completed | expired | assigned |
| 2026-10-16T00:00Z (after) | **expired** (read-time) | completed | expired | assigned |
| 2026-10-21T00:00Z | expired | completed | expired | **expired** |

`AssignmentRecord` details (all `as_of`): q-done `completed_at =
datetime(2026, 10, 5, 10, tzinfo=utc)`. Every other `completed_at` is `None`.
`assigned_at` / `deadline_at` are as listed above. R1 still says
`"assigned"` for q-assigned and q-progress at every `as_of`: read-time expiry
is never written back to the aggregate. The test asserts that difference
explicitly.

### S5 expected

Each lesson extends the run by one day. No gap, no freeze, and no later dates
exist when each lesson arrives, so every lesson emits exactly one
`StreakUpdated` (its own date, rev 1, `previous_streak` = the streak before).

| Lesson | Date | `calculate_streak` for that date | Emitted |
|---|---|---|---|
| e1 | 09-01 | (1,0,0) | `StreakUpdated` rev1 (1,0,0), prev 0 |
| e2 | 09-02 | (2,0,0) | rev1 (2,0,0), prev 1 |
| e3 | 09-03 | (3,0,0) | rev1 (3,0,0), prev 2 |
| e4 | 09-04 | (4,0,0) | rev1 (4,0,0), prev 3 |
| e5 | 09-05 | (5,0,0) | rev1 (5,0,0), prev 4 |
| e6 | 09-06 | (6,0,0) | rev1 (6,0,0), prev 5 |
| e7 | 09-07 | (7,0,0) | rev1 (7,0,0), prev 6, **and** `BadgeAwarded` (some outcome has streak 7 ≥ threshold 7; badge not yet issued) |

The `BadgeAwarded` fields (`learner.py:145-163`):

| Field | Expected |
|---|---|
| `award_id` | `"badge:week_warrior_v1"` |
| `badge_id` | `"week_warrior_v1"` |
| `criteria_version` | `"v1"` |
| `awarded_at` | `"2026-09-07T00:00:00+00:00"` (no `occurred_at` given → `f"{activity_date}T00:00:00+00:00"`) |
| `qualifying_action_ids` | `("e1", "e2", "e3", "e4", "e5", "e6", "e7")`, a **tuple** (`tuple(sorted(self.completed_lesson_ids))`; e7 is already recorded by `_record_lesson_completion` before the badge check). **After the F1 fix:** `["e1", …, "e7"]`, a `list` (`sorted(...)`) |

| Item | Expected |
|---|---|
| `event_counts` | `Created 1, LessonCompleted 7, XPAwarded 7, StreakUpdated 7, BadgeAwarded 1` (total 23) |
| R1 `new_streak` / `freezes_remaining` / `last_activity_date` | 7 / 0 / `"2026-09-07"` |
| R1 `streak_revisions` | every date 09-01..09-07 → 1 |
| R1 `streak_outcomes` | date 09-0k: `(k, 0, 0, k − 1, "streak:{id}:2026-09-0k:1", None)` |
| R1 `badges_issued` | `{"week_warrior_v1": "badge:week_warrior_v1"}` |
| R1 other | `xp_total 700`, `completed_lesson_count 7`, `activity_dates` Sep 1–7, `freeze_ids set()`, `streak_before_break None` |
| R2 | `(7, 0)` |
| R3 | empty summary |
| R4 | 7 |
| R5 | `xp_total 700, new_streak 7, freezes_remaining 0, activity_dates (Sep 1..7), completed_quests (), badges_issued {"week_warrior_v1": "badge:week_warrior_v1"}` |
| R6 | `{"xp_total": 700, "new_streak": 7, "freezes_remaining": 0, "activity_dates": [Sep 1..7], "completed_quests": [], "badges_issued": {"week_warrior_v1": "badge:week_warrior_v1"}}` |
| `qualifying_action_ids` | in memory: `("e1", …, "e7")`, type `tuple`. Phase 2 stored: must equal it **and** be a `tuple`. **After the F1 fix:** all three reads are `["e1", …, "e7"]`, type `list` |

### D expected

| Scenario | Resent commands | Each returns | `save` | Counts after `app3` reopen |
|---|---|---|---|---|
| S1 | a1, a2, f1, a4 | `False` (`completed_lesson_ids` / `freeze_ids` restored) | `[]` | unchanged (11) |
| S2 | b1, b3, b4, b2 | `False` | `[]` | unchanged (15) |
| S3 | c0, c1, f2, c3 | `False` | `[]` | unchanged (12) |
| S4 | 4 × `assign_quest`, c1–c4 | `False` (`assigned_quest_ids` / `completed_lesson_ids` restored) | `[]` | unchanged (21) |

| S5 | e1–e7 | `False` | `[]` | unchanged (23) |

In every case the full `app3` snapshot equals that scenario's `EXPECTED`.
For S5, resending also must not issue a second badge
(`badge_id in badges_issued` guards it, and the resent lessons return `False`
before reaching that check anyway).

## Planned files

| File | Change |
|---|---|
| `tests/state_machine/test_persisted_reload_evidence.py` | new: `snapshot()` helper, S1–S5 (one test each), D (parametrized ×5), E1 for S3 |
| `docs/audit/week4-fix5-persisted-reload.md` | this file |

No `src/` change is planned. If a hand-computed value turns out to differ from
the code, the plan is to stop and report it. The expected value is not to be
edited to match the code.

## Assumptions

- **No aggregate cache.** `AGGREGATE_CACHE_MAXSIZE` stays unset, so
  `repository.get` replays from SQLite (confirmed in eventsourcing 9.5.5
  source). The planned tests don't set it.
- **"Discard every in-memory object"** means: close `app1` and the phase-1
  `SQLiteLearnerProjection`, `del` the `Learner`, the apps, the projections
  and the event lists, then `gc.collect()`. The new app is constructed only
  after that. Module-level state (classes, rule tables) is shared in-process.
  E1 (subprocess) is the stronger option if wanted.
- **The R2 empty case.** For a learner with no streak/freeze events,
  `streak_summaries` has no key. The snapshot uses
  `streak_summaries.get(id, LearnerStreakSummary(0, 0))`, mirroring the Fix 2
  convention, so S4's expected R2 is `(0, 0)`.
- **Datetime equality.** `quest_instances[...]["assigned_at"]` etc. are
  rebuilt by `_as_datetime` from the stored ISO strings, so after reload they
  are aware UTC datetimes and equal by value to `datetime(..., tzinfo=utc)`.
- **`save` with nothing pending returns `[]`.** That is what
  `test_dedup.py` asserts today.
- **The event class name for the aggregate's creation event is `"Created"`.**
  `test_sqlite_spike.py` refers to it that way. To confirm when implementing.

## Uncertainty

- **Hand-computed values are not yet checked against the code.** Per the
  instructions I worked them out from the rules only. The S2 `previous_streak`
  values and the S3 per-step `freezes_remaining` are the easiest to get wrong;
  the step tables above show the reasoning.
- **The JSON round-trip of event attribute types.** S1–S4 only store `str`,
  `int` and `None` in events. S5 adds `BadgeAwarded.qualifying_action_ids`, a
  `tuple`, specifically to check whether it survives the JSON round-trip.
- **R6 and the event store share one file** through two separate SQLite
  connections. `test_atomic_rebuild.py` already does this. The new part is
  reading R6 through a fresh connection after both original connections
  closed.
- **Same-process restart** for S1, S2, S4 and S5 (a new app object and new
  SQLite connections, but the same Python process). S3 is also reloaded in a
  separate process (E1).
- **S4 uses lessons without `activity_date`**, so quests and streaks are not
  tested together after reload. A combined scenario could be added, at the
  cost of more hand computation.

## Implementation

### What the test file does

`tests/state_machine/test_persisted_reload_evidence.py` (written with no `src/` change; the later F1 fix changed two lines in `learner.py`):

| Piece | Behaviour |
|---|---|
| `_phase_one(db, commands, as_ofs)` | new `LearnerApplication`, new `Learner`. Runs the batch and asserts every command returns `True`. Reads the in-memory badge ids from `learner.pending_events`, then `save`s. Rebuilds a new `SQLiteLearnerProjection` on the stored events and snapshots (R1 via `repository.get`, R2–R6). Closes the projection and the app. Also asserts the writer's in-memory aggregate state equals the replayed one |
| `gc.collect()` between phases | only plain values cross the restart: the learner id, the snapshot dict and the badge-id values. No app, projection, aggregate or event object does |
| `_phase_two(db, learner_id, as_ofs)` | **new** `LearnerApplication` and **new** `SQLiteLearnerProjection` on the same file. The read model is **not** rebuilt: `state(id)` reads what phase 1 left on disk. Snapshots the same way, then closes |
| `_snapshot(...)` | `aggregate` (17 R1 fields), `event_counts`, `event_types` (in order), `event_versions`, `streak_projection` (R2), `quest_summaries` per `as_of` (R3), `completion_count` (R4), `replay` (R5 `LearnerReplayState`), `read_model` (R6 JSON) |
| Expected values | `_expected_s1` … `_expected_s5`, written out literally from the tables above. The learner id is the only runtime input (inside `"streak:{id}:{date}:{rev}"`). Nothing in the expected values calls the code under test |
| S1–S5 tests | headline checks first (streak, freezes, XP, event counts, R2, R6), then `pre == expected`, `post == expected`, `post == pre` |
| S5 extra | first version: in memory a `tuple`, then the same type after restart (a `FINDING` assertion, which failed). After the F1 fix: in-memory, stored-before and reloaded values are all `["e1", …, "e7"]` and all `list` (normal assertions) |
| D ×5 | phase 1, then a second app: reload, resend the whole batch (every result `False`), `save(...) == []`, close. Then `_phase_two` (a third app): counts and types unchanged, snapshot equals expected and pre |
| E1 | phase 1 of S3 in-process. Phase 2 runs in `subprocess.run([sys.executable, "-c", SCRIPT, db, id])` with the repo root on `PYTHONPATH` (not `-I`), `cwd` = repo root. The child imports `_phase_two` / `_jsonable` from the test module and prints the JSON snapshot. The parent asserts it equals S3's expected values in JSON form and the in-process pre-restart snapshot |

The test file also hand-writes the **event order** (`event_types`) and
asserts the versions run 1..n. The plan didn't list this; the order follows
from `complete_lesson`: `LessonCompleted`, `XPAwarded`, the `StreakUpdated`
events, then `BadgeAwarded`, then the quest events in sorted assignment-id
order.

### Results

```bash
.venv/bin/python -m pytest tests/state_machine/test_persisted_reload_evidence.py -v -p no:cacheprovider
```

(Filtered to PASSED/FAILED/`E`/summary lines.)

```
tests/state_machine/test_persisted_reload_evidence.py::test_s1_freeze_consumed_in_order_survives_restart PASSED [  9%]
tests/state_machine/test_persisted_reload_evidence.py::test_s2_late_lesson_superseding_outcomes_survives_restart PASSED [ 18%]
tests/state_machine/test_persisted_reload_evidence.py::test_s3_freeze_consumed_by_earlier_date_correction_survives_restart PASSED [ 27%]
tests/state_machine/test_persisted_reload_evidence.py::test_s4_quest_states_and_as_of_reads_survive_restart PASSED [ 36%]
tests/state_machine/test_persisted_reload_evidence.py::test_s5_badge_survives_restart_with_same_qualifying_ids FAILED [ 45%]
tests/state_machine/test_persisted_reload_evidence.py::test_d_resend_whole_batch_after_restart_emits_zero_events[S1] PASSED [ 54%]
tests/state_machine/test_persisted_reload_evidence.py::test_d_resend_whole_batch_after_restart_emits_zero_events[S2] PASSED [ 63%]
tests/state_machine/test_persisted_reload_evidence.py::test_d_resend_whole_batch_after_restart_emits_zero_events[S3] PASSED [ 72%]
tests/state_machine/test_persisted_reload_evidence.py::test_d_resend_whole_batch_after_restart_emits_zero_events[S4] PASSED [ 81%]
tests/state_machine/test_persisted_reload_evidence.py::test_d_resend_whole_batch_after_restart_emits_zero_events[S5] PASSED [ 90%]
tests/state_machine/test_persisted_reload_evidence.py::test_e1_s3_reloaded_in_a_separate_process PASSED [100%]
E       AssertionError: FINDING: qualifying_action_ids type changed across save/reload: in memory ('e1', 'e2', 'e3', 'e4', 'e5', 'e6', 'e7') (tuple), stored before restart ['e1', 'e2', 'e3', 'e4', 'e5', 'e6', 'e7'] (list), after restart ['e1', 'e2', 'e3', 'e4', 'e5', 'e6', 'e7'] (list)
E       assert <class 'list'> is <class 'tuple'>
E        +  where <class 'list'> = type(['e1', 'e2', 'e3', 'e4', 'e5', 'e6', ...])
E        +  and   <class 'tuple'> = type(('e1', 'e2', 'e3', 'e4', 'e5', 'e6', ...))
FAILED tests/state_machine/test_persisted_reload_evidence.py::test_s5_badge_survives_restart_with_same_qualifying_ids
========================= 1 failed, 10 passed in 0.81s =========================
```

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider --ignore=tests/leaderboard --ignore=tests/notifications
.venv/bin/python -m pytest -q -p no:cacheprovider --ignore=tests/leaderboard --ignore=tests/notifications --ignore=tests/state_machine/test_persisted_reload_evidence.py
```

```
FAILED tests/state_machine/test_persisted_reload_evidence.py::test_s5_badge_survives_restart_with_same_qualifying_ids
1 failed, 174 passed, 4 skipped in 2.93s
164 passed, 4 skipped in 2.20s
```

174 = 164 + the 10 passing new tests. No existing test changed state. The
slowest new test is E1 (0.31 s, the subprocess); the rest take ≤ 0.04 s each.

| Scenario | Hand-computed values vs code | Before == after restart |
|---|---|---|
| S1 freeze consumed in order | all match | yes |
| S2 late lesson, revisions 2 with `supersedes` | all match | yes |
| S3 freeze consumed by an earlier-date correction | all match (`freezes_remaining` 0 in R1, R2, R5, R6) | yes, in-process and in a separate process (E1) |
| S4 quests + 5 `as_of` reads | all match, including the boundary `as_of == deadline` → assigned and read-time expiry at 10-16 / 10-21 | yes |
| S5 7-day badge | all snapshot values match (badge in R1, R5, R6; 23 events) | yes for the snapshot; **type changed** for `qualifying_action_ids` (F1) |
| D resend ×5 | every resent command `False`, `save == []`, counts and order unchanged after a third app | yes |

### Finding F1: `BadgeAwarded.qualifying_action_ids` comes back as a `list`, now **fixed**

**Before the fix** (observed in the first implementation run):

| Where read | Value | Type |
|---|---|---|
| in memory (`learner.pending_events`, before `save`), i.e. the decided value | `('e1', 'e2', 'e3', 'e4', 'e5', 'e6', 'e7')` | `tuple` |
| expected (hand-written, from `tuple(sorted(self.completed_lesson_ids))`) | `('e1', 'e2', 'e3', 'e4', 'e5', 'e6', 'e7')` | `tuple` |
| stored, read by app 1 **before** the restart | `['e1', 'e2', 'e3', 'e4', 'e5', 'e6', 'e7']` | **`list`** |
| stored, read by app 2 **after** the restart | `['e1', 'e2', 'e3', 'e4', 'e5', 'e6', 'e7']` | **`list`** |

- **When it happens:** at the event store's encode/decode step, not at the
  restart. App 1 already gets a `list` when it reads its own saved event back.
  The elements and their order are unchanged.
- **Probable cause (not verified in the eventsourcing source):** the default
  JSON transcoder writes a tuple as a JSON array and decodes arrays as lists.
  `_award_badge` declares `qualifying_action_ids: tuple[str, ...]`
  (`learner.py:326`), so the stored event no longer matches its own
  annotation.
- **Impact today:** none on reader output. The `_award_badge` handler ignores
  `qualifying_action_ids`, and nothing else in `src/` or the tests reads it
  (grep: the only reference is that signature). R1 `badges_issued`, R5 and R6
  all match before and after. The risk is for future consumers: anything that
  compares, hashes or uses the value as a dict key, e.g. a checksum over
  event payloads, would see a different value from replay than at decision
  time.
- At first this was left unfixed, as instructed, with S5 failing on a
  `FINDING` assertion.

**Fix (status: fixed).** The decided value now has the type the store
returns: a sorted `list[str]`. Only `src/state_machine/learner.py` changed,
in the only two places that build or annotate the field (grep over `src/`,
`tests/` and `docs/`):

```diff
@@ -159,7 +159,7 @@ class Learner(Aggregate):
                     ruleset.badge_id,
                     rule_version,
                     awarded_at,
-                    tuple(sorted(self.completed_lesson_ids)),
+                    sorted(self.completed_lesson_ids),
                 )
@@ -323,7 +323,7 @@ class Learner(Aggregate):
         badge_id: str,
         criteria_version: str,
         awarded_at: str,
-        qualifying_action_ids: tuple[str, ...],
+        qualifying_action_ids: list[str],
     ) -> None:
```

The fix chose this over registering a tuple transcoding because it changes
nothing in the event store configuration. Already-stored events decode as
lists either way, so old and new events now agree with the annotation.

**After the fix** (observed, same scratch report re-run):

```
badge qualifying_action_ids | in_memory=['e1', 'e2', 'e3', 'e4', 'e5', 'e6', 'e7'] list | stored_before=['e1', 'e2', 'e3', 'e4', 'e5', 'e6', 'e7'] list | after=['e1', 'e2', 'e3', 'e4', 'e5', 'e6', 'e7'] list
```

| Where read | Value | Type |
|---|---|---|
| in memory (decided) | `['e1', 'e2', 'e3', 'e4', 'e5', 'e6', 'e7']` | `list` |
| stored, before the restart | `['e1', 'e2', 'e3', 'e4', 'e5', 'e6', 'e7']` | `list` |
| stored, after the restart | `['e1', 'e2', 'e3', 'e4', 'e5', 'e6', 'e7']` | `list` |

The S5 test now expects `S5_BADGE_IDS = ["e1", …, "e7"]` and asserts, as
normal assertions:

```python
assert in_memory == stored_before == reloaded == S5_BADGE_IDS
assert type(in_memory) is type(stored_before) is type(reloaded) is list
```

No other test changed outcome. Without the new file the suite is still
164 passed, 4 skipped. The other badge-related test files
(`test_badge_idempotency.py`, `test_live_vs_replay.py`,
`test_rule_versioning.py`) pass unchanged, and none of them reads
`qualifying_action_ids`.

### Plan items not done

- None of S1–S5, D or E1 is missing.
- The weakref check and E2 were dropped at review.

## Evidence per scenario: expected vs observed

**How the observed values were collected:** a scratch script (outside the
repo, no test or `src/` change) imports the test module and runs its own
`_phase_one` / `_phase_two` helpers in a temporary directory for each
scenario. For every value it prints the hand-written expected value, the
after-restart value, whether the before-restart value is the same, and a
verdict (`PASS` only if expected == before == after). It then runs D on the
same file: resend, `save`, and a third app.

In the quoted lines, the learner id inside outcome ids is shown as `{id}`.
`before: same` means the before-restart snapshot held exactly the same
value. Long dicts are abbreviated with `…` here; the full line is in the
script output.

### S1: freeze consumed in order

| Step | Expected | Observed (after restart) | Before = after | Result |
|---|---|---|---|---|
| R1 `new_streak` | 3 | `R1 new_streak \| expected=3 \| after=3 \| before:same \| PASS` | yes | PASS |
| R1 `freezes_remaining` | 0 | `after=0` | yes | PASS |
| R1 `last_activity_date` | `2026-09-04` | `after='2026-09-04'` | yes | PASS |
| R1 `xp_total` | 300 | `after=300` | yes | PASS |
| R1 `completed_lesson_count` | 3 | `after=3` | yes | PASS |
| R1 `streak_revisions` | 01:1, 02:1, 04:1 | `after={'2026-09-01': 1, '2026-09-02': 1, '2026-09-04': 1}` | yes | PASS |
| R1 `streak_outcomes` | 01 `(1,0,0,0,…:01:1,None)`, 02 `(2,0,0,1,…)`, 04 `(3,0,1,2,…)` | `'2026-09-04': (3, 0, 1, 2, 'streak:{id}:2026-09-04:1', None)` (all three match) | yes | PASS |
| R1 `freeze_acquisitions` | `{f1: 2026-09-02}` | `after={'f1': '2026-09-02'}` | yes | PASS |
| R2 streak projection | (3, 0) | `after=(3, 0)` | yes | PASS |
| R3 quest summary | empty | `after={None: {}}` | yes | PASS |
| R4 completions | 3 | `after=3` | yes | PASS |
| R5 replay | `(300, 3, 0, (09-01, 09-02, 09-04), (), {})` | `LearnerReplayState(xp_total=300, new_streak=3, freezes_remaining=0, activity_dates=('2026-09-01', '2026-09-02', '2026-09-04'), completed_quests=(), badges_issued={})` | yes | PASS |
| R6 read model (from disk, not rebuilt) | xp 300, streak 3, freezes 0 | `{'activity_dates': ['2026-09-01', '2026-09-02', '2026-09-04'], 'badges_issued': {}, 'completed_quests': [], 'freezes_remaining': 0, 'new_streak': 3, 'xp_total': 300}` | yes | PASS |
| event order / versions 1..n | as listed in the test | `event order == expected … True`, `event versions 1..n … True` | yes | PASS |
| full snapshot | — | `pre==expected True \| post==expected True \| post==pre True` | — | PASS |
| D resend | 4 × `False`, `save == []`, counts unchanged | `results=[False, False, False, False] \| save=[] \| … \| third==expected True` | — | PASS |

### S2: late lesson supersedes later outcomes

| Step | Expected | Observed (after restart) | Before = after | Result |
|---|---|---|---|---|
| R1 `new_streak` | 4 | `after=4` | yes | PASS |
| R1 `freezes_remaining` | 0 | `after=0` | yes | PASS |
| R1 `last_activity_date` | `2026-09-04` | `after='2026-09-04'` | yes | PASS |
| R1 `xp_total` | 400 | `after=400` | yes | PASS |
| R1 `streak_revisions` | 01:1, 02:1, **03:2, 04:2** | `after={'2026-09-01': 1, '2026-09-02': 1, '2026-09-03': 2, '2026-09-04': 2}` | yes | PASS |
| R1 `streak_outcomes` 09-02 | `(2,0,0,2,…:02:1,None)` | `'2026-09-02': (2, 0, 0, 2, 'streak:{id}:2026-09-02:1', None)` | yes | PASS |
| R1 `streak_outcomes` 09-03 | `(3,0,0,1,…:03:2, supersedes …:03:1)` | `'2026-09-03': (3, 0, 0, 1, 'streak:{id}:2026-09-03:2', 'streak:{id}:2026-09-03:1')` | yes | PASS |
| R1 `streak_outcomes` 09-04 | `(4,0,0,1,…:04:2, supersedes …:04:1)` | `'2026-09-04': (4, 0, 0, 1, 'streak:{id}:2026-09-04:2', 'streak:{id}:2026-09-04:1')` | yes | PASS |
| R2 streak projection | (4, 0) | `after=(4, 0)` | yes | PASS |
| R3 / R4 | empty / 4 | `{None: {}}` / `4` | yes | PASS |
| R5 replay | `(400, 4, 0, Sep 1–4, (), {})` | `LearnerReplayState(xp_total=400, new_streak=4, freezes_remaining=0, activity_dates=('2026-09-01', '2026-09-02', '2026-09-03', '2026-09-04'), …)` | yes | PASS |
| R6 read model | xp 400, streak 4, freezes 0 | `{…, 'freezes_remaining': 0, 'new_streak': 4, 'xp_total': 400}` | yes | PASS |
| event order / versions | as listed | `True` / `True` | yes | PASS |
| full snapshot | — | `pre==expected True \| post==expected True \| post==pre True` | — | PASS |
| D resend | 4 × `False`, `[]` | `results=[False, False, False, False] \| save=[] \| … third==expected True` | — | PASS |

### S3: freeze consumed by an earlier-date correction

| Step | Expected | Observed (after restart) | Before = after | Result |
|---|---|---|---|---|
| R1 `new_streak` | 1 | `after=1` | yes | PASS |
| R1 `freezes_remaining` | **0** (1 before the Fix 2 change) | `R1 freezes_remaining \| expected=0 \| after=0 \| before:same \| PASS` | yes | PASS |
| R1 `last_activity_date` | `2026-10-05` | `after='2026-10-05'` | yes | PASS |
| R1 `xp_total` | 300 | `after=300` | yes | PASS |
| R1 `streak_revisions` | 01:1, **03:2**, 05:1 | `after={'2026-10-01': 1, '2026-10-03': 2, '2026-10-05': 1}` | yes | PASS |
| R1 `streak_outcomes` 10-01 | `(1,1,0,1,…:01:1,None)` | `'2026-10-01': (1, 1, 0, 1, 'streak:{id}:2026-10-01:1', None)` | yes | PASS |
| R1 `streak_outcomes` 10-03 | `(2,0,1,1,…:03:2, supersedes …:03:1)` | `'2026-10-03': (2, 0, 1, 1, 'streak:{id}:2026-10-03:2', 'streak:{id}:2026-10-03:1')` | yes | PASS |
| R1 `streak_outcomes` 10-05 | `(1,0,0,0,…:05:1,None)` | `'2026-10-05': (1, 0, 0, 0, 'streak:{id}:2026-10-05:1', None)` | yes | PASS |
| R1 `freeze_acquisitions` | `{f2: 2026-10-02}` | `after={'f2': '2026-10-02'}` | yes | PASS |
| R2 streak projection | (1, 0) | `after=(1, 0)` | yes | PASS |
| R3 / R4 | empty / 3 | `{None: {}}` / `3` | yes | PASS |
| R5 replay | `(300, 1, 0, (10-01, 10-03, 10-05), (), {})` | `LearnerReplayState(xp_total=300, new_streak=1, freezes_remaining=0, activity_dates=('2026-10-01', '2026-10-03', '2026-10-05'), …)` | yes | PASS |
| R6 read model | xp 300, streak 1, freezes 0 | `{…, 'freezes_remaining': 0, 'new_streak': 1, 'xp_total': 300}` | yes | PASS |
| event order / versions | as listed | `True` / `True` | yes | PASS |
| full snapshot | — | `pre==expected True \| post==expected True \| post==pre True` | — | PASS |
| D resend | 4 × `False`, `[]` | `results=[False, False, False, False] \| save=[] \| … third==expected True` | — | PASS |
| E1 separate process (pytest) | child JSON == expected == pre | `test_e1_s3_reloaded_in_a_separate_process PASSED` | — | PASS |

### S4: quests in assigned, progressed, completed and expired states

| Step | Expected | Observed (after restart) | Before = after | Result |
|---|---|---|---|---|
| R1 status, progress | q-assigned (assigned, 0); q-done (completed, 3); q-expired (expired, 1); q-progress (assigned, 2) | `after={'q-assigned': ('assigned', 0), 'q-done': ('completed', 3), 'q-expired': ('expired', 1), 'q-progress': ('assigned', 2)}` | yes | PASS |
| R1 `completed_at` | q-done `2026-10-05T10:00Z`, others `None` | `after={'q-assigned': None, 'q-done': '2026-10-05T10:00:00+00:00', 'q-expired': None, 'q-progress': None}` | yes | PASS |
| R1 streak / freezes / XP | 0 / 0 / 400 | `after=0` / `after=0` / `after=400` | yes | PASS |
| R2 streak projection | (0, 0) | `after=(0, 0)` | yes | PASS |
| R3 `as_of=None` | assigned / completed / expired / assigned | `None: {'q-assigned': 'assigned', 'q-done': 'completed', 'q-expired': 'expired', 'q-progress': 'assigned'}` | yes | PASS |
| R3 `as_of=10-14` (before) | same as `None` | `'2026-10-14T00:00:00+00:00': {'q-assigned': 'assigned', …, 'q-progress': 'assigned'}` | yes | PASS |
| R3 `as_of=10-15` (= deadline) | q-assigned **assigned** | `'2026-10-15T00:00:00+00:00': {'q-assigned': 'assigned', …}` | yes | PASS |
| R3 `as_of=10-16` (after) | q-assigned **expired**, q-progress assigned | `'2026-10-16T00:00:00+00:00': {'q-assigned': 'expired', 'q-done': 'completed', 'q-expired': 'expired', 'q-progress': 'assigned'}` | yes | PASS |
| R3 `as_of=10-21` | q-progress **expired** too | `'2026-10-21T00:00:00+00:00': {'q-assigned': 'expired', 'q-done': 'completed', 'q-expired': 'expired', 'q-progress': 'expired'}` | yes | PASS |
| R3 `quest_progress` (all `as_of`) | `{q-assigned 0, q-done 3, q-expired 1, q-progress 2}` | `{'q-assigned': 0, 'q-done': 3, 'q-expired': 1, 'q-progress': 2}` for each of the 5 | yes | PASS |
| R4 completions | 4 | `after=4` | yes | PASS |
| R5 replay | `(400, 0, 0, (), ('lesson_starter_v1',), {})` | `LearnerReplayState(xp_total=400, new_streak=0, freezes_remaining=0, activity_dates=(), completed_quests=('lesson_starter_v1',), badges_issued={})` | yes | PASS |
| R6 read model | xp 400, `completed_quests ['lesson_starter_v1']` | `{'activity_dates': [], 'badges_issued': {}, 'completed_quests': ['lesson_starter_v1'], 'freezes_remaining': 0, 'new_streak': 0, 'xp_total': 400}` | yes | PASS |
| event order / versions | as listed | `True` / `True` | yes | PASS |
| full snapshot | — | `pre==expected True \| post==expected True \| post==pre True` | — | PASS |
| D resend | 8 × `False`, `[]` | `results=[False, False, False, False, False, False, False, False] \| save=[] \| … third==expected True` | — | PASS |

### S5: 7-day badge

| Step | Expected | Observed (after restart) | Before = after | Result |
|---|---|---|---|---|
| R1 `new_streak` / `freezes_remaining` | 7 / 0 | `after=7` / `after=0` | yes | PASS |
| R1 `xp_total` / `completed_lesson_count` | 700 / 7 | `after=700` / `after=7` | yes | PASS |
| R1 `streak_outcomes` | 09-0k: `(k,0,0,k−1,…:0k:1,None)` | `'2026-09-07': (7, 0, 0, 6, 'streak:{id}:2026-09-07:1', None)` (all seven match) | yes | PASS |
| R1 `badges_issued` | `{week_warrior_v1: badge:week_warrior_v1}` | `after={'week_warrior_v1': 'badge:week_warrior_v1'}` | yes | PASS |
| R2 streak projection | (7, 0) | `after=(7, 0)` | yes | PASS |
| R3 / R4 | empty / 7 | `{None: {}}` / `7` | yes | PASS |
| R5 replay | `(700, 7, 0, Sep 1–7, (), {week_warrior_v1: …})` | `LearnerReplayState(xp_total=700, new_streak=7, freezes_remaining=0, …, badges_issued={'week_warrior_v1': 'badge:week_warrior_v1'})` | yes | PASS |
| R6 read model | xp 700, streak 7, badge | `{…, 'badges_issued': {'week_warrior_v1': 'badge:week_warrior_v1'}, …, 'new_streak': 7, 'xp_total': 700}` | yes | PASS |
| event order / versions | as listed | `True` / `True` | yes | PASS |
| full snapshot | — | `pre==expected True \| post==expected True \| post==pre True` | — | PASS |
| `qualifying_action_ids` value | `['e1', …, 'e7']` (after the F1 fix) | `in_memory=['e1', …, 'e7'] list \| stored_before=['e1', …, 'e7'] list \| after=['e1', …, 'e7'] list` | yes | PASS |
| `qualifying_action_ids` type | `list` in all three reads (after the F1 fix) | `list` / `list` / `list` | yes | PASS (before the fix: `tuple` / `list` / `list`, FAIL, see F1) |
| D resend | 7 × `False`, `[]`, no second badge | `results=[False, False, False, False, False, False, False] \| save=[] \| counts_after_third_app={'BadgeAwarded': 1, …} \| third==expected True` | — | PASS |

## Emitted events per type, before and after the reload

Observed in the same run (`event_counts`). In every row, before the restart,
after the restart and after the D resend plus a third app are identical, and
all equal the expected counts.

| Type | S1 | S2 | S3 | S4 | S5 |
|---|---|---|---|---|---|
| `Created` | 1 → 1 | 1 → 1 | 1 → 1 | 1 → 1 | 1 → 1 |
| `LessonCompleted` | 3 → 3 | 4 → 4 | 3 → 3 | 4 → 4 | 7 → 7 |
| `XPAwarded` | 3 → 3 | 4 → 4 | 3 → 3 | 4 → 4 | 7 → 7 |
| `StreakUpdated` | 3 → 3 | 6 → 6 | 4 → 4 | 0 → 0 | 7 → 7 |
| `FreezeAcquired` | 1 → 1 | 0 → 0 | 1 → 1 | 0 → 0 | 0 → 0 |
| `QuestAssigned` | 0 → 0 | 0 → 0 | 0 → 0 | 4 → 4 | 0 → 0 |
| `QuestProgressed` | 0 → 0 | 0 → 0 | 0 → 0 | 6 → 6 | 0 → 0 |
| `QuestCompleted` | 0 → 0 | 0 → 0 | 0 → 0 | 1 → 1 | 0 → 0 |
| `QuestExpired` | 0 → 0 | 0 → 0 | 0 → 0 | 1 → 1 | 0 → 0 |
| `BadgeAwarded` | 0 → 0 | 0 → 0 | 0 → 0 | 0 → 0 | 1 → 1 |
| **Total** | **11 → 11** | **15 → 15** | **12 → 12** | **21 → 21** | **23 → 23** |
| After D resend + third app | 11 | 15 | 12 | 21 | 23 |

Quoted source lines (after restart):

```
S1 event_counts | after={'Created': 1, 'FreezeAcquired': 1, 'LessonCompleted': 3, 'StreakUpdated': 3, 'XPAwarded': 3} | before:same | PASS
S2 event_counts | after={'Created': 1, 'LessonCompleted': 4, 'StreakUpdated': 6, 'XPAwarded': 4} | before:same | PASS
S3 event_counts | after={'Created': 1, 'FreezeAcquired': 1, 'LessonCompleted': 3, 'StreakUpdated': 4, 'XPAwarded': 3} | before:same | PASS
S4 event_counts | after={'Created': 1, 'LessonCompleted': 4, 'QuestAssigned': 4, 'QuestCompleted': 1, 'QuestExpired': 1, 'QuestProgressed': 6, 'XPAwarded': 4} | before:same | PASS
S5 event_counts | after={'BadgeAwarded': 1, 'Created': 1, 'LessonCompleted': 7, 'StreakUpdated': 7, 'XPAwarded': 7} | before:same | PASS
```

(The scenario prefix is added here; the script prints each scenario under a
`===== S<n>` header.)

## What this proves / what it does not

**Proves:**

- For five hand-checked histories, every state-machine reader gives the same
  values after a close/reopen of the on-disk SQLite file as before it, and
  those values equal hand-computed expectations. The readers are the
  reloaded aggregate, the streak, quest and completion projections, the
  replay fold, and the persisted SQLite read model.
- The histories cover:
  - streaks;
  - a freeze consumed in order;
  - a freeze consumed by an earlier-date correction (the Fix 2 bug, now
    checked persisted);
  - superseding revisions with their `supersedes` ids;
  - quests in all four states;
  - read-time `as_of` expiry, including the exact deadline;
  - a badge.
- The persisted read model (R6) is read back from disk through a new
  connection without being rebuilt.
- Per-type event counts, event order and versions are identical after the
  reload.
- Resending the whole input batch after a reload emits **zero** new events
  for every scenario. A third reopen still matches.
- For S3, the same holds when the reload happens in a **separate Python
  process** (E1).

**Does not prove:**

- **Single writer, single process** (except E1's read-only child). No
  concurrent writers or interleaved saves. The two ingestion tests cover
  concurrency only for completions and XP.
- **SQLite only.** No other persistence module (e.g. PostgreSQL) is
  exercised.
- **One learner per file.** Per-learner isolation on disk after reopening
  is not shown (E2 was dropped).
- **No snapshots or aggregate cache.** `IS_SNAPSHOTTING_ENABLED` and
  `AGGREGATE_CACHE_MAXSIZE` are unset, so every read replays all events.
- **No schema or rule changes between write and reload.** Same code, rules
  v1, same event classes.
- **Event attribute types are not preserved in general.** The store returns
  JSON arrays as lists. F1 was fixed by emitting a list in the first place,
  not by changing the store. Any tuple-valued event field added later would
  again come back as a list.
- **No crash in the middle of a save,** no partial writes, no corrupt file.
- **Lessons with `occurred_at` + `timezone`** (local-date derivation) are not
  part of these scenarios; S4 uses undated lessons and S1–S3/S5 use explicit
  `activity_date`.
- **The ingestion service and evidence store** are not involved. Commands go
  straight to the aggregate.

## Reproduce

```bash
.venv/bin/python -m pytest tests/state_machine/test_persisted_reload_evidence.py -v -p no:cacheprovider
```

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider --ignore=tests/leaderboard --ignore=tests/notifications
```

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider --ignore=tests/leaderboard --ignore=tests/notifications --ignore=tests/state_machine/test_persisted_reload_evidence.py
```

## Final test counts

| Scope | Passed | Failed | Skipped | Output |
|---|---|---|---|---|
After the F1 fix:

| Scope | Passed | Failed | Skipped | Output |
|---|---|---|---|---|
| `tests/state_machine/test_persisted_reload_evidence.py` | 11 | 0 | 0 | `11 passed in 0.63s` |
| Suite excluding `tests/leaderboard` and `tests/notifications` | 175 | 0 | 4 | `175 passed, 4 skipped in 2.41s` |
| Same, without the new file | 164 | 0 | 4 | `164 passed, 4 skipped in 2.22s` |

175 = 164 + the 11 new tests. Before the fix it was `1 failed, 174 passed,
4 skipped` (S5).

`git status --short` and `git diff --stat` (read-only):

```
 M src/state_machine/learner.py
?? docs/audit/week4-fix5-persisted-reload.md
?? tests/state_machine/test_persisted_reload_evidence.py
 src/state_machine/learner.py | 4 ++--
 1 file changed, 2 insertions(+), 2 deletions(-)
```

## Draft commit message (not made, no trailer)

Files:

```
src/state_machine/learner.py
tests/state_machine/test_persisted_reload_evidence.py
docs/audit/week4-fix5-persisted-reload.md
```

```
fix(m2): add persisted save/reload evidence and keep badge ids a list

Week 3 review deliverable 1. Only single-completion and quest-expiry
paths were checked across a real SQLite close/reopen; streak state,
superseding revisions, freeze consumption, most quest states, as_of
reads, the projections and the persisted read model were not.

Add tests/state_machine/test_persisted_reload_evidence.py. Each
scenario writes through a LearnerApplication on a SQLite file in
tmp_path, snapshots the reloaded aggregate, per-type event counts,
event order, the streak/quest/completion projections, the replay fold
and the persisted SQLite read model, closes everything, reopens the
same file with new objects and snapshots again. Both snapshots must
equal hand-computed expected values and each other.

Scenarios: freeze consumed in order (S1), late lesson superseding later
outcomes (S2), freeze consumed by an earlier-date correction (S3, also
reloaded in a separate process), quests in assigned/progressed/
completed/expired states read at five as_of times (S4), and a 7-day
badge (S5). Resending each whole batch after reload emits zero events.

S5 found that BadgeAwarded.qualifying_action_ids was decided as a
tuple but read back from the event store as a list, so the replayed
value differed in type from the decided one. Emit a sorted list and
annotate it list[str]; in-memory, stored and reloaded values are now
the same list. Details in docs/audit/week4-fix5-persisted-reload.md
(finding F1).
```
