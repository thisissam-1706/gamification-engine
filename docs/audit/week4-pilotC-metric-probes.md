# Week 4 pilot C: metric probes from the review

## Changes
- New `tests/experiments/test_metrics_review_probes.py` (6 tests, public `first_eligible_checkpoint_mastery`,
  and `mastery_report` where noted, `as_of="2100-01-01T00:00:00Z"`).
- `src/experiments/metrics.py` (`mastery_report`):
  - While scanning attempts, record every observable (on/before cutoff) record per `attempt_id` as the tuple
    (learner_id, assessment_id, assessment_version, normalized occurred_at).
  - A learner whose chosen `attempt_id` has more than one distinct record is classified "conflicting".
  - A completion carrying a `learner_id` different from the attempt's learner makes the learner "conflicting";
    completions without `learner_id` behave as before.
  - `MasteryReport` fields and all existing assertions unchanged.
  (The file also still contains the earlier, separate `as_of`-required change from pilot 0.)

## Before / after
- Before: reused attempt_ids with different content and cross-learner completions were silently accepted
  (first attempt used; foreign completion matched by attempt_id).
- After: both count as `conflicting`, independent of input order.

## Commands and output
    .venv/bin/python -m pytest tests/experiments/test_metrics_review_probes.py -q   (before the code change)
    -> 2 failed, 4 passed  (the two expected: duplicate attempt ids; foreign-learner completion)
    .venv/bin/python -m pytest tests/experiments -q   (after)
    -> 55 passed, 2 skipped in 0.36s   (10-learner fixture still 0.2)
    .venv/bin/python -m pytest tests -q   (whole suite)
    -> 404 passed, 4 skipped, 1 deselected in 9.92s

## Assumptions
- Attempt records after the cutoff are not observable, so they do not create a conflict.
- Records are tracked for every attempt row, including unassigned learners and attempts outside the 14-day window,
  so an id reused across learners also conflicts.
- Equal instants in different timestamp spellings count as identical content (normalized before comparing).
- Only the chosen first attempt's id is checked, per the stated rule.

## Uncertainty
- Window-scoped conflicts: a duplicate id sitting outside the window still marks the learner conflicting.
- No git commands that change state were run.
