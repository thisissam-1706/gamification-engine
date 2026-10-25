# Week 4 pilot 2: simulated exp_007 event streams

## Changes
- New `simulator/pilot.py`: constants, `SETTINGS`, frozen `PilotRun`, `simulate(population, draws, setting, seed)`
  implementing assignment, eligibility, decision, delivery, exposure and outcome as specified. Module comment lists
  the limits (no exposure-before-attempt ordering check; eligibility/decision/delivery are stand-ins for M4/M5).
- New `tests/simulator/test_pilot_streams.py` (9 tests, n=600, seed=7).
- No other file edited.

## Before / after
- Before: no `simulator/pilot.py`; collection error `ModuleNotFoundError: No module named 'simulator.pilot'`.
- After: `simulate` produces deterministic streams, one record set per learner, sorted by learner_id then time.

## Commands and output
    .venv/bin/python -m pytest tests/simulator/test_pilot_streams.py -q   (tests first)
    -> 1 error in 0.09s  (ModuleNotFoundError: simulator.pilot)
    .venv/bin/python -m pytest tests/simulator -q   (after implementation)
    -> 18 passed in 0.38s   (9 pilot-1 tests + 9 pilot-2 tests)

## Assumptions
- Rolling 24h cap window is (scheduled - 24h, scheduled), i.e. a message exactly 24h earlier is not counted.
  Tests use the same window (t-24h, t].
- Only delivered/provider_failed messages count towards the cap.
- `reason` in eligibility is None for eligible learners; `policy` is None for no_action decisions
  (spec did not define them).
- `setting` dict gets setting_name "custom".
- A completion is only emitted when the attempt exists and completion_logged < 0.95; `passed` uses the
  "passed" draw regardless of logging.
- Streams are sorted by learner_id because learners are processed in sorted order; within a learner, time
  order follows message index / single attempt.

## Uncertainty
- An opted-out-after-delivery message still counts as exposure source if it was the first delivered.
- Exposure uses first delivered message regardless of attempt time (documented limit).
- Cap-stress exceeding the cap relies on the default draws at n=600/seed=7; test passed.
- No git commands that change state were run.
