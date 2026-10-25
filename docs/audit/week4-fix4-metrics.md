# Week 4 Fix 4: exp_007 primary-metric hardening

Status: **done. Not committed.** All six defects are fixed. The full suite
is **211 passed, 0 failed, 4 skipped**. `src/experiments/assignment.py`
and the hash behaviour are unchanged. The exp_007 example in
`docs/metrics-framework.md` was updated to match (see "Doc fix").

**Finding (Step 3, corrected in 3b):** given the maturity rule, ignoring
**attempts** after `as_of` can never change a report, so no test can catch
removing that filter. The **completion** filter does matter in one case (a
conflicting row dated after `as_of`). That case is pinned (Step 3b).

The sections below are a chronological log. Earlier sections keep the output
from that point in time. "Final verification" has the current state.

## Summary

| What | Where | Change |
|---|---|---|
| `_timestamp` (defect 5) | `src/experiments/metrics.py` | ISO string → aware UTC; `Z` = UTC, naive = UTC, offsets converted. `TypeError` for non-strings kept |
| `WINDOW`, `_cutoff` | `src/experiments/metrics.py` | `timedelta(days=14)` written once. `_cutoff` normalizes `as_of` (`None`, ISO string or `datetime`) to UTC |
| `MasteryReport` (new frozen dataclass) | `src/experiments/metrics.py` | `rate` (`float \| None`), `mastered`, `denominator`, `assigned`, `immature`, `missing_outcome`, `failed`, `invalid_ordering`, `conflicting` |
| `_outcome` (defects 1, 2) | `src/experiments/metrics.py` | one bucket per mature learner. Rows per `attempt_id` collapse when identical (including `type(passed)`) and are `conflicting` otherwise. `completion < attempt` → `invalid_ordering`. Mismatched assessment or a completion after +14 d → `missing_outcome`. `passed is True` → `mastered`, otherwise `failed` |
| `mastery_report(..., as_of=None)` (defects 3, 4) | `src/experiments/metrics.py` | first attempt = min `(occurred_at, attempt_id)` in `[assignment, +14 d]`. With `as_of`: data after it is ignored; `assignment + 14 d > as_of` → `immature`, left out of `denominator`. `rate = mastered / denominator`, or `None` |
| `first_eligible_checkpoint_mastery(..., as_of=None)` | `src/experiments/metrics.py` | thin float wrapper: `rate`, or `0.0` when `rate is None`. The docstring calls that `0.0` ambiguous and points to `mastery_report` |
| `assignment.function` (defect 6) | `experiments/exp_007.yaml` | describes sha256 of the compact UTF-8 JSON array, first 8 bytes big-endian, mod 100, `bucket < 50` → `variant_a` (propensity 0.5), and why plain concatenation is avoided. `algorithm: "hash_v2"` kept; nothing else in the yaml changed |
| Hardening tests (31) | `tests/experiments/test_metrics_hardening.py` | one or more per defect, edge cases, 17,280-ordering permutation test, invariant on 14 fixtures |
| Yaml ↔ code tests (8) | `tests/experiments/test_exp007_yaml_matches_code.py` | parses the documented rule's numbers from the yaml and recomputes buckets for 5 subjects independently of `assign()` |
| Audit log | `docs/audit/week4-fix4-metrics.md` | this file |

Not touched: `src/experiments/assignment.py`,
`tests/experiments/test_deterministic_assignment.py`,
`tests/experiments/test_metrics.py`, every section of
`docs/metrics-framework.md` except the exp_007 example, and every other
`src/` file.

## Goal

Week 3 review, row 27: `first_eligible_checkpoint_mastery` in
`src/experiments/metrics.py` and the exp_007 spec have six defects:

| # | Defect | Wanted behaviour |
|---|---|---|
| 1 | A passing completion dated **before** its matching attempt counts as mastery | completion must be at or after its attempt; otherwise not mastery (and counted as invalid ordering) |
| 2 | Conflicting duplicate completions for one `attempt_id` are last-write-wins, so input order changes the result | order-independent; a `passed=True` + `passed=False` conflict is not mastery (counted as conflicting). Identical duplicates collapse to one |
| 3 | Equal-time first attempts have no stable tie key | ties broken by smallest `attempt_id` |
| 4 | No data cutoff: learners whose 14-day window has not closed count as failures | new optional `as_of`; immature learners leave the denominator and are reported separately |
| 5 | Naive and timezone-aware timestamps cannot be compared (`TypeError`) | naive = UTC; `Z` and offsets normalized to UTC |
| 6 | `experiments/exp_007.yaml` says `H(subject_id \|\| 'exp_007') mod 100`; `assign()` uses `sha256(json.dumps([subject_id, exp_id], separators=(",", ":")))`, first 8 bytes big-endian, mod 100, `bucket < 50` → `variant_a` | the spec text describes the real algorithm |

Planned API (from the task):

```python
first_eligible_checkpoint_mastery(assignments, attempts, completions, as_of=None) -> float
mastery_report(assignments, attempts, completions, as_of=None)
    # -> object with: rate (float | None), mastered, denominator, assigned,
    #    immature, missing_outcome, failed, invalid_ordering, conflicting
```

## Files I will touch

| File | Status | Change |
|---|---|---|
| `docs/audit/week4-fix4-metrics.md` | new | this audit log |
| `tests/experiments/test_metrics_hardening.py` | new | 8 tests in Step 1; bucket invariant and `True`/`1` tests in Step 2; 7 edge-case tests (incl. 13b) and a 14-fixture parametrized invariant in Step 3 (31 collected) |
| `tests/experiments/test_exp007_yaml_matches_code.py` | new (Step 4) | 8 tests (4 functions, one parametrized over 5 subjects) |
| `src/experiments/metrics.py` | modified in Steps 2–3 (+160 / −38 total) | `_timestamp`, new `WINDOW`, `MasteryReport`, `_outcome`, `_cutoff`, `mastery_report` (Step 3: tie-break, `as_of`); `first_eligible_checkpoint_mastery` is a wrapper with `as_of` |
| `experiments/exp_007.yaml` | modified in Step 4 (1 line) | `assignment.function` text (defect 6) |
| `docs/metrics-framework.md` | modified in "Doc fix" (2 lines) | exp_007 Implementation Example only: lines 48 (assignment rule) and 49 (primary metric name and maturity sentence) |

Not to be touched: `tests/experiments/test_metrics.py` (the 10-learner test),
`src/experiments/assignment.py` (the code is correct; only the spec text is
wrong), every other test.

`docs/metrics-framework.md:48` repeated the same stale formula (see "Users").
Fixed in "Doc fix", together with line 49.

## Users of the metric and of exp_007

Command:

```bash
grep -rn "first_eligible_checkpoint_mastery\|experiments.metrics\|exp_007" src tests experiments docs --include="*"
```

Output (exit 0; run before the new test file existed):

```
experiments/exp_007.yaml:1:exp_id: "exp_007"
experiments/exp_007.yaml:12:  function: "H(subject_id || 'exp_007') mod 100"
tests/experiments/test_deterministic_assignment.py:3:Given learner L1 and experiment exp_007 with a fixed hash algorithm v2, when assignment runs twice, then L1 is bucketed into the same variant both times, with the propensity score logged on the event.
tests/experiments/test_deterministic_assignment.py:14:    first = assign("L1", "exp_007")
tests/experiments/test_deterministic_assignment.py:15:    second = assign("L1", "exp_007")
tests/experiments/test_deterministic_assignment.py:24:        assign(f"synthetic-{index}", "exp_007")[0] for index in range(10_000)
tests/experiments/test_deterministic_assignment.py:55:        ("L1", "exp_007", "variant_b"),
tests/experiments/test_deterministic_assignment.py:56:        ("learner-001", "exp_007", "variant_b"),
tests/experiments/test_deterministic_assignment.py:57:        ("learner-002", "exp_007", "variant_a"),
tests/experiments/test_deterministic_assignment.py:58:        ("learner-004", "exp_007", "variant_a"),
tests/experiments/test_metrics.py:1:"""Tests for the exp_007 primary metric."""
tests/experiments/test_metrics.py:3:from src.experiments.metrics import first_eligible_checkpoint_mastery
tests/experiments/test_metrics.py:6:def test_first_eligible_checkpoint_mastery_uses_first_attempt_and_fourteen_day_window():
tests/experiments/test_metrics.py:149:    assert first_eligible_checkpoint_mastery(assignments, attempts, completions) == 0.2
src/experiments/metrics.py:13:def first_eligible_checkpoint_mastery(
tests/experiments/test_assignment_vs_exposure.py:3:Given L1 is assigned to variant_a for exp_007, when L1 has not yet opened the app, then only Experiment Assignment exists. Experiment Exposure is emitted only once L1 actually views the treatment surface.
docs/traceability-matrix.md:44:12. **Deterministic Experiment Assignment (M7):** Given learner L1 and experiment exp_007 with a fixed hash algorithm v2, when assignment runs twice, then L1 is bucketed into the same variant both times, with the propensity score logged on the event.
docs/traceability-matrix.md:45:13. **Assignment vs. Exposure (M7):** Given L1 is assigned to variant_a for exp_007, when L1 has not yet opened the app, then only Experiment Assignment exists. Experiment Exposure is emitted only once L1 actually views the treatment surface.
docs/metrics-framework.md:43:**Implementation Example (exp_007)**
docs/metrics-framework.md:44:* **Experiment Identifier (exp_id):** "exp_007" (Streak-Risk Reminder Policy vs. Control).
docs/metrics-framework.md:48:* **Assignment & Exclusions:** 50/50 split via `H(subject_id || "exp_007") mod 100`. Excludes learners with platform tenure < 3 days or those who opted out of the push × streak_risk consent pair.
```

| Location | Kind | Relevance |
|---|---|---|
| `src/experiments/metrics.py:13-61` | definition | the metric itself. Its only helper is `_timestamp` (lines 7-10) |
| `tests/experiments/test_metrics.py:3-149` | test, passes | the only caller of the metric: the 10-learner fixture, expected `0.2`. Must stay untouched and green. Positional 3-argument call, no `as_of` |
| `experiments/exp_007.yaml:12` | spec | the stale `function:` text (defect 6) |
| `docs/metrics-framework.md:48` | doc | the same stale formula, written with `"exp_007"`. **Not** covered by the task's file list; flagged here |
| `tests/experiments/test_deterministic_assignment.py:3-58` | test, passes | calls `assign()`. `test_assignment_matches_golden_hash_buckets` (lines 51-64) already recomputes the real sha256/json/8-byte/big-endian/`< 50` algorithm and pins 5 golden variants, so the **code** side of defect 6 is already pinned |
| `tests/experiments/test_assignment_vs_exposure.py:3` | test docstring | mentions exp_007 only |
| `docs/traceability-matrix.md:44-45` | doc | acceptance-test text; mentions "hash algorithm v2" but no formula |

Nothing in `src/` except the definition references the metric. No service,
API or pipeline calls it. So the fix only has to keep the 3-positional-argument
call and the `0.2` result for the existing fixture.

## Before

`src/experiments/metrics.py` (current, unchanged):

```python
def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError("metric timestamps must be ISO 8601 strings")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def first_eligible_checkpoint_mastery(assignments, attempts, completions) -> float:
    if not assignments:
        return 0.0

    first_attempts: dict[str, Mapping[str, object]] = {}
    for attempt in attempts:
        learner_id = attempt["learner_id"]
        if learner_id not in assignments:
            continue
        assignment_at = _timestamp(assignments.get(learner_id, ""))
        attempted_at = _timestamp(attempt["occurred_at"])
        if assignment_at <= attempted_at <= assignment_at + timedelta(days=14):
            previous = first_attempts.get(learner_id)
            if previous is None or attempted_at < _timestamp(previous["occurred_at"]):
                first_attempts[learner_id] = attempt

    completions_by_attempt = {
        completion["attempt_id"]: completion for completion in completions
    }
    mastered = 0
    for learner_id in assignments:
        attempt = first_attempts.get(learner_id)
        if attempt is None:
            continue
        completion = completions_by_attempt.get(attempt["attempt_id"])
        if (
            completion is not None
            and completion["assessment_id"] == attempt["assessment_id"]
            and completion["assessment_version"] == attempt["assessment_version"]
            and completion["passed"] is True
            and _timestamp(assignments[learner_id])
            <= _timestamp(completion["occurred_at"])
            <= _timestamp(assignments[learner_id]) + timedelta(days=14)
        ):
            mastered += 1
    return mastered / len(assignments)
```

Where each defect is in the code:

| # | Line(s) | Cause |
|---|---|---|
| 1 | 51-58 | the completion is checked against the assignment window only, never against `attempted_at` |
| 2 | 42-44 | `{attempt_id: completion}` dict comprehension: the last row per `attempt_id` wins |
| 3 | 39 | strict `<` keeps whichever equal-time attempt arrived first |
| 4 | 61 | denominator is always `len(assignments)`; no cutoff argument exists |
| 5 | 10, 37 | `fromisoformat` keeps a naive string naive; comparing it with an aware value raises `TypeError` |
| 6 | `exp_007.yaml:12` | spec text, not code |

Also seen while reading (not one of the six defects): `if not assignments:
return 0.0` returns a rate of 0 for an empty population. The planned
`mastery_report.rate` is `float | None`, which suggests `None` there. Left
for the fix step (see "Uncertainty").

`experiments/exp_007.yaml:10-13` (current):

```yaml
assignment:
  algorithm: "hash_v2"
  function: "H(subject_id || 'exp_007') mod 100"
  split: "50/50"
```

`src/experiments/assignment.py:7-13` (the truth, not to be changed):

```python
    encoded = json.dumps([subject_id, exp_id], separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], byteorder="big") % 100
    variant = "variant_a" if bucket < 50 else "variant_b"
    return variant, 0.5
```

## After (Step 2): defects 1, 2, 5 and `mastery_report`

Only `src/experiments/metrics.py` changed. `git diff --stat`:
`1 file changed, 122 insertions(+), 38 deletions(-)` (after Step 2b). Nothing else in `src/`,
`experiments/` or the existing tests changed.

### `_timestamp` (defect 5)

Before:

```python
def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError("metric timestamps must be ISO 8601 strings")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
```

After:

```python
def _timestamp(value: object) -> datetime:
    """Parse an ISO 8601 string to an aware UTC datetime; naive means UTC."""
    if not isinstance(value, str):
        raise TypeError("metric timestamps must be ISO 8601 strings")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
```

The `TypeError` for non-strings is kept. `Z` becomes `+00:00`. A naive value
is tagged UTC. Any offset is converted to UTC, so every comparison is between
aware UTC datetimes.

### New: `WINDOW`, `MasteryReport`, `_outcome`, `mastery_report`

Before: none existed. The 14-day window was `timedelta(days=14)` written
inline twice.

After:

```python
WINDOW = timedelta(days=14)

@dataclass(frozen=True)
class MasteryReport:
    rate: float | None
    mastered: int
    denominator: int
    assigned: int
    immature: int
    missing_outcome: int
    failed: int
    invalid_ordering: int
    conflicting: int


def _outcome(assigned_at, attempt, records) -> str:
    if attempt is None or not records:
        return "missing_outcome"
    # Identical records collapse; any disagreement is a conflict, never a pick.
    # type() keeps True and 1 apart, which compare equal.   (added in Step 2b)
    distinct = {
        (record["assessment_id"], record["assessment_version"],
         type(record["passed"]), record["passed"], _timestamp(record["occurred_at"]))
        for record in records
    }
    if len(distinct) > 1:
        return "conflicting"
    assessment_id, assessment_version, _, passed, completed_at = distinct.pop()
    if (assessment_id != attempt["assessment_id"]
            or assessment_version != attempt["assessment_version"]):
        return "missing_outcome"
    if completed_at < _timestamp(attempt["occurred_at"]):
        return "invalid_ordering"
    if completed_at > assigned_at + WINDOW:
        return "missing_outcome"
    return "mastered" if passed is True else "failed"


def mastery_report(assignments, attempts, completions) -> MasteryReport:
    assigned_at = {learner_id: _timestamp(value) for learner_id, value in assignments.items()}

    first_attempts = {}          # learner -> (attempted_at, attempt)
    for attempt in attempts:
        ...                      # same eligibility and strict-< first-seen rule as before
    completions_by_attempt = {}  # attempt_id -> [every completion row]
    for completion in completions:
        completions_by_attempt.setdefault(completion["attempt_id"], []).append(completion)

    counts = dict.fromkeys(("mastered", "failed", "missing_outcome",
                            "invalid_ordering", "conflicting"), 0)
    for learner_id, start in assigned_at.items():
        ...                      # first attempt + its records -> _outcome -> counts[bucket] += 1

    denominator = len(assigned_at)
    return MasteryReport(
        rate=counts["mastered"] / denominator if denominator else None,
        denominator=denominator, assigned=len(assigned_at), immature=0, **counts,
    )
```

(Condensed; the file has the full layout.)

How `_outcome` decides, in order (first match wins):

| # | Condition | Bucket | Defect |
|---|---|---|---|
| 1 | no first attempt in the window, or no completion rows for it | `missing_outcome` | |
| 2 | the rows for that `attempt_id` differ in any of `(assessment_id, assessment_version, passed (value and type), occurred_at as UTC)` | `conflicting` | 2 |
| 3 | the single distinct row's assessment id/version don't match the attempt | `missing_outcome` | |
| 4 | `completion < attempt` | `invalid_ordering` | 1 |
| 5 | `completion > assignment + 14 d` | `missing_outcome` | |
| 6 | `passed is True` → `mastered`, otherwise `failed` | | |

Combined with attempt eligibility (`assignment <= attempt <= assignment +
14 d`), a learner is mastered only when
`assignment <= attempt <= completion <= assignment + 14 d`, inclusive at both
ends, with exactly one distinct matching passing completion.

Exactly one bucket per assigned learner, by construction: each learner is
classified once by one `_outcome` call. `immature` is always 0 for now, and
`denominator == assigned`.

### `first_eligible_checkpoint_mastery`

Before: the full computation (see "Before" above).

After:

```python
def first_eligible_checkpoint_mastery(assignments, attempts, completions) -> float:
    """... Returns 0.0 when nobody is assigned."""
    rate = mastery_report(assignments, attempts, completions).rate
    return 0.0 if rate is None else rate
```

Same signature, still a `float`. Empty `assignments` still gives `0.0`. The
docstring gained one sentence; the rest is unchanged.

### Decisions and behaviour changes

- **The conflict check runs before the assessment match.** If one
  `attempt_id` has a matching row and a row for another assessment/version,
  that is `conflicting`, as the task says ("differ in any of those fields").
  It is not "ignore the mismatched row".
- **A mismatched single completion counts as `missing_outcome`.** There is no
  matching completion. That matches the old result (not mastered) for L5/L6
  in the 10-learner fixture.
- **A completion after +14 days counts as `missing_outcome`, not `failed`.**
  No outcome was observed inside the window. `failed` is kept for "a valid
  in-window completion with `passed` not `True`". The task's bucket list
  doesn't name this case, so this is my choice (see "Assumptions").
- **Identity uses the parsed UTC instant.** `"…T01:00:00Z"` and
  `"…T06:30:00+05:30"` for the same attempt collapse instead of conflicting.
- **`passed` keeps `is True`.** A non-bool truthy value (`1`, `"true"`) in a
  valid completion goes to `failed`. In the identity key, `passed` is stored
  with its type, so `True` and `1` are different values: `[True, 1]` is
  `conflicting` in either order (Step 2b, test 10).
- **Assignment timestamps are now parsed eagerly** for every assigned
  learner. Before, a learner's assignment string was parsed only if they had
  an attempt. A malformed assignment for a learner with no attempts now
  raises; before, it was silently ignored. Completion timestamps are parsed
  only for the first attempt's rows, as before (plus the mismatched-row case,
  which before was never parsed).
- **Not changed yet:** the equal-time attempt tie-break (still first seen,
  strict `<`), and `as_of` / `immature`.

### 10-learner fixture under the new buckets (scratch check)

I wrapped the existing test's call with a spy that recorded the arguments, then
ran `mastery_report` on them. The test file was not edited.

```
MasteryReport(rate=0.2, mastered=2, denominator=10, assigned=10, immature=0, missing_outcome=6, failed=2, invalid_ordering=0, conflicting=0)
```

| Learner | Bucket | Why |
|---|---|---|
| L1, L7 | mastered | passing completion in window |
| L2, L3 | failed | first attempt's completion `passed=False` (L3's later passing `A3-later` is ignored) |
| L4 | missing_outcome | completion Jan 20, after +14 d |
| L5 | missing_outcome | completion for `other-checkpoint` |
| L6 | missing_outcome | completion for version 2 |
| L8, L9 | missing_outcome | no completion |
| L10 | missing_outcome | only attempt (Jan 16) is outside the window |

The rate is 0.2, the same as before.

## After (Step 3): defects 3 and 4

Only `src/experiments/metrics.py` changed in this step (+38 lines on top of
Step 2; cumulative `git diff --stat`: `1 file changed, 160 insertions(+), 38
deletions(-)`). `_timestamp`, `MasteryReport` and `_outcome` are unchanged.
There is no `datetime.now` / `utcnow` / `today()`: the grep returns nothing,
exit 1.

### New: `_cutoff`

```python
def _cutoff(value: datetime | str | None) -> datetime | None:
    """Normalize an optional as_of to aware UTC; naive means UTC."""
    if value is None or isinstance(value, str):
        return None if value is None else _timestamp(value)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
```

`as_of` can be `None`, an ISO string (parsed with `_timestamp`), or a
`datetime`. A naive `datetime` is treated as UTC, and an aware one is
converted to UTC.

### `mastery_report`

Before (end of Step 2):

```python
def mastery_report(assignments, attempts, completions) -> MasteryReport:
    assigned_at = {...}
    first_attempts: dict[str, tuple[datetime, Mapping]] = {}
    for attempt in attempts:
        ...
        if start <= attempted_at <= start + WINDOW:
            previous = first_attempts.get(learner_id)
            if previous is None or attempted_at < previous[0]:
                first_attempts[learner_id] = (attempted_at, attempt)
    ...
    counts = dict.fromkeys(("mastered", "failed", "missing_outcome",
                            "invalid_ordering", "conflicting"), 0)
    for learner_id, start in assigned_at.items():
        ...
        counts[_outcome(start, attempt, records)] += 1

    denominator = len(assigned_at)
    return MasteryReport(rate=..., denominator=denominator,
                         assigned=len(assigned_at), immature=0, **counts)
```

After:

```python
def mastery_report(assignments, attempts, completions,
                   as_of: datetime | str | None = None) -> MasteryReport:
    cutoff = _cutoff(as_of)
    assigned_at = {...}
    first_attempts: dict[str, tuple[tuple[datetime, object], Mapping]] = {}
    for attempt in attempts:
        ...
        attempted_at = _timestamp(attempt["occurred_at"])
        if cutoff is not None and attempted_at > cutoff:
            continue
        if start <= attempted_at <= start + WINDOW:
            # The attempt_id breaks equal-time ties, so input order cannot matter.
            key = (attempted_at, attempt["attempt_id"])
            previous = first_attempts.get(learner_id)
            if previous is None or key < previous[0]:
                first_attempts[learner_id] = (key, attempt)
    ...
    counts = dict.fromkeys(("mastered", "failed", "missing_outcome",
                            "invalid_ordering", "conflicting", "immature"), 0)
    for learner_id, start in assigned_at.items():
        if cutoff is not None and start + WINDOW > cutoff:
            counts["immature"] += 1
            continue
        ...
        if cutoff is not None:
            records = [r for r in records if _timestamp(r["occurred_at"]) <= cutoff]
        counts[_outcome(start, attempt, records)] += 1

    denominator = len(assigned_at) - counts["immature"]
    return MasteryReport(
        rate=counts["mastered"] / denominator if denominator else None,
        denominator=denominator, assigned=len(assigned_at), **counts,
    )
```

(Condensed; the file has the full layout and the expanded docstring.)

| Rule | Implementation |
|---|---|
| First attempt = min by `(occurred_at, attempt_id)` within `[assignment, assignment + 14 d]` | key tuple compared with `<`. The minimum of a set doesn't depend on order, so the pick is order-independent (defect 3) |
| Ignore attempts after `as_of` | skipped before the window check |
| Ignore completions after `as_of` | filtered out of the first attempt's rows before `_outcome`, so the conflict check also only sees observable rows |
| Mature iff `assignment + 14 d <= as_of` | `start + WINDOW > cutoff` → `immature`, checked **before** any outcome, so an immature learner who already passed is still only `immature` |
| `denominator = assigned − immature`; `rate = mastered / denominator`, or `None` at 0 | as written |
| `as_of=None` → everyone mature | `cutoff is None` skips every maturity and filter check, so the output is identical to Step 2 |

### `first_eligible_checkpoint_mastery`

Before (Step 2):

```python
def first_eligible_checkpoint_mastery(assignments, attempts, completions) -> float:
    """... counted as not mastered. Returns 0.0 when nobody is assigned."""
    rate = mastery_report(assignments, attempts, completions).rate
    return 0.0 if rate is None else rate
```

After:

```python
def first_eligible_checkpoint_mastery(assignments, attempts, completions,
                                      as_of: datetime | str | None = None) -> float:
    """... counted as not mastered. ``as_of`` is passed to ``mastery_report``.

    Returns 0.0 when there are no mature assigned learners. That 0.0 is
    ambiguous: it looks the same as "nobody mastered". Use
    ``mastery_report``, whose ``rate`` is None in that case, as the
    reviewable interface.
    """
    rate = mastery_report(assignments, attempts, completions, as_of=as_of).rate
    return 0.0 if rate is None else rate
```

### Why the `as_of` filters cannot change a report

- **Mature learner.** `as_of >= assignment + 14 d`. Any attempt after `as_of`
  is also after the window, so the window check already drops it. Any
  completion after `as_of` is also after `assignment + 14 d`. It would be
  `missing_outcome` without the filter, and it is `missing_outcome` with it
  (no rows). The exception would be a conflict between an in-window row and
  a post-`as_of` row: with the filter that is in-window-only, without it
  `conflicting`. So the completion filter *can* matter in that one case.
  Pinned in Step 3b by `test_completion_after_as_of_is_ignored`.
- **Immature learner.** Bucketed `immature` before their data is looked at.

For attempts, the tests can confirm the filter is harmless but can't prove
it works. For completions, test 13b proves it works.

## After (Step 4): defect 6, exp_007.yaml

Only `experiments/exp_007.yaml` line 12 changed (`git diff --stat`: `2 +-`).
`src/experiments/assignment.py` and every hash behaviour are unchanged.

Before:

```yaml
assignment:
  algorithm: "hash_v2"
  function: "H(subject_id || 'exp_007') mod 100"
  split: "50/50"
```

After:

```yaml
assignment:
  algorithm: "hash_v2"
  function: "bucket = int.from_bytes(sha256(json.dumps([subject_id, exp_id], separators=(',', ':')).encode('utf-8')).digest()[:8], 'big') mod 100, i.e. the first 8 bytes of the SHA-256 of the compact UTF-8 JSON array [subject_id, exp_id], read as a big-endian unsigned integer, mod 100; bucket < 50 -> variant_a (propensity 0.5), otherwise variant_b. The JSON array deliberately avoids plain concatenation, which collides: ('ab', 'c') and ('a', 'bc') would both hash 'abc'."
  split: "50/50"
```

`diff` against a snapshot taken before the edit shows only line 12:

```
12c12
<   function: "H(subject_id || 'exp_007') mod 100"
---
>   function: "bucket = int.from_bytes(sha256(json.dumps([subject_id, exp_id], ...
```

- The text keeps the fragments the Step 1 hardening test asserts on the
  `function:` line (`sha256`, `json.dumps([subject_id, exp_id]`, `[:8]`,
  `big`, `mod 100`, no `||`) and mentions `bucket < 50` and `variant_a`.
- It is one double-quoted YAML scalar with no `"` or `\` inside, so the file
  stays valid YAML. The inner quotes are single quotes, so the code reads
  `separators=(',', ':')` rather than `(",", ":")`; Python treats them the
  same. **Not parsed:** `pyyaml` isn't installed, so I checked the quoting by
  hand and with a small script, not with a YAML parser.


## Tests

New file: `tests/experiments/test_metrics_hardening.py`. Shared helpers
`_attempt(...)` and `_completion(...)` build rows with
`assessment_id="checkpoint"` and `assessment_version=1` by default.
`mastery_report` is imported inside the two tests that use it, so a missing
name fails only those tests and not the whole module at collection.

| # | Defect | Test | Fixture (hand-computed) | Expected | Current result |
|---|---|---|---|---|---|
| 1 | 1 | `test_completion_before_its_attempt_is_not_mastery` | L1 assigned Jan 1, attempt Jan 5, completion Jan 3 `passed=True` | 0/1 = **0.0** | FAIL `assert 1.0 == 0.0` |
| 2 | 2 | `test_conflicting_duplicate_completions_are_order_independent_and_not_mastery` | L1, attempt A1 Jan 2; two A1 completions, `True` and `False`, run in both orders | **(0.0, 0.0)** | FAIL `(0.0, 1.0) == (0.0, 0.0)`: the `[False, True]` order gives 1.0 |
| 3 | 2 | `test_identical_duplicate_completions_collapse_to_one` | L1, A1 Jan 2, the same passing completion twice | rate **1.0**; report `mastered=1, conflicting=0` | FAIL `ImportError: cannot import name 'mastery_report'` |
| 4 | 3 | `test_tied_first_attempts_use_smallest_attempt_id` | L1, attempts A2 and A1 both Jan 2 00:00; A1 passed, A2 failed; run in both orders | A1 is first → **(1.0, 1.0)** | FAIL `(0.0, 1.0) == (1.0, 1.0)`: order `[A2, A1]` picks A2 |
| 5 | 5 | `test_naive_and_offset_timestamps_are_normalized_to_utc` | L1 naive `2026-01-01T00:00:00` (= 00:00Z), attempt `05:30+05:30` (= 00:00Z, on the boundary, eligible), completion `2026-01-15T00:00:00Z` (exactly +14 d, eligible). L2 `00:00Z`, attempt `05:00+05:30` (= Dec 31 23:30Z, before assignment, not eligible) | 1/2 = **0.5** | FAIL `TypeError: can't compare offset-naive and offset-aware datetimes` (`metrics.py:37`) |
| 6 | 4 | `test_immature_learner_is_not_counted_as_failure` | L1 assigned Dec 20 (window closed Jan 3), passed; L2 assigned Jan 1, nothing yet; `as_of=2026-01-10T00:00:00Z` | L2 immature → 1/1 = **1.0** (today it would be 1/2 = 0.5) | FAIL `TypeError: ... unexpected keyword argument 'as_of'` |
| 7 | 4 | `test_mastery_report_counts_immature_learner_separately` | same as 6 | `rate=1.0, assigned=2, denominator=1, immature=1, mastered=1, failed=0, missing_outcome=0, invalid_ordering=0, conflicting=0` | FAIL `ImportError: cannot import name 'mastery_report'` |
| 8 | 6 | `test_exp_007_assignment_function_matches_implementation` | reads `experiments/exp_007.yaml` as text. Its `function:` line must contain `sha256`, `json.dumps([subject_id, exp_id]`, `[:8]`, `big`, `mod 100` and no `\|\|`; the file must mention `bucket < 50` and `variant_a` | text matches `assign()` | FAIL `AssertionError: ('sha256', '  function: "H(subject_id \|\| \'exp_007\') mod 100"')` |

Why each one fails, and whether it is the stated reason:

| # | Failure | Stated reason? |
|---|---|---|
| 1 | returns 1.0: the Jan 3 completion is inside the assignment window, and its order relative to the Jan 5 attempt is never checked | yes, defect 1 |
| 2 | dict last-write-wins: `[True, False]` → 0.0, `[False, True]` → 1.0 | yes, defect 2 (order dependence shows up as the second element) |
| 3 | `mastery_report` doesn't exist | yes, missing planned API. Note: its first assertion (rate 1.0) would **pass** today, because identical rows already collapse in the dict. Only the `conflicting == 0` report check is new behaviour |
| 4 | first-seen equal-time attempt wins: `[A2, A1]` → A2 (failed) → 0.0 | yes, defect 3 |
| 5 | naive assignment vs aware attempt comparison raises | yes, defect 5 |
| 6 | no `as_of` parameter | yes, missing planned API (defect 4) |
| 7 | `mastery_report` doesn't exist | yes, missing planned API (defect 4) |
| 8 | the yaml text is the old formula | yes, defect 6 |

Existing test untouched: `tests/experiments/test_metrics.py` still passes
(below). `git status` shows no change to it.

(Results above are from Step 1. After Step 2, tests 1, 2, 3 and 5 pass; tests
4, 6, 7 and 8 still fail, see Step 2 output.)

Step 2 addition:

| # | Test | Fixture (hand-computed) | Expected | Result |
|---|---|---|---|---|
| 9 | `test_report_buckets_partition_assigned_learners` | 7 learners, all assigned Jan 1Z: L1 passed → mastered; L2 `passed=False` → failed; L3 no attempt → missing; L4 attempt, no completion → missing; L5 attempt Jan 5, completion Jan 3 → invalid_ordering; L6 `True` + `False` rows → conflicting; L7 completion Jan 20 → missing. Run with completions forward and reversed | `mastered=1, failed=1, missing_outcome=3, invalid_ordering=1, conflicting=1, immature=0, assigned=denominator=7, rate=1/7`; both orders give equal reports; `mastered + failed + missing_outcome + invalid_ordering + conflicting + immature == assigned` | PASS |
| 10 | `test_true_and_one_are_conflicting_in_either_order` (Step 2b) | L1, A1 Jan 2; two A1 completions, identical except `passed=True` vs `passed=1`, run in both orders | equal reports; `conflicting=1, mastered=0, rate=0.0` | PASS (would have failed before Step 2b: the two orders gave `mastered` 1 vs 0) |

Step 3 additions (all PASS):

| # | Test | Fixture (hand-computed) | Expected |
|---|---|---|---|
| 11 | `test_as_of_exactly_at_window_close_is_mature` | L1 assigned Jan 1Z, passed Jan 2; `as_of = datetime(2026, 1, 15, tzinfo=utc)` | Jan 1 + 14 d == as_of → mature; `immature=0, mastered=1, denominator=1, rate=1.0` |
| 12 | `test_as_of_one_second_before_window_close_is_immature` | same; `as_of="2026-01-14T23:59:59Z"` | immature even though passed; `immature=1, mastered=0, denominator=0, rate=None` |
| 13 | `test_attempt_after_as_of_is_ignored` | L1 assigned Dec 20 (window closes Jan 3); attempt and passing completion Jan 12; `as_of = datetime(2026, 1, 10)` (naive → UTC) | report equals the no-data report: `missing_outcome=1, immature=0, rate=0.0`. **Does not discriminate** (see the mutation check) |
| 13b | `test_completion_after_as_of_is_ignored` (Step 3b) | L1 assigned Dec 20; attempt Dec 21; completions Dec 21 `passed=True` and Jan 12 `passed=False` | `as_of` Jan 10: only the Dec 21 row is visible → `mastered=1, conflicting=0, rate=1.0`. No `as_of`: both rows → `conflicting=1, rate=0.0`. Catches removing the completion filter |
| 14 | `test_zero_mature_learners_gives_rate_none` | L1 Jan 1 (passed), L2 Jan 3; `as_of` Jan 10 | `assigned=2, immature=2, denominator=0, rate=None`; the wrapper returns `0.0`; `mastery_report({}, [], []).rate is None` |
| 15 | `test_two_arms_evaluated_separately_are_independent` | arm A {L1, L2 Jan 1}, arm B {L3 Jan 1, L4 Jan 5}; shared attempts/completions; `as_of` Jan 16 | A: L1 mastered, L2 failed → 0.5. B: L3 missing, L4 immature (Jan 19 > Jan 16, already passed) → 0.0. A's report equals A computed on A-only rows. Every count field of the combined report equals A + B |
| 16 | `test_every_input_permutation_gives_identical_report` | L1 attempts A2/A1 at the same instant (A1 passed, A2 failed); L2 passed; L3 C1 with `True` + `False` rows | `mastered=2, conflicting=1, rate=2/3` for all 6 × 24 × 120 = **17,280** orderings of assignments × attempts × completions |
| 17 | `test_bucket_sum_invariant_holds_on_every_fixture[...]` (14 cases) | every fixture in the file: `before_attempt`, `boundary_immature`, `boundary_mature`, `conflicting`, `empty`, `identical`, `maturity`, `partition`, `permutation`, `tie`, `timestamps`, `true_and_one`, `two_arms`, `zero_mature` | bucket sum == `assigned`; `denominator == assigned − immature`; `rate == mastered / denominator` or `None` |

Helpers added: `_passed_on_jan_2`, `_assert_partition`, `_two_arms`,
`_permutation_fixture`, `FIXTURES`. Imports added: `datetime`, `timezone`,
`itertools.permutations`, `pytest`. Earlier tests are unchanged. `FIXTURES`
copies the data of the inline fixtures (1–5, 9, 10) so the invariant runs on
them too. The 10-learner fixture in `test_metrics.py` is not in `FIXTURES`,
because that file must stay untouched. The Step 2 scratch spy check covers its
buckets.

Step 4 file: `tests/experiments/test_exp007_yaml_matches_code.py` (all PASS)

| # | Test | What it checks |
|---|---|---|
| Y1 | `test_yaml_keeps_hash_v2_and_drops_concatenation_formula` | `algorithm: "hash_v2"` present; the `function:` line mentions `json`; no `\|\|` anywhere in the file |
| Y2 | `test_documented_rule_is_first_8_bytes_big_endian_mod_100_below_50` | regexes parse `[:N]`, `mod N`, `bucket < N -> variant_a` from the `function:` line and require `big-endian` and `'big'`. Result must be `(8, "big", 100, 50)` |
| Y3 | `test_documented_rule_matches_assign[...]` × 5 (`L1`, `learner-001`, `learner-002`, `learner-004`, `synthetic-0`) | rebuilds the bucket with `hashlib` using the **numbers parsed from the yaml** (not hard-coded) and asserts `assign(subject, "exp_007") == (expected, 0.5)` |
| Y4 | `test_samples_cover_both_variants` | the 5 samples include both `variant_a` and `variant_b`, so Y3 couldn't pass against a constant `assign()` |

Checked against the old yaml (scratch, pointing the module's `YAML` path at
the pre-edit snapshot):

```
test_yaml_keeps_hash_v2_and_drops_concatenation_formula: FAILS on old yaml (AssertionError())
test_documented_rule_is_first_8_bytes_big_endian_mod_100_below_50: FAILS on old yaml (AssertionError('  function: "H(subject_id || \'exp_007\') mod 100"'))
test_documented_rule_matches_assign[L1]: FAILS on old yaml (AssertionError('  function: "H(subject_id || \'exp_007\') mod 100"'))
```

So the doc tests fail on the old text and pass on the new.
`test_deterministic_assignment.py` still pins the code side (golden buckets)
and was not touched.


## Exact commands and output

All commands use `.venv/bin/python` (the system `python` has no
`eventsourcing`). `-p no:cacheprovider` keeps pytest from writing
`.pytest_cache`.

### New file only

```bash
.venv/bin/python -m pytest tests/experiments/test_metrics_hardening.py -v -p no:cacheprovider
```

```
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0 -- /home/sampadram/Sampadram/IITH/SEM 7/DATGC/project/gamification-engine/.venv/bin/python
rootdir: /home/sampadram/Sampadram/IITH/SEM 7/DATGC/project/gamification-engine
configfile: pyproject.toml
collecting ... collected 8 items

tests/experiments/test_metrics_hardening.py::test_completion_before_its_attempt_is_not_mastery FAILED [ 12%]
tests/experiments/test_metrics_hardening.py::test_conflicting_duplicate_completions_are_order_independent_and_not_mastery FAILED [ 25%]
tests/experiments/test_metrics_hardening.py::test_identical_duplicate_completions_collapse_to_one FAILED [ 37%]
tests/experiments/test_metrics_hardening.py::test_tied_first_attempts_use_smallest_attempt_id FAILED [ 50%]
tests/experiments/test_metrics_hardening.py::test_naive_and_offset_timestamps_are_normalized_to_utc FAILED [ 62%]
tests/experiments/test_metrics_hardening.py::test_immature_learner_is_not_counted_as_failure FAILED [ 75%]
tests/experiments/test_metrics_hardening.py::test_mastery_report_counts_immature_learner_separately FAILED [ 87%]
tests/experiments/test_metrics_hardening.py::test_exp_007_assignment_function_matches_implementation FAILED [100%]
...
============================== 8 failed in 0.04s ===============================
```

(The full `-v` tracebacks were captured during the run. The compact form below
has every error message.)

```bash
.venv/bin/python -m pytest tests/experiments/test_metrics_hardening.py -q --tb=line -p no:cacheprovider
```

```
FFFFFFFF                                                                 [100%]
=================================== FAILURES ===================================
E   AssertionError: assert 1.0 == 0.0
     +  where 1.0 = first_eligible_checkpoint_mastery({'L1': '2026-01-01T00:00:00+00:00'}, [{'learner_id': 'L1', 'attempt_id': 'A1', 'assessment_id': 'checkpoint', 'assessment_version': 1, ...}], [{'attempt_id': 'A1', 'assessment_id': 'checkpoint', 'assessment_version': 1, 'passed': True, ...}])
/home/sampadram/Sampadram/IITH/SEM 7/DATGC/project/gamification-engine/tests/experiments/test_metrics_hardening.py:44: AssertionError: assert 1.0 == 0.0
E   assert (0.0, 1.0) == (0.0, 0.0)
      
      At index 1 diff: 1.0 != 0.0
      Use -v to get more diff
/home/sampadram/Sampadram/IITH/SEM 7/DATGC/project/gamification-engine/tests/experiments/test_metrics_hardening.py:59: assert (0.0, 1.0) == (0.0, 0.0)
E   ImportError: cannot import name 'mastery_report' from 'src.experiments.metrics' (/home/sampadram/Sampadram/IITH/SEM 7/DATGC/project/gamification-engine/src/experiments/metrics.py)
/home/sampadram/Sampadram/IITH/SEM 7/DATGC/project/gamification-engine/tests/experiments/test_metrics_hardening.py:63: ImportError: cannot import name 'mastery_report' from 'src.experiments.metrics' (/home/sampadram/Sampadram/IITH/SEM 7/DATGC/project/gamification-engine/src/experiments/metrics.py)
E   assert (0.0, 1.0) == (1.0, 1.0)
      
      At index 0 diff: 0.0 != 1.0
      Use -v to get more diff
/home/sampadram/Sampadram/IITH/SEM 7/DATGC/project/gamification-engine/tests/experiments/test_metrics_hardening.py:91: assert (0.0, 1.0) == (1.0, 1.0)
E   TypeError: can't compare offset-naive and offset-aware datetimes
/home/sampadram/Sampadram/IITH/SEM 7/DATGC/project/gamification-engine/src/experiments/metrics.py:37: TypeError: can't compare offset-naive and offset-aware datetimes
E   TypeError: first_eligible_checkpoint_mastery() got an unexpected keyword argument 'as_of'
/home/sampadram/Sampadram/IITH/SEM 7/DATGC/project/gamification-engine/tests/experiments/test_metrics_hardening.py:132: TypeError: first_eligible_checkpoint_mastery() got an unexpected keyword argument 'as_of'
E   ImportError: cannot import name 'mastery_report' from 'src.experiments.metrics' (/home/sampadram/Sampadram/IITH/SEM 7/DATGC/project/gamification-engine/src/experiments/metrics.py)
/home/sampadram/Sampadram/IITH/SEM 7/DATGC/project/gamification-engine/tests/experiments/test_metrics_hardening.py:138: ImportError: cannot import name 'mastery_report' from 'src.experiments.metrics' (/home/sampadram/Sampadram/IITH/SEM 7/DATGC/project/gamification-engine/src/experiments/metrics.py)
E   AssertionError: ('sha256', '  function: "H(subject_id || \'exp_007\') mod 100"')
    assert 'sha256' in '  function: "H(subject_id || \'exp_007\') mod 100"'
/home/sampadram/Sampadram/IITH/SEM 7/DATGC/project/gamification-engine/tests/experiments/test_metrics_hardening.py:162: AssertionError: ('sha256', '  function: "H(subject_id || \'exp_007\') mod 100"')
=========================== short test summary info ============================
FAILED tests/experiments/test_metrics_hardening.py::test_completion_before_its_attempt_is_not_mastery
FAILED tests/experiments/test_metrics_hardening.py::test_conflicting_duplicate_completions_are_order_independent_and_not_mastery
FAILED tests/experiments/test_metrics_hardening.py::test_identical_duplicate_completions_collapse_to_one
FAILED tests/experiments/test_metrics_hardening.py::test_tied_first_attempts_use_smallest_attempt_id
FAILED tests/experiments/test_metrics_hardening.py::test_naive_and_offset_timestamps_are_normalized_to_utc
FAILED tests/experiments/test_metrics_hardening.py::test_immature_learner_is_not_counted_as_failure
FAILED tests/experiments/test_metrics_hardening.py::test_mastery_report_counts_immature_learner_separately
FAILED tests/experiments/test_metrics_hardening.py::test_exp_007_assignment_function_matches_implementation
8 failed in 0.02s
```

### Context checks (no changes)

```bash
.venv/bin/python -m pytest tests/experiments/test_metrics.py -q -p no:cacheprovider
```

```
1 passed in 0.01s
```

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider --ignore=tests/experiments/test_metrics_hardening.py
```

```
145 passed, 9 skipped in 1.61s
```

So the suite without the new file is the same as at the end of Fix 3
(145 passed, 9 skipped).

### Git (read-only)

```bash
git status --short
git diff --stat
git log --oneline -3
```

```
?? tests/experiments/test_metrics_hardening.py
(git diff --stat: empty)
97559e3 fix(m2): keep quest reader per assignment and agree with the aggregate
0b59873 fix(m2): make streak reader and aggregate freeze balance order-independent
5c77444 fix(m2): accept new lessons tagged to an already-completed quest
```

(Run before this audit file was written; it is now also untracked.) Fix 3 is
committed as `97559e3`. There are no other local changes.

### Step 2: before editing `src/`

A new commit, `534b3c0` ("updates(m3,m5): …"), landed between Step 1 and
Step 2. It changes leaderboard and notification code and tests only:

```bash
git diff --stat 97559e3 HEAD          # 28 files, all under src/leaderboard, src/notifications, tests/leaderboard, tests/notifications
git diff 97559e3 HEAD -- src/experiments tests/experiments experiments docs/metrics-framework.md
```

```
(second command: empty)
```

So the metric code, its tests, the yaml and the framework doc are unchanged.
The suite totals changed, so I re-baselined:

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider --ignore=tests/experiments/test_metrics_hardening.py
.venv/bin/python -m pytest -q -p no:cacheprovider tests/experiments/test_metrics_hardening.py
```

```
172 passed, 4 skipped in 2.89s
8 failed in 0.07s
```

(Before `534b3c0` the first command gave 145 passed, 9 skipped.)

### Step 2: after the fix

```bash
.venv/bin/python -m pytest tests/experiments/test_metrics_hardening.py -v -p no:cacheprovider
```

(Filtered to PASSED/FAILED/`E`/summary lines.)

```
tests/experiments/test_metrics_hardening.py::test_completion_before_its_attempt_is_not_mastery PASSED [ 11%]
tests/experiments/test_metrics_hardening.py::test_conflicting_duplicate_completions_are_order_independent_and_not_mastery PASSED [ 22%]
tests/experiments/test_metrics_hardening.py::test_identical_duplicate_completions_collapse_to_one PASSED [ 33%]
tests/experiments/test_metrics_hardening.py::test_tied_first_attempts_use_smallest_attempt_id FAILED [ 44%]
tests/experiments/test_metrics_hardening.py::test_naive_and_offset_timestamps_are_normalized_to_utc PASSED [ 55%]
tests/experiments/test_metrics_hardening.py::test_immature_learner_is_not_counted_as_failure FAILED [ 66%]
tests/experiments/test_metrics_hardening.py::test_mastery_report_counts_immature_learner_separately FAILED [ 77%]
tests/experiments/test_metrics_hardening.py::test_exp_007_assignment_function_matches_implementation FAILED [ 88%]
tests/experiments/test_metrics_hardening.py::test_report_buckets_partition_assigned_learners PASSED [100%]
E       AssertionError: assert (0.0, 1.0) == (1.0, 1.0)
E       TypeError: first_eligible_checkpoint_mastery() got an unexpected keyword argument 'as_of'
E       TypeError: mastery_report() got an unexpected keyword argument 'as_of'
E           AssertionError: ('sha256', '  function: "H(subject_id || \'exp_007\') mod 100"')
E           assert 'sha256' in '  function: "H(subject_id || \'exp_007\') mod 100"'
FAILED tests/experiments/test_metrics_hardening.py::test_tied_first_attempts_use_smallest_attempt_id
FAILED tests/experiments/test_metrics_hardening.py::test_immature_learner_is_not_counted_as_failure
FAILED tests/experiments/test_metrics_hardening.py::test_mastery_report_counts_immature_learner_separately
FAILED tests/experiments/test_metrics_hardening.py::test_exp_007_assignment_function_matches_implementation
========================= 4 failed, 5 passed in 0.06s ==========================
```

All 4 required tests pass: before-attempt, conflict, identical duplicates and
timestamps. The new invariant test passes too. The 4 that still fail are the
allowed ones, and each fails for its original reason:
- tie-break: still first-seen
- maturity: no `as_of`. The second test now gets `TypeError: mastery_report()
  got an unexpected keyword argument 'as_of'` instead of the earlier
  `ImportError`, because `mastery_report` exists now.
- yaml: not edited yet

```bash
.venv/bin/python -m pytest tests/experiments/test_metrics.py -v -p no:cacheprovider
```

```
tests/experiments/test_metrics.py::test_first_eligible_checkpoint_mastery_uses_first_attempt_and_fourteen_day_window PASSED [100%]
============================== 1 passed in 0.01s ===============================
```

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider
```

```
FAILED tests/experiments/test_metrics_hardening.py::test_tied_first_attempts_use_smallest_attempt_id
FAILED tests/experiments/test_metrics_hardening.py::test_immature_learner_is_not_counted_as_failure
FAILED tests/experiments/test_metrics_hardening.py::test_mastery_report_counts_immature_learner_separately
FAILED tests/experiments/test_metrics_hardening.py::test_exp_007_assignment_function_matches_implementation
4 failed, 177 passed, 4 skipped in 2.94s
```

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider --ignore=tests/experiments/test_metrics_hardening.py
```

```
172 passed, 4 skipped in 2.84s
```

177 = the 172 baseline + 5 passing hardening tests. (After Step 2b: 178, see below.) Without the hardening
file the suite is unchanged (172 / 4), so nothing that passed before fails
now.

```bash
git status --short
git diff --stat
```

```
 M src/experiments/metrics.py
?? docs/audit/week4-fix4-metrics.md
?? tests/experiments/test_metrics_hardening.py
 src/experiments/metrics.py | 158 ++++++++++++++++++++++++++++++++++-----------
 1 file changed, 120 insertions(+), 38 deletions(-)
```

### Step 2b: `True` vs `1` order dependence

While writing the uncertainty notes I checked the `True == 1` case directly
(scratch, against the Step 2 code):

```bash
.venv/bin/python -c "...mastery_report(a, at, [c(True), c(1)]).mastered, mastery_report(a, at, [c(1), c(True)]).mastered"
```

```
[True,1] 1  [1,True] 0
```

That is a defect-2 violation: the input order changed the result. Fix (one
`_outcome` change, nothing else):

```diff
     # Identical records collapse; any disagreement is a conflict, never a pick.
+    # type() keeps True and 1 apart, which compare equal.
     distinct = {
         (
             record["assessment_id"],
             record["assessment_version"],
+            type(record["passed"]),
             record["passed"],
             _timestamp(record["occurred_at"]),
         )
         for record in records
     }
     if len(distinct) > 1:
         return "conflicting"
-    assessment_id, assessment_version, passed, completed_at = distinct.pop()
+    assessment_id, assessment_version, _, passed, completed_at = distinct.pop()
```

Added test 10, `test_true_and_one_are_conflicting_in_either_order`.

```bash
.venv/bin/python -m pytest tests/experiments/test_metrics_hardening.py -v -p no:cacheprovider
```

(Filtered to PASSED/FAILED/summary lines.)

```
tests/experiments/test_metrics_hardening.py::test_completion_before_its_attempt_is_not_mastery PASSED [ 10%]
tests/experiments/test_metrics_hardening.py::test_conflicting_duplicate_completions_are_order_independent_and_not_mastery PASSED [ 20%]
tests/experiments/test_metrics_hardening.py::test_identical_duplicate_completions_collapse_to_one PASSED [ 30%]
tests/experiments/test_metrics_hardening.py::test_tied_first_attempts_use_smallest_attempt_id FAILED [ 40%]
tests/experiments/test_metrics_hardening.py::test_naive_and_offset_timestamps_are_normalized_to_utc PASSED [ 50%]
tests/experiments/test_metrics_hardening.py::test_immature_learner_is_not_counted_as_failure FAILED [ 60%]
tests/experiments/test_metrics_hardening.py::test_mastery_report_counts_immature_learner_separately FAILED [ 70%]
tests/experiments/test_metrics_hardening.py::test_exp_007_assignment_function_matches_implementation FAILED [ 80%]
tests/experiments/test_metrics_hardening.py::test_report_buckets_partition_assigned_learners PASSED [ 90%]
tests/experiments/test_metrics_hardening.py::test_true_and_one_are_conflicting_in_either_order PASSED [100%]
FAILED tests/experiments/test_metrics_hardening.py::test_tied_first_attempts_use_smallest_attempt_id
FAILED tests/experiments/test_metrics_hardening.py::test_immature_learner_is_not_counted_as_failure
FAILED tests/experiments/test_metrics_hardening.py::test_mastery_report_counts_immature_learner_separately
FAILED tests/experiments/test_metrics_hardening.py::test_exp_007_assignment_function_matches_implementation
========================= 4 failed, 6 passed in 0.07s ==========================
```

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider tests/experiments/test_metrics.py
.venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python -m pytest -q -p no:cacheprovider --ignore=tests/experiments/test_metrics_hardening.py
```

```
1 passed in 0.01s
4 failed, 178 passed, 4 skipped in 2.99s
172 passed, 4 skipped in 2.84s
```

The 4 failures are the same 4 allowed tests. 178 = 172 + 6 passing hardening
tests. Without the hardening file the suite is unchanged.

```bash
git status --short
git diff --stat
```

```
 M src/experiments/metrics.py
?? docs/audit/week4-fix4-metrics.md
?? tests/experiments/test_metrics_hardening.py
 src/experiments/metrics.py | 160 ++++++++++++++++++++++++++++++++++-----------
 1 file changed, 122 insertions(+), 38 deletions(-)
```

### Step 3: defects 3 and 4

```bash
.venv/bin/python -m pytest tests/experiments/test_metrics_hardening.py -v -p no:cacheprovider
```

(Filtered to PASSED/FAILED/`E`/summary lines.)

```
tests/experiments/test_metrics_hardening.py::test_completion_before_its_attempt_is_not_mastery PASSED [  3%]
tests/experiments/test_metrics_hardening.py::test_conflicting_duplicate_completions_are_order_independent_and_not_mastery PASSED [  6%]
tests/experiments/test_metrics_hardening.py::test_identical_duplicate_completions_collapse_to_one PASSED [ 10%]
tests/experiments/test_metrics_hardening.py::test_tied_first_attempts_use_smallest_attempt_id PASSED [ 13%]
tests/experiments/test_metrics_hardening.py::test_naive_and_offset_timestamps_are_normalized_to_utc PASSED [ 16%]
tests/experiments/test_metrics_hardening.py::test_immature_learner_is_not_counted_as_failure PASSED [ 20%]
tests/experiments/test_metrics_hardening.py::test_mastery_report_counts_immature_learner_separately PASSED [ 23%]
tests/experiments/test_metrics_hardening.py::test_exp_007_assignment_function_matches_implementation FAILED [ 26%]
tests/experiments/test_metrics_hardening.py::test_report_buckets_partition_assigned_learners PASSED [ 30%]
tests/experiments/test_metrics_hardening.py::test_true_and_one_are_conflicting_in_either_order PASSED [ 33%]
tests/experiments/test_metrics_hardening.py::test_as_of_exactly_at_window_close_is_mature PASSED [ 36%]
tests/experiments/test_metrics_hardening.py::test_as_of_one_second_before_window_close_is_immature PASSED [ 40%]
tests/experiments/test_metrics_hardening.py::test_attempt_after_as_of_is_ignored PASSED [ 43%]
tests/experiments/test_metrics_hardening.py::test_zero_mature_learners_gives_rate_none PASSED [ 46%]
tests/experiments/test_metrics_hardening.py::test_two_arms_evaluated_separately_are_independent PASSED [ 50%]
tests/experiments/test_metrics_hardening.py::test_every_input_permutation_gives_identical_report PASSED [ 53%]
tests/experiments/test_metrics_hardening.py::test_bucket_sum_invariant_holds_on_every_fixture[before_attempt] PASSED [ 56%]
tests/experiments/test_metrics_hardening.py::test_bucket_sum_invariant_holds_on_every_fixture[boundary_immature] PASSED [ 60%]
tests/experiments/test_metrics_hardening.py::test_bucket_sum_invariant_holds_on_every_fixture[boundary_mature] PASSED [ 63%]
tests/experiments/test_metrics_hardening.py::test_bucket_sum_invariant_holds_on_every_fixture[conflicting] PASSED [ 66%]
tests/experiments/test_metrics_hardening.py::test_bucket_sum_invariant_holds_on_every_fixture[empty] PASSED [ 70%]
tests/experiments/test_metrics_hardening.py::test_bucket_sum_invariant_holds_on_every_fixture[identical] PASSED [ 73%]
tests/experiments/test_metrics_hardening.py::test_bucket_sum_invariant_holds_on_every_fixture[maturity] PASSED [ 76%]
tests/experiments/test_metrics_hardening.py::test_bucket_sum_invariant_holds_on_every_fixture[partition] PASSED [ 80%]
tests/experiments/test_metrics_hardening.py::test_bucket_sum_invariant_holds_on_every_fixture[permutation] PASSED [ 83%]
tests/experiments/test_metrics_hardening.py::test_bucket_sum_invariant_holds_on_every_fixture[tie] PASSED [ 86%]
tests/experiments/test_metrics_hardening.py::test_bucket_sum_invariant_holds_on_every_fixture[timestamps] PASSED [ 90%]
tests/experiments/test_metrics_hardening.py::test_bucket_sum_invariant_holds_on_every_fixture[true_and_one] PASSED [ 93%]
tests/experiments/test_metrics_hardening.py::test_bucket_sum_invariant_holds_on_every_fixture[two_arms] PASSED [ 96%]
tests/experiments/test_metrics_hardening.py::test_bucket_sum_invariant_holds_on_every_fixture[zero_mature] PASSED [100%]
E           AssertionError: ('sha256', '  function: "H(subject_id || \'exp_007\') mod 100"')
E           assert 'sha256' in '  function: "H(subject_id || \'exp_007\') mod 100"'
FAILED tests/experiments/test_metrics_hardening.py::test_exp_007_assignment_function_matches_implementation
========================= 1 failed, 29 passed in 0.43s =========================
```

The tie-break test and both maturity tests from Step 1 now pass. **Not all
green:** the yaml test (defect 6) still fails because `experiments/exp_007.yaml`
is unchanged. This step's instruction was `metrics.py` only.

```bash
.venv/bin/python -m pytest tests/experiments/test_metrics.py -v -p no:cacheprovider
```

```
tests/experiments/test_metrics.py::test_first_eligible_checkpoint_mastery_uses_first_attempt_and_fourteen_day_window PASSED [100%]
============================== 1 passed in 0.01s ===============================
```

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python -m pytest -q -p no:cacheprovider --ignore=tests/experiments/test_metrics_hardening.py
```

```
FAILED tests/experiments/test_metrics_hardening.py::test_exp_007_assignment_function_matches_implementation
1 failed, 201 passed, 4 skipped in 3.31s
172 passed, 4 skipped in 2.86s
```

201 = 172 baseline + 29 passing hardening tests. Without the hardening file
the suite is unchanged (172 / 4).

```bash
grep -n "datetime.now\|utcnow\|today()" src/experiments/metrics.py
```

```
(no output, exit 1)
```

### Step 3: mutation check

A scratch script swapped one bad line at a time into
`src/experiments/metrics.py`, ran the hardening file (yaml test deselected),
then restored the original. It confirmed the restored file is byte-identical
(`restored: True`).

```bash
```

```
no as_of filter on attempts      -> 29 passed, 1 deselected in 0.39s
no as_of filter on completions   -> 29 passed, 1 deselected in 0.39s
tie-break by time only           -> 2 failed, 27 passed, 1 deselected in 0.10s
    test_tied_first_attempts_use_smallest_attempt_id
    test_every_input_permutation_gives_identical_report
maturity uses >=                 -> 1 failed, 28 passed, 1 deselected in 0.43s
    test_as_of_exactly_at_window_close_is_mature
denominator ignores immature     -> 9 failed, 20 passed, 1 deselected in 0.53s
    test_immature_learner_is_not_counted_as_failure
    test_mastery_report_counts_immature_learner_separately
    test_as_of_one_second_before_window_close_is_immature
    test_zero_mature_learners_gives_rate_none
    test_two_arms_evaluated_separately_are_independent
    test_bucket_sum_invariant_holds_on_every_fixture[boundary_immature]
    test_bucket_sum_invariant_holds_on_every_fixture[maturity]
    test_bucket_sum_invariant_holds_on_every_fixture[two_arms]
    test_bucket_sum_invariant_holds_on_every_fixture[zero_mature]
restored: True
```

- The tie-break, the maturity boundary (`>` vs `>=`) and the denominator are
  each pinned.
- Neither `as_of` filter was pinned at this point. That led to Step 3b.

### Step 3b: pin the completion cutoff

Writing up the result above, I found my first claim ("neither filter can
change a report") was wrong for completions. A mature learner with an
in-window pass and a conflicting row dated after `as_of` is `mastered` with
the filter and `conflicting` without it. I added
`test_completion_after_as_of_is_ignored` (test 13b). No `src/` change.

```bash
.venv/bin/python -m pytest tests/experiments/test_metrics_hardening.py -q -p no:cacheprovider
```

```
1 failed, 30 passed in 0.43s
```

(The 1 failure is the yaml test, as before.)

```bash
```

```
no as_of filter on attempts      -> 30 passed, 1 deselected in 0.40s
no as_of filter on completions   -> 1 failed, 29 passed, 1 deselected in 0.42s
    test_completion_after_as_of_is_ignored
tie-break by time only           -> 2 failed, 28 passed, 1 deselected in 0.10s
    test_tied_first_attempts_use_smallest_attempt_id
    test_every_input_permutation_gives_identical_report
maturity uses >=                 -> 1 failed, 29 passed, 1 deselected in 0.41s
    test_as_of_exactly_at_window_close_is_mature
denominator ignores immature     -> 9 failed, 21 passed, 1 deselected in 0.52s
    test_immature_learner_is_not_counted_as_failure
    test_mastery_report_counts_immature_learner_separately
    test_as_of_one_second_before_window_close_is_immature
    test_zero_mature_learners_gives_rate_none
    test_two_arms_evaluated_separately_are_independent
    test_bucket_sum_invariant_holds_on_every_fixture[boundary_immature]
    test_bucket_sum_invariant_holds_on_every_fixture[maturity]
    test_bucket_sum_invariant_holds_on_every_fixture[two_arms]
    test_bucket_sum_invariant_holds_on_every_fixture[zero_mature]
restored: True
```

Every mutation is now caught except removing the attempt filter, which can't
be observed under the maturity rule.

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider
```

```
1 failed, 202 passed, 4 skipped in 3.23s
```

(The failure is the yaml test. 202 = 172 + 30.)

```bash
git status --short
git diff --stat
```

```
 M src/experiments/metrics.py
?? docs/audit/week4-fix4-metrics.md
?? tests/experiments/test_metrics_hardening.py
 src/experiments/metrics.py | 198 ++++++++++++++++++++++++++++++++++++---------
 1 file changed, 160 insertions(+), 38 deletions(-)
```

### Step 4: defect 6

```bash
.venv/bin/python -m pytest tests/experiments/test_exp007_yaml_matches_code.py -v -p no:cacheprovider
```

```
tests/experiments/test_exp007_yaml_matches_code.py::test_yaml_keeps_hash_v2_and_drops_concatenation_formula PASSED [ 12%]
tests/experiments/test_exp007_yaml_matches_code.py::test_documented_rule_is_first_8_bytes_big_endian_mod_100_below_50 PASSED [ 25%]
tests/experiments/test_exp007_yaml_matches_code.py::test_documented_rule_matches_assign[L1] PASSED [ 37%]
tests/experiments/test_exp007_yaml_matches_code.py::test_documented_rule_matches_assign[learner-001] PASSED [ 50%]
tests/experiments/test_exp007_yaml_matches_code.py::test_documented_rule_matches_assign[learner-002] PASSED [ 62%]
tests/experiments/test_exp007_yaml_matches_code.py::test_documented_rule_matches_assign[learner-004] PASSED [ 75%]
tests/experiments/test_exp007_yaml_matches_code.py::test_documented_rule_matches_assign[synthetic-0] PASSED [ 87%]
tests/experiments/test_exp007_yaml_matches_code.py::test_samples_cover_both_variants PASSED [100%]
============================== 8 passed in 0.03s ===============================
```

## Final verification

### Test counts

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python -m pytest -q -p no:cacheprovider tests/experiments
.venv/bin/python -m pytest -q -p no:cacheprovider --ignore=tests/experiments/test_metrics_hardening.py --ignore=tests/experiments/test_exp007_yaml_matches_code.py
.venv/bin/python -m pytest -q -p no:cacheprovider -rs
```

```
211 passed, 4 skipped in 3.29s
48 passed, 2 skipped in 0.48s
172 passed, 4 skipped in 2.77s
SKIPPED [1] tests/abuse/test_point_farming.py:7: Abuse detection is outside this OSS spike.
SKIPPED [1] tests/experiments/test_assignment_vs_exposure.py:7: Experiment exposure is outside this OSS spike.
SKIPPED [1] tests/experiments/test_deterministic_assignment.py:71: Exposure logging is not implemented yet.
SKIPPED [1] tests/integration/test_full_state_rebuild.py:7: Multi-learner full-state orchestration is not implemented.
```

| Scope | Passed | Failed | Skipped |
|---|---|---|---|
| Full suite | **211** | **0** | **4** |
| `tests/experiments` | 48 | 0 | 2 |
| Without the two new files | 172 | 0 | 4 |

How the counts add up: 211 = 172 (suite at `534b3c0` without the new files)
+ 31 (`test_metrics_hardening.py`) + 8 (`test_exp007_yaml_matches_code.py`).
The 4 skips are pre-existing and none is in the metric code.

**About the stated baseline (145 passed, 9 skipped):** that was the count at
`97559e3`. Commit `534b3c0` (leaderboard/notifications, between Steps 1 and
2) changed it to 172 passed, 4 skipped, re-measured in Step 2 before any
`src/` edit and again here. Against 145 / 9 the numbers would not add up, so
172 / 4 is the real baseline. No previously passing test fails, and no test is
marked `xfail`.

### Git (read-only)

```bash
git status --short
git diff --stat
git diff --stat -- src/experiments/assignment.py tests/experiments/test_deterministic_assignment.py tests/experiments/test_metrics.py docs/metrics-framework.md
git stash list
git log --oneline -2
```

```
 M experiments/exp_007.yaml
 M src/experiments/metrics.py
?? docs/audit/week4-fix4-metrics.md
?? tests/experiments/test_exp007_yaml_matches_code.py
?? tests/experiments/test_metrics_hardening.py
---
 experiments/exp_007.yaml   |   2 +-
 src/experiments/metrics.py | 198 ++++++++++++++++++++++++++++++++++++---------
 2 files changed, 161 insertions(+), 39 deletions(-)
---
(empty: the protected files are unchanged)
(no stashes)
534b3c0 updates(m3,m5): updated the implementation of M3 and added the initial implementation for M5
97559e3 fix(m2): keep quest reader per assignment and agree with the aggregate
```

Changed or created by this fix, relative to `534b3c0`:

| File | Status |
|---|---|
| `src/experiments/metrics.py` | modified (+160 / −38) |
| `experiments/exp_007.yaml` | modified (1 line) |
| `tests/experiments/test_metrics_hardening.py` | new (503 lines, 31 tests) |
| `tests/experiments/test_exp007_yaml_matches_code.py` | new (68 lines, 8 tests) |
| `docs/audit/week4-fix4-metrics.md` | new |

Unrelated local changes: **none.** `534b3c0` is a commit, not a local change.
`.pytest_cache/` is git-ignored. Scratch scripts and snapshots are in the
session scratchpad outside the repo. Nothing was committed, staged or
stashed.

### Suggested commit (not made)

Files:

```
src/experiments/metrics.py
experiments/exp_007.yaml
tests/experiments/test_metrics_hardening.py
tests/experiments/test_exp007_yaml_matches_code.py
docs/metrics-framework.md
docs/audit/week4-fix4-metrics.md
```

Message (no trailer; updated after "Doc fix"):

```
fix(m7): harden exp_007 primary metric and document the real assignment hash

first_eligible_checkpoint_mastery counted a passing completion dated
before its attempt, resolved conflicting duplicate completions
last-write-wins, picked equal-time first attempts by arrival order,
counted learners whose 14-day window had not closed as failures, and
raised TypeError when naive and aware timestamps met.

Add mastery_report(assignments, attempts, completions, as_of=None)
returning a frozen MasteryReport with one bucket per assigned learner:
mastered, failed, missing_outcome, invalid_ordering, conflicting or
immature. Timestamps are normalized to UTC (naive means UTC). The
first attempt is the minimum (occurred_at, attempt_id) in the window;
completions for it collapse when identical and are conflicting
otherwise, in any order; a completion before its attempt is invalid.
With as_of, later data is ignored and learners whose window has not
closed are immature and leave the denominator; rate is None when no
learner is mature. first_eligible_checkpoint_mastery is now a float
wrapper (0.0 for None, documented as ambiguous).

experiments/exp_007.yaml now describes what assign() does: sha256 of
the compact UTF-8 JSON array [subject_id, exp_id], first 8 bytes
big-endian, mod 100, bucket < 50 -> variant_a. The exp_007 example in
docs/metrics-framework.md uses the same wording and renames the
primary metric to first eligible checkpoint mastery. Assignment code
and hash behaviour are unchanged.

Add tests/experiments/test_metrics_hardening.py (31 tests) and
tests/experiments/test_exp007_yaml_matches_code.py (8 tests). Details
in docs/audit/week4-fix4-metrics.md.

Not covered: yaml eligibility exclusions are not applied by the metric.
```

## Assumptions

- **14-day window stays inclusive at both ends**, as in the current code
  (`assignment_at <= t <= assignment_at + 14 days`). Test 5 puts the attempt
  exactly on the assignment instant and the completion exactly on +14 days and
  expects both to count.
- **Defect 1 rule (implemented in Step 2):** a completion must satisfy
  `completion.occurred_at >= attempt.occurred_at`. Equal instants count as
  valid ordering. Test 1 only covers "strictly before" (Jan 3 < Jan 5), so
  equality is not pinned.
- **Defect 2 rule (implemented in Step 2, as the task defined it):** rows for
  one `attempt_id` collapse when they're identical in `(assessment_id,
  assessment_version, passed, occurred_at)`, with `occurred_at` compared as a
  UTC instant. Any difference in those fields is `conflicting`. Test 2's rows
  differ only in `passed`. Rows that differ only in `occurred_at`, or only in
  assessment id/version, are conflicting by the rule but not pinned by a test.
- **Late completion bucket (Step 2 choice):** a single matching completion
  after `assignment + 14 d` counts as `missing_outcome`, not `failed`. Pinned
  by L7 in test 9.
- **Mismatched completion bucket:** a single completion whose assessment
  id/version differs from the attempt counts as `missing_outcome`. Not pinned
  by a hardening test; the existing 10-learner fixture exercises it (L5, L6),
  but only through the rate.
- **Defect 3 tie key (implemented in Step 3):** `(occurred_at as UTC,
  attempt_id)`, smallest first, using plain comparison on `attempt_id` (so
  `"A10" < "A2"`). Mixed `attempt_id` types (e.g. `int` and `str`) at the
  same instant would raise `TypeError`. The event contract uses strings.
- **Defect 4 maturity (implemented in Step 3):** a learner is mature when
  `assignment_at + 14 days <= as_of`. The boundary is pinned by tests 11
  (equal → mature) and 12 (one second earlier → immature). `as_of` accepts an
  ISO string or a `datetime`; a naive `datetime` is UTC. `as_of=None` keeps
  the old behaviour (every assigned learner is in the denominator).
- **Defect 5:** naive means UTC. That is the same convention as
  `src/state_machine/learner.py:_as_datetime`.
- **Defect 6 target text:** the test asserts fragments (`sha256`,
  `json.dumps([subject_id, exp_id]`, `[:8]`, `big`, `mod 100`, no `||`,
  plus `bucket < 50` and `variant_a` somewhere in the file), not one exact
  string. That leaves the yaml wording open. `pyyaml` is a dev dependency in
  `pyproject.toml` but is **not installed** in `.venv`, so the test reads the
  file as text instead of parsing YAML.
- **Report counts:** `assigned = mastered + failed + missing_outcome +
  invalid_ordering + conflicting + immature`. Asserted by test 9 (Step 2) for
  a fixture that reaches every bucket except `immature`.

## Uncertainty

- **Category definitions (resolved in Step 2).**
  - No eligible attempt → `missing_outcome`.
  - An attempt with no completion → `missing_outcome`.
  - Invalid-ordering and conflicting learners stay in the denominator.

  These follow the task's bucket definitions and are pinned by test 9. Still
  my choice and only pinned by L7: a late completion counts as
  `missing_outcome` (see "Assumptions").
- **Empty population (resolved in Step 3).** `mastery_report(...).rate` is
  `None` when the denominator is 0 (nobody assigned, or nobody mature), and
  the float wrapper returns `0.0`. Its docstring now calls that `0.0`
  ambiguous and names `mastery_report` as the reviewable interface. Pinned by
  test 14.
- **The attempt `as_of` filter is not pinned and can't be.** Under the
  maturity rule, an attempt after `as_of` either belongs to an immature
  learner (bucketed before attempts are used) or falls after a mature
  learner's window (dropped by the window check). Test 13 confirms the report
  doesn't change, but the mutation check shows removing the filter breaks
  nothing. Kept because the task asked for it and it makes the "not yet
  observable" rule explicit. The **completion** filter is pinned by test 13b
  (Step 3b).
- **Immature learners' data is not validated.** An immature learner is
  bucketed before their attempts/completions are examined. Malformed
  completion timestamps for them are never parsed, while malformed attempt
  timestamps still raise (attempts are parsed while scanning).
- **`mastery_report` type (resolved in Step 2):** a frozen dataclass,
  `MasteryReport`, with the 9 planned fields.
- **Eager parsing of assignment timestamps (Step 2 behaviour change).** A
  malformed assignment string for a learner with no attempts now raises; before,
  it was ignored. No test or caller passes such data.
- **`True` vs `1` in `passed` (found and fixed in Step 2b).** With the
  first Step 2 key, `True == 1` made the rows collapse, and the set kept
  whichever arrived first. So the result depended on input order:
  `[True, 1]` → mastered and `[1, True]` → not mastered. The key now includes
  `type(passed)`, so the two rows conflict in either order. Pinned by test 10.
  Other "equal but not identical" values (e.g. `1` vs `1.0` for
  `assessment_version`) still collapse, because only `passed` carries its
  type. Not tested.
- **Conflicting duplicate attempt records are not handled.** Attempts are
  not deduplicated or checked for conflicts by `attempt_id`. Probed on the
  final code (scratch):
  - the same `attempt_id` under two learners, with one passing completion:
    both learners count as mastered (`2 mastered of 2`), because completions
    are looked up only by `attempt_id`;
  - the same `attempt_id` twice for one learner at Jan 2 and Jan 4, with a
    completion on Jan 3: the Jan 2 record wins as the first attempt, so the
    completion is valid and the learner is mastered. Against the Jan 4 record
    it would have been invalid ordering. Neither case is flagged as a conflict.
- **The yaml's eligibility exclusions are not applied.** `platform_tenure < 3
  days` and the push × streak_risk consent opt-out are not checked by the
  metric. It treats every key of `assignments` as an eligible assigned
  learner, so the caller must filter before calling. Nothing in the repo does
  that today.
- **0.0 vs None in the float wrapper.** `first_eligible_checkpoint_mastery`
  returns `0.0` both when nobody mastered and when there is no mature learner
  (or nobody is assigned). Its docstring says so. Use `mastery_report`, whose
  `rate` is `None` in the second case, for anything that gets reviewed. The
  10-learner test still uses the wrapper.
- **Per-arm reporting means calling the report once per arm.** The metric
  has no notion of variants. A per-arm result means calling `mastery_report`
  separately with each arm's assignments (shared attempts/completions are
  fine: test 15 shows the arms don't affect each other, and the counts add
  up). Nothing in the repo does this split yet. SRM, guardrails and exposure
  vs assignment are out of scope.
- **Test 3 (Step 1 note, now moot).** At Step 1 it failed only because
  `mastery_report` was missing. If the report were added without conflict
  tracking, the rate half would never have been red. The order-dependence
  part of defect 2 is covered by test 2.
- **Equal-time duplicate completions with different `occurred_at`**, and
  version/assessment mismatches combined with duplicates, are not covered.
  The existing 10-learner test covers assessment/version mismatches on single
  completions.

## Doc fix: `docs/metrics-framework.md` exp_007 example

Only the exp_007 "Implementation Example" section changed: lines 48 and 49
(`git diff --stat`: `docs/metrics-framework.md | 4 +-`). No code, test or
other section was touched.

Before:

```markdown
* **Assignment & Exclusions:** 50/50 split via `H(subject_id || "exp_007") mod 100`. Excludes learners with platform tenure < 3 days or those who opted out of the push × streak_risk consent pair.
* **Primary Metric (Mprimary):** First-Attempt Mastery Accuracy over a 14-day window (with Remediation Conversion Rate as the short-horizon operational signal).
```

After:

```markdown
* **Assignment & Exclusions:** 50/50 split via `bucket = int.from_bytes(sha256(json.dumps([subject_id, exp_id], separators=(',', ':')).encode('utf-8')).digest()[:8], 'big') mod 100`, i.e. the first 8 bytes of the SHA-256 of the compact UTF-8 JSON array [subject_id, exp_id], read as a big-endian unsigned integer, mod 100; bucket < 50 → variant_a (propensity 0.5), otherwise variant_b. The JSON array deliberately avoids plain concatenation, which collides: ('ab', 'c') and ('a', 'bc') would both hash 'abc'. Excludes learners with platform tenure < 3 days or those who opted out of the push × streak_risk consent pair.
* **Primary Metric (Mprimary):** First eligible checkpoint mastery over a 14-day window (with Remediation Conversion Rate as the short-horizon operational signal). The 14-day window is measured from assignment; learners whose window has not closed are reported as immature and left out of the denominator, and the missing-outcome rate is reported separately.
```

- The assignment text uses the same wording as `experiments/exp_007.yaml`
  line 12, including the concatenation note. The yaml's `->` is written `→`,
  matching the doc's prose style.
- The exclusions sentence is unchanged.
- **Still old elsewhere in the same file (other sections, left alone as
  instructed):**
  - line 9 defines "First-Attempt Mastery Accuracy" as the average accuracy
    over attempts with sequence 1;
  - line 37 gives the general rule `A(subject_id, exp_id) = H(subject_id ||
    exp_id) mod 100`.

  Both now disagree with the exp_007 example and the code.

Also updated in this audit file so it doesn't contradict the doc fix: the
status line, the `docs/metrics-framework.md` row of "Files I will touch",
the "Not touched" line under "Summary", and the suggested commit (file list,
body, "Not covered" line). The Uncertainty note about the stale doc was
removed.

```bash
diff <snapshot of docs/metrics-framework.md> docs/metrics-framework.md
```

```
48,49c48,49
< * **Assignment & Exclusions:** 50/50 split via `H(subject_id || "exp_007") mod 100`. ...
< * **Primary Metric (Mprimary):** First-Attempt Mastery Accuracy over a 14-day window ...
---
> * **Assignment & Exclusions:** 50/50 split via `bucket = int.from_bytes(sha256(json.dumps([subject_id, exp_id], ...
> * **Primary Metric (Mprimary):** First eligible checkpoint mastery over a 14-day window ...
```

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider
```

```
211 passed, 4 skipped in 3.26s
```

Same as "Final verification": no test reads this doc.

```bash
git status --short
git diff --stat
```

```
 M docs/metrics-framework.md
 M experiments/exp_007.yaml
 M src/experiments/metrics.py
?? docs/audit/week4-fix4-metrics.md
?? tests/experiments/test_exp007_yaml_matches_code.py
?? tests/experiments/test_metrics_hardening.py
 docs/metrics-framework.md  |   4 +-
 experiments/exp_007.yaml   |   2 +-
 src/experiments/metrics.py | 198 ++++++++++++++++++++++++++++++++++++---------
 3 files changed, 163 insertions(+), 41 deletions(-)
```

- Follow-up: line 9 now defines "First eligible checkpoint mastery" (share of assigned eligible learners passing their first eligible checkpoint within 14 days of assignment; missing-outcome rate reported separately), replacing the old "First-Attempt Mastery Accuracy" definition. This supersedes the "still old elsewhere" note above.
- Follow-up: lines 37–38 (general assignment rule) now use the yaml wording: SHA-256 of the compact UTF-8 JSON array [subject_id, exp_id], first 8 bytes big-endian, mod 100, versioned as hash_v2. Line 38 ("where H is … MurmurHash3") was replaced too, because it described the removed `H`. Full suite after the change: 211 passed, 4 skipped.
