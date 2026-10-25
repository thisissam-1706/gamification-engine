# Week 4 pilot B: outcome buckets per arm

## Changes
- `simulator/pilot_report.py`: new `_buckets(...)`; each arm now has a `buckets` dict:
  mature_with_outcome (mastered + failed), mature_no_outcome (= missing_outcome), missing_no_attempt,
  missing_attempt_no_completion, mature_invalid (invalid_ordering + conflicting), immature,
  headline_denominator, headline_rate. `meta.notes` gains a third note defining the headline rate.
- `tests/simulator/test_pilot_report.py`: 2 new tests (hand-built 5-learner fixture; real run n=300 seed 7 "null").
  One existing assertion edited: `len(meta.notes) == 2` -> `== 3`, forced by the added note.

## Before / after
- Before: arms had counts only inside `mastery`; no split of missing outcomes.
- After: top-level buckets sum to eligible; no_attempt + attempt_no_completion == mature_no_outcome;
  headline_rate == mastery.rate, headline_denominator == mastery.denominator.

## Commands and output
    .venv/bin/python -m pytest tests/simulator/test_pilot_report.py -q   (tests first)
    -> 2 failed, 7 passed   (KeyError: 'buckets')
    .venv/bin/python -m pytest tests/simulator -q   (after implementation)
    -> 34 passed in 0.73s

## Assumptions
- Mature iff assigned_at + 14 days <= DATA_CUTOFF_ISO (same boundary as mastery_report).
- missing_no_attempt = mature eligible learners with no attempt record at or before the cutoff; the remainder of
  missing_outcome is attributed to attempt_no_completion. So an attempt outside the learner's window, or a completion
  that is present but not matched, would also land in the second bucket (does not occur in simulated runs).
- Headline denominator is computed from the bucket sum, and tests assert it equals the mastery denominator.

## Uncertainty
- The notes-length assertion change is the only edit to an existing assertion.
- No git commands that change state were run.
