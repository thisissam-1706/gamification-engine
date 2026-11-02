# Week 6 / M4 / Step 3 — remediation quest definition

Status: **Step 3 complete (prompt 5): edge cases added, full suite green, event contract documented.** (Prompt 2: quest definitions + kind filter implemented.) (Prompt 1: failing tests only.)
Git rule in force: no state-changing git commands (none were run).

## 1. Changes made

- Created this file and `tests/interventions/test_remediation_quest.py` (16 tests). Nothing under `src/` changed.

## 2. Before / after behaviour

No behaviour change. Current behaviour is described in §3 (read from the code, not changed).

## 3. What I read

### 3.1 How `quest_target_for` resolves a quest (`src/state_machine/rules.py:220-227`)

`quest_target_for(quest_id, rule_version="v1")` calls `get_ruleset(rule_version)` (raises `KeyError("Unknown rule version: …")` if the version is not in
`RULESETS`), then looks `quest_id` up in `ruleset.quest_targets` (raises `KeyError("Unknown quest: …")`). So a quest's **definition_version is a rule-set
version** (`v1`, plus `v2…` after `register_decay_rulesets()`); targets live in `rulesets.json` / `rulesets_decay.json`
(`"quest_targets": {"lesson_starter_v1": 3}`). There is no place for a non-rule-set quest or for a per-quest kind filter.
`QUEST_DEFINITIONS` is a legacy compatibility view of the same single target.

### 3.2 `Learner.assign_quest` (`learner.py:494-523`)

`assign_quest(quest_id, *, assignment_id=None, assigned_at=None, deadline_at=None, definition_version="v1") -> bool`.
`assignment_id` defaults to `quest:<quest_id>`; an already-assigned id returns False (idempotent). `assigned_at` is required (ValueError).
It calls `quest_target_for(quest_id, definition_version)` purely to validate (so an unknown quest/version raises KeyError **today**, which is why
assigning `remediation_v1` / `remediation_quests_v1` currently fails). Deadline defaults to assigned + 7 days. It emits `QuestAssigned`, whose fold stores
`quest_instances[assignment_id] = {assignment_id, quest_id, definition_version, status: "assigned", assigned_at, deadline_at, completed_at, …}` with progress 0.

### 3.3 Quest progress loop in `complete_lesson` (`learner.py`, end of the method)

After XP/streak/badge work, for each `assignment_id` in `sorted(assigned_quest_ids)` whose instance `status == "assigned"`:
skip if `occurred < assigned_at`; if `occurred > deadline_at` emit `QuestExpired` and skip; else resolve
`target = quest_target_for(q_id, instance["definition_version"])` (KeyError → `continue`, i.e. silently skipped), emit `QuestProgressed` with
`progress + 1`, and emit `QuestCompleted` (id `quest_completion:<assignment_id>`) when `new_step >= target`.
**There is no completion-kind check**: every accepted completion (any `completion_kind`, including None) advances every active quest.
`completion_kind` is validated against `COMPLETION_KINDS = {lesson, review, remediation}` and is used only for XP-decay eligibility (`DECAYING_KINDS = {lesson}`).
Because a missing target is silently `continue`d, a quest with an unknown version would be assigned-but-never-progressing if assignment validation were bypassed.

### 3.4 Is `assignment_id` (lesson_completed payload) used anywhere?

Yes — in `src/ingestion/service.py` only (the learner's `complete_lesson` does not receive it). `_ingest` reads `payload.assignment_id` (lines ~245-249) and,
via `_quest_already_complete` (lines 176-180), checks whether `quest_completion:<assignment_id>` is already in `learner.completed_quest_ids` before and after
accepting; if the quest was already complete before this lesson, an otherwise-accepted completion is reported `accepted` / `quest_already_completed` with a
duplicate evidence row (lines ~475-485). It is **not** used to choose which quest to progress (progress still goes to all active quests) and the JSON
schema for `lesson_completed` lists it as an optional payload field. Tests: `tests/ingestion/test_completed_quest_lesson.py`, `test_ingestion_service.py`.
I did not change any of this.

## 4. Tests added (`tests/interventions/test_remediation_quest.py`)

Definition lookup:
- `test_rule_set_quest_definition_comes_from_the_rule_set` — `("lesson_starter_v1","v1")`: target 3, kinds None.
- `test_remediation_quest_definition` — `("remediation_v1","remediation_quests_v1")`: target 1, `frozenset({"remediation"})`.
- `test_quest_definition_is_frozen` (extra).
- `test_unknown_quest_id_raises_key_error`, `test_unknown_definition_version_raises_key_error`.

Learner:
- `test_assign_remediation_quest_succeeds`.
- `test_non_remediation_completion_does_not_progress_the_quest[lesson|None|review]` — no QuestProgressed/QuestCompleted, status "assigned", progress 0.
- `test_non_remediation_completions_still_earn_xp` (extra) — filter affects quest progress only.
- `test_one_remediation_completion_completes_the_quest` — QuestCompleted, status "completed", progress 1.
- `test_remediation_completion_after_ignored_lessons_still_completes` (extra).
- Regression `test_lesson_starter_still_progresses_on_every_completion_kind[None|lesson|review|remediation]` — 3 completions → 3 QuestProgressed + QuestCompleted.

Tests drive `Learner.complete_lesson` directly (no DB), like other state-machine tests, rather than the ingestion path.

## 5. Command and output

```
$ python -m pytest tests/interventions/test_remediation_quest.py -v
12 failed, 4 passed in 0.17s
FAILED  test_rule_set_quest_definition_comes_from_the_rule_set        ModuleNotFoundError: No module named 'src.state_machine.quest_definitions'
FAILED  test_remediation_quest_definition                             ModuleNotFoundError (same)
FAILED  test_quest_definition_is_frozen                               ModuleNotFoundError (same)
FAILED  test_unknown_quest_id_raises_key_error                        ModuleNotFoundError (same)
FAILED  test_unknown_definition_version_raises_key_error              ModuleNotFoundError (same)
FAILED  test_assign_remediation_quest_succeeds                        KeyError: 'Unknown rule version: remediation_quests_v1' (from assign_quest)
FAILED  test_non_remediation_completion_does_not_progress_the_quest[lesson|None|review]   same KeyError, raised in the _remediation_learner() fixture helper
FAILED  test_non_remediation_completions_still_earn_xp                same KeyError
FAILED  test_one_remediation_completion_completes_the_quest           same KeyError
FAILED  test_remediation_completion_after_ignored_lessons_still_completes   same KeyError
PASSED  test_lesson_starter_still_progresses_on_every_completion_kind[None|lesson|review|remediation]   (regression, as intended)
```
(Per-test lines condensed from the `-v` output; counts and error texts are exact. After the last rename I re-ran with `-q`: `12 failed, 4 passed`.)

**Why they fail:** 5 import the missing `quest_definitions` module; 7 fail at `assign_quest` because `remediation_quests_v1` is not a known rule version. Both are
missing features. The 4 regression tests pass today by design: they pin behaviour that must not change.

## 6. Assumptions / unsure

- The 7 remediation Learner tests currently die in setup (`assign_quest`), so they have not yet exercised the progress loop. Once assignment works they could
  still fail for other reasons, and the "ignored completions don't progress" tests only prove something once assignment works.
- I assumed the kind filter lives in `complete_lesson`'s progress loop and that a filtered completion is **skipped silently** (no event), not progressed-by-zero.
  Tests assert only the absence of `QuestProgressed`/`QuestCompleted`.
- `completion_kind=None` is treated as not-remediation for a quest that requires `remediation` (per brief: "or absent"). For `accepted_completion_kinds=None` quests
  any kind including None progresses.
- A filtered-out completion still earns XP (extra test); I assumed this since the brief only talks about quest progress.
- The assignment_id used (`remediation:att-1`) is my choice; the brief does not say how a remediation quest's `assignment_id` is derived (Step 1/2 decisions use `remediation:<attempt_id>`, which would collide in meaning with the decision_id but live in a different namespace).
- Deadline passed explicitly to `assign_quest` in the helper; `test_assign_remediation_quest_succeeds` relies on the 7-day default instead.
- Quest completions in the tests are 1 hour apart and after `assigned_at`, so the assigned_at/deadline guards are not the reason for any outcome.
- The ingestion path (`ingest_lesson_completed` with `completion_kind` and `assignment_id`) is not covered here; unsure whether a later step should test it end to end.

---

# Prompt 2 — implementation

## 7. Changes made

- Created `src/state_machine/quest_definitions.py` (source in §10).
- `src/state_machine/learner.py`: import of `quest_definition_for` (the `quest_target_for` import was removed, as nothing else in the file used it); `assign_quest` and the `complete_lesson` quest progress loop changed.
- Not touched: `rulesets.json`, `rulesets_decay.json`, rule-set digests, `rules.py`, `src/ingestion`, tests.

## 8. Before / after of changed functions

`Learner.assign_quest` (validation line only):
- before: `quest_target_for(quest_id, definition_version)`  (KeyError for anything but a rule-set quest/version)
- after:  `quest_definition_for(quest_id, definition_version)`  (registry first, then rule-set fallback; same KeyErrors for unknown quest/version)

`Learner.complete_lesson`, quest progress loop (the part after the `occurred < assigned_at` and deadline-expiry guards):
- before:
  ```python
  try:
      target = quest_target_for(q_id, str(instance["definition_version"]))
  except KeyError:
      continue
  ```
- after:
  ```python
  try:
      definition = quest_definition_for(q_id, str(instance["definition_version"]))
  except KeyError:
      continue
  accepted_kinds = definition.accepted_completion_kinds
  if accepted_kinds is not None and (completion_kind or "lesson") not in accepted_kinds:
      continue
  target = definition.target
  ```
- behaviour: quests whose accepted kinds are None behave exactly as before; a quest with an accepted-kind set is skipped (no event) for other kinds, an absent kind counting as "lesson".

New: `QuestDefinition`, `_DEFINITIONS` (MappingProxyType, one entry), `quest_definition_for`.

## 9. Commands and output

```
$ python -m pytest tests/interventions -v
68 passed in 0.54s      (19 ingestion + 18 policy + 15 service + 16 quest; all PASSED)

$ python -m pytest tests/state_machine tests/ingestion -q
241 passed in 3.38s
```
Before this prompt: `test_remediation_quest.py` had 12 failed, 4 passed (§5). The full suite and the integration marker were not re-run in this prompt.

## 10. Source of quest_definitions.py

```python
"""Quest definitions: the target and accepted completion kinds of a quest version."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .rules import quest_target_for


@dataclass(frozen=True)
class QuestDefinition:
    quest_id: str
    definition_version: str
    target: int
    # None means any completion kind progresses the quest.
    accepted_completion_kinds: frozenset[str] | None


_DEFINITIONS: Mapping[tuple[str, str], QuestDefinition] = MappingProxyType(
    {
        ("remediation_v1", "remediation_quests_v1"): QuestDefinition(
            quest_id="remediation_v1",
            definition_version="remediation_quests_v1",
            target=1,
            accepted_completion_kinds=frozenset({"remediation"}),
        )
    }
)


def quest_definition_for(quest_id: str, definition_version: str) -> QuestDefinition:
    """Returns a quest definition, from this registry first, else from the rule set.

    Raises KeyError for an unknown quest or version.
    """
    registered = _DEFINITIONS.get((quest_id, definition_version))
    if registered is not None:
        return registered
    return QuestDefinition(
        quest_id=quest_id,
        definition_version=definition_version,
        target=quest_target_for(quest_id, definition_version),
        accepted_completion_kinds=None,
    )
```

## 11. Assumptions / unsure

- The kind filter sits **after** the `occurred < assigned_at` and deadline-expiry guards, so a completion of any kind can still expire an overdue remediation
  quest (as any completion does for every quest today), but only an accepted kind progresses it. Putting the filter earlier would change when expiry fires; untested either way.
- `quest_definition_for` builds a new `QuestDefinition` per call for rule-set quests (cheap; no caching).
- A quest assigned with a definition version that later disappears is still silently skipped by the loop (pre-existing `except KeyError: continue`).
- Old streams: existing `QuestAssigned` events carry rule-set versions, which resolve through the fallback, so replay is unchanged (covered by the 241 state_machine/ingestion tests passing, not by a dedicated replay test).
- Still nothing assigns the quest from a remediation decision (later step). `Learner._assign_quest` event shape is unchanged.
- Absent kind counts as "lesson" only for the accepted-kinds check; it does not change XP-decay eligibility, which still treats None as eligible.

---

# Prompt 3 — failing tests for connecting an assign decision to a quest

## 12. Changes made

- Created `tests/interventions/test_remediation_end_to_end.py` (8 tests). It reuses helpers from `test_remediation_service.py` and the Step 1 ingestion tests
  (`_open`, `_new_learner`, `_ingest`, `_run`, `NOW`) and `tests.ingestion.support.ingest_lesson_completed` (late window off). No `src/` change.

## 13. Tests added

1. `test_config_has_quest_definition_version` — `RemediationConfig().quest_definition_version == "remediation_quests_v1"`.
2. `test_assign_decision_creates_the_quest_in_one_save` — instance `remediation_quest:att-1`: quest_id `remediation_v1`, definition_version, status `assigned`,
   `assigned_at == decided_at`, `deadline_at == decided_at + 7 days`; exactly one `RemediationDecided` and one `QuestAssigned`.
   **How "one save" is checked:** the event store does not expose which events were written by which save, so the test wraps `application.save`, counts calls during
   `run_remediation` (must be 1), and asserts the `QuestAssigned` event's `originator_version` is the `RemediationDecided` version + 1 (consecutive in the same aggregate).
   This proves one save call and adjacency, not atomicity at the storage level (SQLite writes one save's events in one transaction, per the library, which I did not verify here).
3. `test_passed_attempt_creates_no_quest` — no instances, no `QuestAssigned`, one `RemediationDecided`.
4. `test_window_blocked_decision_creates_no_second_quest` (extra) — a `no_action` from the 14-day window assigns no quest.
5. `test_rerun_30_days_later_assigns_nothing_new` — still one `QuestAssigned`.
6. `test_full_path_remediation_completion_completes_the_quest` — failed checkpoint → `run_remediation` → ingest `lesson_completed` with `completion_kind: "remediation"`
   at NOW+2 days (before the deadline) → status `completed`, one `QuestCompleted`.
7. `test_full_path_lesson_completion_leaves_the_quest_assigned` — same with `"lesson"` → `assigned`, progress 0, no `QuestCompleted`.
8. `test_replay_in_a_fresh_application_keeps_quest_and_decisions` — three decisions (assign, window no_action, passed no_action), a completing remediation lesson, then a fresh
   `LearnerApplication` on the same DB: `quest_instances` and `remediation_decisions` equal.

## 14. Command and output

```
$ python -m pytest tests/interventions/test_remediation_end_to_end.py -v
7 failed, 1 passed in 0.31s
FAILED test_config_has_quest_definition_version                          AttributeError: 'RemediationConfig' object has no attribute 'quest_definition_version'
FAILED test_assign_decision_creates_the_quest_in_one_save                KeyError: 'remediation_quest:att-1'   (instance missing)
FAILED test_window_blocked_decision_creates_no_second_quest              assert set() == {'remediation_quest:att-1'}  (the first, assigned decision created no quest)
FAILED test_rerun_30_days_later_assigns_nothing_new                      assert 0 == 1  (no QuestAssigned at all)
FAILED test_full_path_remediation_completion_completes_the_quest         KeyError: 'remediation_quest:att-1'
FAILED test_full_path_lesson_completion_leaves_the_quest_assigned        KeyError: 'remediation_quest:att-1'
FAILED test_replay_in_a_fresh_application_keeps_quest_and_decisions      assert set() == {'remediation_quest:att-1'}
PASSED test_passed_attempt_creates_no_quest                              (guard: already true today)
```
(Failure lines condensed from the `-v` output; error texts are exact.) All 7 failures are the missing feature: the quest is never assigned and the config field does not exist.
In the two full-path tests the lesson ingestion itself succeeded (the `accepted` assertion passed) before the missing-instance `KeyError`, so ingestion with `completion_kind` works today.
The passed-attempt test passes now by design; it guards against over-assigning.

## 15. Assumptions / unsure

- Wrapping `application.save` on the instance relies on `run_remediation` calling `application.save(...)` as an attribute lookup at call time (true in the current service). If the implementation saved via another path, test 2 would need adjusting.
- The "same save" check is indirect (see §13.2). A stricter check would be to fail the second write and assert neither event persisted; I did not do that.
- Full-path tests rely on `lesson_completed` for `completion_kind` "remediation" earning XP/streak as normal; XP/streak are not asserted.
- The lesson envelope's `completion_id` is `c-<kind>`; the quest has no `assignment_id` link in the payload (the optional payload `assignment_id` is deliberately unused, so progress comes from the kind filter alone).
- NOW is 2026-09-10; the lesson occurs NOW+2d, inside the 7-day window, so the deadline guard is not exercised here (no expiry-after-deadline test requested).
- Test 8's `quest_instances` equality compares datetimes parsed from ISO strings on both sides; fine under current `_as_datetime`.

---

# Prompt 4 — implementation of decision → quest

## 16. Changes made

- `src/interventions/config.py`: added `quest_definition_version: str = "remediation_quests_v1"` to `RemediationConfig`.
- `src/interventions/service.py`: `run_remediation` now assigns the quest for an `assign` decision before the single save.
- `policy.py` unchanged. `src/ingestion`, `src/state_machine` not touched in this prompt.
- Tests: appended `test_failing_quest_assignment_saves_nothing` to `test_remediation_end_to_end.py`; **edited one earlier test** (see §18).

## 17. Before / after of `run_remediation`

before (tail of the function):
```python
    learner.record_remediation_decision(decision)
    application.save(learner)
    return decision
```
after:
```python
    learner.record_remediation_decision(decision)
    if decision.action == "assign":
        learner.assign_quest(
            config.quest_id,
            assignment_id=f"remediation_quest:{attempt_id}",
            assigned_at=decision.decided_at,
            deadline_at=decision.deadline_at,
            definition_version=config.quest_definition_version,
        )
    application.save(learner)
    return decision
```
Everything before that (load, return stored decision without saving, `decide_remediation`) is unchanged. The decision and quest are recorded on the same learner
object and written by one `application.save`. `RemediationConfig`: before — four fields; after — five (new field after `quest_id`, default as above).

Failure semantics: `assign_quest` validates through `quest_definition_for` before emitting anything, so an unknown quest raises `KeyError` with the decision already
recorded **on the in-memory aggregate only**; the exception propagates before `application.save`, so nothing is persisted. The new test uses
`RemediationConfig(quest_id="no_such_quest")`: `KeyError` is raised, and the reloaded learner has no `remediation_decisions`, no `quest_instances`, and the event count is unchanged.

## 18. Test change that needs your attention

`test_run_remediation_leaves_xp_streak_quests_and_attempts_unchanged` (Step 2, `test_remediation_service.py`) failed after the change: it asserted quest state is unchanged
by `run_remediation`, which this feature deliberately contradicts for an `assign`. I made a minimal edit rather than stopping: renamed it
`..._leaves_xp_streak_and_attempts_unchanged`, removed the four quest fields from its snapshot, and added an assertion that the only quest is
`remediation_quest:att-1` and `completed_quest_ids` is empty. XP, streak and checkpoint-attempt assertions are unchanged. If you would rather I had stopped first, that is the one test to revert/review.

## 19. Commands and output

```
$ python -m pytest tests/interventions -v      (first run, before editing the test above)
1 failed, 76 passed   -> test_run_remediation_leaves_xp_streak_quests_and_attempts_unchanged

$ python -m pytest tests/interventions -q      (after the test edit)
77 passed in 0.62s     (19 ingestion + 18 policy + 15 service + 16 quest + 9 end-to-end)

$ python -m pytest tests/state_machine tests/ingestion -q
241 passed in 2.95s
```
The previous run's 7 end-to-end failures (§14) now pass. The full suite and integration marker were not re-run in this prompt.

## 20. Source of service.py

```python
"""Runs the remediation policy against a stored learner and records the decision."""

from datetime import datetime
from typing import Callable
from uuid import UUID

from src.interventions.config import RemediationConfig
from src.interventions.policy import RemediationDecision, decide_remediation
from src.state_machine.learner import LearnerApplication


def _decision_from_record(record: dict[str, object]) -> RemediationDecision:
    deadline = record["deadline_at"]
    return RemediationDecision(
        decision_id=str(record["decision_id"]),
        attempt_id=str(record["attempt_id"]),
        assessment_id=str(record["assessment_id"]),
        action=str(record["action"]),
        reason=None if record["reason"] is None else str(record["reason"]),
        decided_at=datetime.fromisoformat(str(record["decided_at"])),
        deadline_at=None if deadline is None else datetime.fromisoformat(str(deadline)),
        quest_id=None if record["quest_id"] is None else str(record["quest_id"]),
        policy_version=str(record["policy_version"]),
    )


def run_remediation(
    application: LearnerApplication,
    learner_id: str,
    attempt_id: str,
    *,
    clock: Callable[[], datetime],
    config: RemediationConfig = RemediationConfig(),
) -> RemediationDecision:
    """Returns the stored decision for the attempt, or decides, records and saves one.

    An ``assign`` decision also assigns its quest, in the same save.
    """
    learner = application.repository.get(UUID(learner_id))
    stored = learner.remediation_decisions.get(f"remediation:{attempt_id}")
    if stored is not None:
        return _decision_from_record(stored)
    prior = [_decision_from_record(r) for r in learner.remediation_decisions.values()]
    decision = decide_remediation(
        learner.checkpoint_attempts,
        learner.first_checkpoint_attempt_ids,
        prior,
        attempt_id,
        clock(),
        config,
    )
    learner.record_remediation_decision(decision)
    if decision.action == "assign":
        # Raises before the save for an unknown quest, so no decision is stored without its quest.
        learner.assign_quest(
            config.quest_id,
            assignment_id=f"remediation_quest:{attempt_id}",
            assigned_at=decision.decided_at,
            deadline_at=decision.deadline_at,
            definition_version=config.quest_definition_version,
        )
    application.save(learner)
    return decision
```

## 21. Assumptions / unsure

- Ordering: the decision is recorded before `assign_quest`, so in the stream `RemediationDecided` precedes `QuestAssigned` (test 2 asserts consecutive versions in that order).
- The stored-decision early return does not check that the quest exists; if a decision was stored before this feature (no quest), re-running will not assign one retroactively.
  Old assign decisions from Step 2 therefore stay questless. Whether to backfill is an open question.
- The failure test covers an unknown `quest_id`; an unknown `quest_definition_version` in config would fail the same way but is not tested.
- A `ValueError` from `assign_quest` (e.g. missing `assigned_at`) cannot occur here since `decided_at` is always set.
- Atomicity is as strong as one `application.save`; I did not verify storage-level behaviour beyond the observed no-write on failure.

---

# Prompt 5 — edge cases and finish Step 3

## 22. Changes made

- Tests only (no source change, no fix needed): 4 added to `test_remediation_quest.py`, 1 to `test_remediation_end_to_end.py`.
- `docs/event-contracts.md`: new subsection "Remediation quest (`remediation_v1`), M4" under the M4 remediation section, plus a changelog line dated 2026-11-02.
- Traceability matrix, README, AT-17: not touched.

## 23. Edge-case tests and first-run result (run before any fix)

| # | Test | Passed first run? |
|---|------|------|
| 1 | `test_remediation_completion_before_assigned_at_does_not_progress` (status assigned, progress 0, no quest events, XP still earned) | yes |
| 2 | `test_remediation_completion_after_deadline_does_not_complete_and_expires_lazily` | yes |
| 3 | `test_remediation_completion_without_an_active_quest_still_earns_xp` | yes |
| 4 | `test_remediation_completion_earns_full_xp_with_no_decay` (rule set v2, four prior `lesson` completions of the same lesson, then `remediation` → 100 XP, `repeat_index` 0) | yes |
| 5 | `test_other_learners_remediation_completion_does_not_touch_this_learners_quest` (ingestion path, shared DB) | yes |

**Current lazy expiry, as observed (test 2):** a remediation completion at deadline + 1 h emits `QuestExpired` from inside `complete_lesson`
(status becomes `expired`), emits no `QuestProgressed`/`QuestCompleted`, and the completion still earns XP. The quest expires only when some completion
is processed after the deadline; nothing expires it otherwise. The test pins this so Step 4's sweep can change it deliberately.

**Fixes:** none. After the first run I removed two unused `names = ...` assignments in tests 1 and 2 (cosmetic; re-run still green).

## 24. Commands and exact output

```
$ python -m pytest tests/interventions -q
82 passed in 0.68s

$ python -m pytest -q
........................................................................ [ 97%]
......................                                                   [100%]
740 passed, 2 skipped, 1 deselected in 23.70s

$ python -m pytest -m integration -q
.                                                                        [100%]
1 passed, 742 deselected in 4.75s
```
(Full-suite output shows its last two progress rows; earlier rows were all dots and the two skips.) Nothing failed, so there is no regression to report.
Before Step 3 the full suite was 710 passed, 2 skipped, 1 deselected (Step 2 close). The +30 tests are exactly the Step 3 additions (16 quest + 9 end-to-end + 5 edge cases).

## 25. Summary of Step 3

- `quest_definitions.py`: read-only registry holding `remediation_v1` / `remediation_quests_v1` (target 1, accepted kind `remediation`), falling back to rule-set quests (accepted kinds None).
- `Learner.assign_quest` validates via `quest_definition_for`; the progress loop skips a quest whose accepted kinds exclude the completion kind (absent = `lesson`).
- `RemediationConfig.quest_definition_version`; `run_remediation` assigns `remediation_quest:<attempt_id>` in the same save as an `assign` decision; failure saves nothing.
- Rule sets, digests and ingestion untouched.

## 26. Final test counts

- `tests/interventions`: 82 passed (19 ingestion, 18 policy, 15 service, 20 quest, 10 end-to-end).
- `tests/state_machine` + `tests/ingestion`: 241 passed at the end of prompt 4; not re-run separately in this prompt (covered by the full suite).
- Full suite 740 passed, 2 skipped, 1 deselected; integration 1 passed.

## 27. Open questions / unsure

- Step 2 `assign` decisions stored before Step 3 have no quest and are never backfilled (the stored-decision early return does not assign).
- The documented expiry sentence describes lazy expiry as current behaviour; Step 4 will change it, and the contract text will need updating then.
- An earlier Step 2 test was edited in prompt 4 (quest assertions narrowed); flagged there, repeated here for visibility.
- Test 4 relies on the `v2` decay rule set being registered via `monkeypatch` of `_REGISTRY` (same technique as `test_xp_decay.py`); the 100 XP figure is the v2 base amount.
- The documented "absent kind counts as `lesson`" applies to the accepted-kinds check only; XP-decay eligibility still treats an absent kind as eligible, as before.
- Quests with an unknown definition version are still silently skipped in the progress loop (pre-existing).
- Duplicate `assignment_id` namespaces: `remediation:<attempt_id>` (decision) vs `remediation_quest:<attempt_id>` (quest) are distinct by design; confirm that is fine for AT-17 wording later.
- No test asserts the ingestion `assignment_id` payload field with the remediation quest; the quest progresses by kind only.
