# Week 4 pilot E: per-learner ledger

## Changes
- `simulator/pilot_report.py`: `LEDGER_BUCKETS`, `_classify(...)`, `build_ledger(run)` (one row per assigned learner,
  sorted by learner_id, 19 keys). Eligible learners are bucketed by calling `mastery_report` on that single learner
  with `as_of=DATA_CUTOFF_ISO`; `missing_outcome` is split by whether the learner has an attempt record.
- `simulator/run_pilot.py`: `run_single` writes `ledger.jsonl` (same JSON rules) and adds `ledger_summary`
  (bucket counts per variant, all 8 buckets, zeros included) to report.json (and to the returned dict).
- `tests/simulator/test_run_pilot.py`: 4 new tests. One existing assertion edited: the expected file set in
  `test_run_single_writes_all_files_with_one_line_per_learner` now includes `ledger.jsonl` (forced by the new file).

## Before / after
- Before: no ledger; run directory had 7 streams + report.json.
- After: also `ledger.jsonl`; report.json has `ledger_summary`.

## Commands and output
    .venv/bin/python -m pytest tests/simulator/test_run_pilot.py -q   (tests first)
    -> 5 failed, 6 passed  (missing ledger.jsonl / file set)
    .venv/bin/python -m pytest tests/simulator -q   (after)
    -> 45 passed in 1.29s
    .venv/bin/python -m simulator.run_pilot --setting positive --n 200 --out <scratchpad>/ledger
    -> exit 0 (written outside the repo); first 3 eligible rows of each arm were printed in the session.

## Assumptions
- Ledger `attempted`/`attempt_at` use the learner's earliest attempt record (any time); `completed`/`passed` use the
  first completion record. Excluded learners keep their real attempt facts (the simulator generates outcomes for all
  assigned learners) but get bucket "excluded".
- `exposure_before_attempt` is None unless the learner is exposed and has an attempt; otherwise strict exposed_at < attempt_at.
- Immature learners can still show attempts/completions in the ledger; their bucket is "immature".
- Message counts are 0 (not None) for learners with no deliveries.

## Uncertainty
- `_classify` asserts the residual case is missing_outcome; a future MasteryReport change would trip it.
- Cost: one mastery_report call per eligible learner (fine at n=4000, not measured).
- No git commands that change state were run.
