# Week 4 pilot 0: `as_of` required in exp_007 metrics

## Changes
- `src/experiments/metrics.py`
  - `mastery_report` and `first_eligible_checkpoint_mastery`: `as_of` has no default (`datetime | str`).
  - `mastery_report` raises `ValueError("as_of is required: declare the data cutoff")` when `as_of is None`;
    `first_eligible_checkpoint_mastery` inherits this because it delegates.
  - Docstrings updated; the sentence "as_of=None treats every learner as mature" is removed.
- `tests/experiments/test_metrics.py`: 1 call site (the hand-checked 10-learner fixture) now passes
  `as_of="2100-01-01T00:00:00Z"`. Assertion unchanged (still `== 0.2`).
- `tests/experiments/test_metrics_hardening.py`
  - 15 call sites that omitted `as_of` now pass `as_of="2100-01-01T00:00:00Z"` (lines 48, 58, 59, 75, 76, 90, 91,
    117, 195, 196, 218, 219, 308, 328, 400) plus the permutation loop call (~line 412).
  - 9 entries of the `FIXTURES` table carried `None` as the as_of element, which the shared test passed to
    `mastery_report(..., as_of=as_of)`. They now carry `"2100-01-01T00:00:00Z"`. No assertion changed.
  - New test `test_as_of_is_required_and_none_is_rejected`.
- No callers in `src/` outside `metrics.py` (grep below).

## Before / after
- Before: `mastery_report(a, t, c)` and `as_of=None` both treated every learner as mature.
- After: omitting `as_of` -> `TypeError`; `as_of=None` -> `ValueError("as_of is required: declare the data cutoff")`.

## Commands and output
    grep -rnE "mastery_report|first_eligible_checkpoint_mastery" src tests --include='*.py'
    -> src: only metrics.py; tests: test_metrics.py (1 call), test_metrics_hardening.py (many)
    .venv/bin/python -m pytest tests/experiments -q
    -> 49 passed, 2 skipped in 0.30s
    .venv/bin/python -m pytest tests/experiments/test_metrics.py -q -v
    -> 1 passed (10-learner fixture == 0.2)
(A first run before fixing the FIXTURES table gave 9 failed, 40 passed: the None entries above.)

## Assumptions
- `"2100-01-01T00:00:00Z"` reproduces the old None behaviour for every fixture (all windows closed, nothing after cutoff).
- FIXTURES `None` entries count as call sites that "omit" as_of, so they were updated.
- The 2 skipped tests are pre-existing and unrelated (not investigated).

## Uncertainty
- `test_completion_after_as_of_is_ignored` has a comment "Without a cutoff ..." for the `everything` call; it now uses
  the far-future cutoff, comment left unchanged by instruction (assertions untouched).
- No git commands that change state were run; only read-only `git diff`/`git status`.
