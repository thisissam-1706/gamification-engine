# Week 6 / M4 / Step 2 — pure remediation policy

Status: **Step 2 complete (prompt 6): full suite green, event contract documented.** (Prompt 4: Learner storage + runner implemented.) (Prompt 2: policy implemented, 37 tests pass.) (Prompt 1 was failing tests only; its sections below are kept.)
Git rule in force: no state-changing git commands (read-only only; none were run).

## 1. Changes made

- Created this file.
- Created `tests/interventions/test_remediation_policy.py` (17 tests).
- Nothing under `src/` changed. `src/interventions/` does not exist yet.

## 2. Before / after behaviour

No behaviour change. The tests describe the policy to be built in `src/interventions/`.

## 3. Learner record shape (read from `src/state_machine/learner.py:322-334`)

`Learner.checkpoint_attempts[attempt_id]` = `{attempt_id, assessment_id, assessment_version, passed, timezone, occurred_at}`
(`occurred_at` is `str | datetime`). `Learner.first_checkpoint_attempt_ids[assessment_id] = attempt_id`.
The policy needs only `assessment_id` and `passed` from the record. The tests build records with all six keys.

## 4. Tests added (`tests/interventions/test_remediation_policy.py`)

Requested cases:
1. `test_failed_first_attempt_assigns` — assign, deadline = now + 7d, id `remediation:att-1`, quest/policy fields.
2. `test_passed_first_attempt_is_no_action` — `passed`.
3. `test_failed_second_attempt_is_not_first` — `not_first_attempt`.
4. `test_prior_assign_three_days_ago_blocks` — `remediated_within_window`.
5. `test_prior_assign_exactly_14_days_ago_does_not_block` — assign.
6. `test_prior_assign_just_inside_window_blocks` — 14d − 1s → `remediated_within_window`.
7. `test_prior_no_action_does_not_block` — assign.
8. `test_unknown_attempt_id_raises_key_error`; `test_naive_now_raises_value_error`.
9. `test_deterministic`.

Extra cases I added (beyond the brief; delete if unwanted):
- `test_config_defaults`, `test_config_is_frozen`, `test_decision_is_frozen`
- `test_not_first_checked_before_passed`, `test_passed_checked_before_window` (rule ordering c→d→e)
- `test_unknown_attempt_checked_before_naive_now` (rule ordering a→b)
- `test_any_blocking_prior_among_several_blocks` (also passes a one-shot iterator as `prior_decisions`)
- `test_inputs_not_mutated`

## 5. Commands and full output

```
$ python -m pytest tests/interventions/test_remediation_policy.py -v
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0 -- /home/sampadram/miniconda3/bin/python
cachedir: .pytest_cache
rootdir: /home/sampadram/Sampadram/IITH/SEM 7/DATGC/project/gamification-engine
configfile: pyproject.toml
plugins: anyio-4.12.1, asyncio-1.4.1
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 0 items / 1 error

==================================== ERRORS ====================================
_______ ERROR collecting tests/interventions/test_remediation_policy.py ________
ImportError while importing test module '.../tests/interventions/test_remediation_policy.py'.
Traceback:
/home/sampadram/miniconda3/lib/python3.14/importlib/__init__.py:88: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
tests/interventions/test_remediation_policy.py:12: in <module>
    from src.interventions.config import RemediationConfig
E   ModuleNotFoundError: No module named 'src.interventions'
=========================== short test summary info ============================
ERROR tests/interventions/test_remediation_policy.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
=============================== 1 error in 0.08s ===============================
```

(Traceback path abbreviated in this file only; the console showed the absolute path.)

**Why it fails:** `src.interventions` does not exist — the feature is missing, not a typo. Other `src.*` packages
import fine (`tests/interventions/test_checkpoint_attempt_ingestion.py` uses the same `src.` import style).
Because the import is module-level, all 17 tests fail together at collection; no per-test result exists until
the package is created.

## 6. Assumptions / unsure

- `src/interventions/__init__.py` will be needed (other packages have one); not created, as it is implementation.
- `decided_at` for the new decision = `now` (the brief gives no other source). `deadline_at = now + quest_deadline`.
- `RemediationDecision.assessment_id` comes from the attempt record, not from `first_attempt_ids`.
- Rule (e) compares `decided_at > now - frequency_window` strictly, per the brief; prior decisions are not filtered
  by `assessment_id` (the window is per learner, as the brief says). My `prior()` helper uses `assessment_id="a0"`
  to show that.
- Prior `assign` decisions with `decided_at` in the future relative to `now` would block (`> now - window`); not tested.
- Frozen-dataclass tests use `pytest.raises(Exception)` (FrozenInstanceError subclass of AttributeError); could be tightened.
- If `first_attempt_ids` lacks the attempt's `assessment_id`, behaviour is unspecified (treated as not first by the
  obvious implementation); not tested.

---

# Prompt 2 — implementation

## 7. Changes made

- Created `src/interventions/__init__.py` (empty), `src/interventions/config.py`, `src/interventions/policy.py`.
- No change to tests, `src/state_machine` or `src/ingestion`. `policy.py` imports only stdlib and `config`; no clock, I/O or event store.

## 8. Before / after

Before: `src.interventions` missing, 17 tests failed at collection. After: `decide_remediation` implements rules a→f in order.
Rule order in code: `attempts[attempt_id]` (KeyError) → naive `now` (ValueError) → `first_attempt_ids.get(assessment_id) != attempt_id`
→ `passed` → any prior `assign` with `decided_at > now - frequency_window` → assign.
No test contradicted the rules.

## 9. Command and output

```
$ python -m pytest tests/interventions -v
collected 37 items
tests/interventions/test_checkpoint_attempt_ingestion.py: 19 PASSED
tests/interventions/test_remediation_policy.py: 18 PASSED
============================== 37 passed in 0.38s ==============================
```
(Per-test lines condensed here; every one showed PASSED. The remediation file has 18 tests, not 17 as I wrote in §1 —
the 17 was a miscount; the step-1 ingestion file has 19.)

## 10. Source

`src/interventions/config.py`:
```python
"""Configuration for the remediation policy."""

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class RemediationConfig:
    frequency_window: timedelta = timedelta(days=14)
    quest_deadline: timedelta = timedelta(days=7)
    quest_id: str = "remediation_v1"
    policy_version: str = "remediation_policy_v1"
```

`src/interventions/policy.py`:
```python
"""Pure remediation policy: decides whether a checkpoint attempt earns a remediation quest."""

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping

from src.interventions.config import RemediationConfig


@dataclass(frozen=True)
class RemediationDecision:
    decision_id: str
    attempt_id: str
    assessment_id: str
    action: str  # "assign" or "no_action"
    reason: str | None
    decided_at: datetime
    deadline_at: datetime | None
    quest_id: str | None
    policy_version: str


def decide_remediation(
    attempts: Mapping[str, Mapping[str, object]],
    first_attempt_ids: Mapping[str, str],
    prior_decisions: Iterable[RemediationDecision],
    attempt_id: str,
    now: datetime,
    config: RemediationConfig,
) -> RemediationDecision:
    """Decides remediation for one attempt; rules are checked in a fixed order.

    Raises KeyError for an unknown attempt and ValueError for a naive ``now``.
    """
    attempt = attempts[attempt_id]
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    assessment_id = str(attempt["assessment_id"])

    def no_action(reason: str) -> RemediationDecision:
        return RemediationDecision(
            decision_id=f"remediation:{attempt_id}",
            attempt_id=attempt_id,
            assessment_id=assessment_id,
            action="no_action",
            reason=reason,
            decided_at=now,
            deadline_at=None,
            quest_id=None,
            policy_version=config.policy_version,
        )

    if first_attempt_ids.get(assessment_id) != attempt_id:
        return no_action("not_first_attempt")
    if attempt["passed"]:
        return no_action("passed")
    window_start = now - config.frequency_window
    if any(d.action == "assign" and d.decided_at > window_start for d in prior_decisions):
        return no_action("remediated_within_window")
    return RemediationDecision(
        decision_id=f"remediation:{attempt_id}",
        attempt_id=attempt_id,
        assessment_id=assessment_id,
        action="assign",
        reason=None,
        decided_at=now,
        deadline_at=now + config.quest_deadline,
        quest_id=config.quest_id,
        policy_version=config.policy_version,
    )
```

## 11. Assumptions / unsure

- Missing `assessment_id` in `first_attempt_ids` → treated as `not_first_attempt` (unspecified in brief; untested).
- `attempt["passed"]` is read by truthiness; a record missing `passed` raises KeyError.
- `prior_decisions` is consumed once (works with iterators).
- Naive check also treats a tzinfo with `utcoffset() is None` as naive.

---

# Prompt 3 — failing tests for storage and runner

## 12. Changes made

- Created `tests/interventions/test_remediation_service.py` (9 tests). No `src/` change.

## 13. Tests added

Learner level (no DB, `Learner()` directly):
- `test_record_stores_decision_with_iso_datetimes_and_emits_one_event` — all fields stored, datetimes as ISO strings, one `RemediationDecided` event.
- `test_record_no_action_stores_none_fields`
- `test_record_is_idempotent_on_decision_id` — second call returns False, emits nothing, first record kept.
- `test_record_leaves_xp_streak_quests_and_attempts_unchanged`

Runner level (real `LearnerApplication` on tmp_path SQLite; attempts via `ingest_checkpoint_attempted` using the Step 1 helpers):
- `test_failed_first_attempt_is_assigned_and_stored_once`
- `test_rerun_returns_stored_decision_even_when_clock_has_moved_on` (+30 days; equal decision, one event)
- `test_second_failed_first_attempt_within_window_is_no_action` (different assessments, +3 days)
- `test_passed_attempt_stores_an_explicit_no_action`
- `test_run_remediation_leaves_xp_streak_quests_and_attempts_unchanged`

## 14. Command and result

```
$ python -m pytest tests/interventions/test_remediation_service.py -v
9 FAILED
  4 x AttributeError: 'Learner' object has no attribute 'record_remediation_decision'   (Learner-level tests)
  5 x ModuleNotFoundError: No module named 'src.interventions.service'                  (runner tests)
```
Every failure is a missing feature. The `service` import and `Learner` method are only touched inside each test, so each
test fails individually rather than at collection. Note the 5 runner tests fail at the `src.interventions.service` import
first, so they have not yet exercised the `Learner` method or DB path; they could still fail for other reasons once
those exist (e.g. my assumptions below).

## 15. Assumptions / unsure

- Ingestion uses the Step 1 helpers (`UNBOUNDED_CONFIG`, no 72 h window) since the 2026-09 timestamps are far from the real clock.
- Attempt `occurred_at` is the same for all test attempts (`OCCURRED`); ordering across assessments is irrelevant to the policy, and `now` comes from the injected clock.
- Stored record keys: `decision_id, attempt_id, assessment_id, action, reason, decided_at, deadline_at, quest_id, policy_version`.
- `Learner.collect_events()` is used to inspect pending events on an unsaved aggregate (eventsourcing API); first call drops the creation event.
- Quest state is checked via `assigned_quest_ids`, `completed_quest_ids`, `quest_progress`, `quest_instances`. The policy does NOT assign the quest in this step (decision only); tests assume that.
- `run_remediation` takes `learner_id` as the string form, like the ingestion helpers.

---

# Prompt 4 — implementation of storage and runner

## 16. Changes made

- `src/state_machine/learner.py` (additive only; the file already carried uncommitted Step 1 changes): `TYPE_CHECKING` import of
  `RemediationDecision`; new `remediation_decisions` in `__init__`; new `record_remediation_decision` and `@event("RemediationDecided") _record_remediation_decision`.
- `src/interventions/service.py` created.
- `src/ingestion` untouched.

## 17. Design choice: decision object vs plain fields

Public `record_remediation_decision(decision)` takes the `RemediationDecision` object (the tests pass one), imported **only under
`TYPE_CHECKING`**, so there is no runtime import and no cycle (`service.py` imports `learner`; `learner.py` never imports `interventions` at runtime).
The `@event` method takes plain fields (strings / None), as the event payload must be serialisable: datetimes are converted to ISO strings in the public
method, mirroring how checkpoint attempts keep `occurred_at`. Policy stays free of any Learner import. (In fact `policy.py` imports only `config`, so a real
import would not cycle today; TYPE_CHECKING keeps the state machine independent of the interventions package.)

## 18. Before / after of changed functions

- `Learner.__init__`: before — no remediation state; after — adds `self.remediation_decisions: dict[str, dict[str, object]] = {}`. Old event streams replay unchanged
  because the attribute is initialised in `__init__`, not by an event.
- `Learner.record_remediation_decision` (new): returns False without emitting if `decision_id` is stored; else calls the event method with ISO-string datetimes and returns True.
- `Learner._record_remediation_decision` (new, `@event("RemediationDecided")`): pure fold writing the record dict (keys: decision_id, attempt_id, assessment_id, action, reason, decided_at, deadline_at, quest_id, policy_version).
- `run_remediation` (new): load learner → return stored decision (rebuilt via `datetime.fromisoformat`) if `remediation:<attempt_id>` exists, with no save → else `decide_remediation(..., now=clock())` with priors rebuilt from stored records → record → `application.save` → return.
No other function changed.

## 19. Commands and output

```
$ python -m pytest tests/interventions -v
... 46 passed in 0.41s        (19 ingestion + 18 policy + 9 service)

$ python -m pytest tests/state_machine tests/ingestion -q
241 passed in 3.00s
```
Before this prompt: 9 of the service tests failed (missing feature, §14).

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
    """Returns the stored decision for the attempt, or decides, records and saves one."""
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
    application.save(learner)
    return decision
```

## 21. Assumptions / unsure

- Prior decisions passed to the policy are all stored decisions for the learner (not filtered by assessment), matching the brief.
- A stored decision is returned regardless of the current `config`; the stored `policy_version` is whatever was recorded.
- `run_remediation` for an unknown `attempt_id` raises `KeyError` from the policy before anything is saved (not tested at service level).
- Naive clock raises `ValueError` from the policy, also before saving (not tested at service level).
- Only `tests/state_machine` and `tests/ingestion` were run besides `tests/interventions`; the full suite was not run.

---

# Prompt 5 — edge-case tests

## 22. Changes made

- Appended 6 tests to `tests/interventions/test_remediation_service.py`. **No source change; no fix was needed.**

## 23. Tests and first-run result (all run before any fix)

| # | Test | Passed before any fix? |
|---|------|------|
| 1 | `test_replay_in_a_fresh_application_keeps_decisions_and_returns_stored` (3 decisions: assign, no_action window, no_action passed; fresh `LearnerApplication` on same DB: `remediation_decisions` equal, re-run 60 days later returns equal decisions, event count unchanged) | yes |
| 2 | `test_late_earlier_attempt_keeps_stored_decision_and_is_blocked_by_it` (A assigned; B earlier on same assessment becomes first; A's stored record unchanged; B → `no_action`/`remediated_within_window`; 2 events total) | yes |
| 3 | `test_unknown_attempt_raises_key_error_and_saves_nothing` | yes |
| 4 | `test_naive_clock_raises_value_error_and_saves_nothing` | yes |
| 5 | `test_window_is_measured_from_decided_at_not_attempt_occurred_at` (attempt occurred now−20d, assigned now−3d; new failed attempt on another assessment at now → blocked) | yes |
| 6 | `test_learners_are_independent` | yes |

## 24. Fixes

None.

## 25. Commands and output

```
$ python -m pytest tests/interventions/test_remediation_service.py -v
15 passed in 0.30s   (9 earlier tests + 6 new, all PASSED)

$ python -m pytest tests/interventions -q
52 passed
```

## 26. Notes / unsure

- Because all six passed immediately, they were not seen failing. I checked by reading that #5 would fail if the policy used the attempt's `occurred_at`
  (that attempt is 20 days old, so it would not block) and #2 would fail if a stored decision were recomputed; I did not mutate the code to prove it.
- #3/#4 verify "nothing saved" via event count and an empty `remediation_decisions`.
- #2: B is ingested with `occurred_at` one hour before A; only the unbounded ingest config is used, so the 72 h window itself is not exercised here.
- #2 relies on `first_checkpoint_attempt_ids` re-pointing to B (asserted in the test), which Step 1 tests already cover.

---

# Prompt 6 — finish Step 2

## 27. Changes made

- `docs/event-contracts.md`: new section "Remediation decision (`RemediationDecided`), M4" at the end, plus a changelog line dated 2026-11-02.
- Traceability matrix, README and AT-17 wording: not touched, as instructed.
- No source or test change.

## 28. Commands and exact output

```
$ python -m pytest -q
........................................................................ [ 10%]
...  (rows of dots omitted here; the only non-dot characters were two `s`, in the 30% row)
710 passed, 2 skipped, 1 deselected in 23.75s

$ python -m pytest -m integration -q
.                                                                        [100%]
1 passed, 712 deselected in 4.60s
```
Nothing failed, so there was nothing to report as a regression. I have no pre-Step-2 full-suite run to compare against, so "nothing that
passed before now fails" is inferred from zero failures, not from a before/after diff. The 2 skips and 1 deselected test (the integration one,
excluded by default config) were not investigated.

## 29. Summary of Step 2

- Pure policy `decide_remediation` + frozen `RemediationConfig` / `RemediationDecision` in `src/interventions/` (18 tests).
- `Learner.record_remediation_decision` + `RemediationDecided` event + `remediation_decisions` state, idempotent on `decision_id` (4 Learner-level tests).
- `run_remediation` runner returning the stored decision when present (5 service tests + 6 edge-case tests).
- Contract documented in `docs/event-contracts.md`.
- `src/ingestion` untouched in Step 2; `src/state_machine/learner.py` changed additively.

## 30. Final test counts

- `tests/interventions`: 52 passed (19 step-1 ingestion, 18 policy, 15 service).
- Full suite: 710 passed, 2 skipped, 1 deselected. Integration marker: 1 passed.

## 31. Open questions / unsure

- Quest assignment: a decision with `action = assign` records `quest_id` and `deadline_at` but nothing assigns a quest yet. Whether and where that happens is a later step.
- Window is learner-wide across assessments (per the brief); confirm that is intended.
- A stored decision is returned regardless of the `config` passed later; a policy-version change will not re-decide old attempts.
- Late earlier attempt (edge case 2) yields `remediated_within_window` for B because A's assign is in the window, which is by design but means the "first attempt" that actually gets remediated can be the later-arriving one's sibling.
- Unspecified/untested: `assessment_id` missing from `first_attempt_ids`; a prior `assign` dated in the future relative to `now`; records missing `passed`.
- Edge-case tests all passed on first run and were never seen failing (see §26).
- The changelog line sits beside the existing 2026-11-02 line at the top of the file; the doc has no separate changelog section.
- Whether event-contracts.md should state the `RemediationDecided` payload as a JSON schema (none exists for it) is undecided.
