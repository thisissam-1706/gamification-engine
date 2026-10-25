# Week 4 pilot 4: file-writing runner

## Changes
- New `simulator/run_pilot.py`: `run_single`, `run_many`, `main` (argparse; `--setting` required with choices
  null/positive/harmful/cap_stress, `--seed` 7, `--n` 4000, `--out` docs/evidence/pilot, `--seeds "a-b"`).
- New `tests/simulator/test_run_pilot.py` (7 tests, n=200, tmp_path).
- New empty `simulator/__init__.py` (did not exist; needed for `python -m simulator.run_pilot`).
  Note: this turns `simulator` from a namespace package into a regular package.

## Before / after
- Before: `ModuleNotFoundError: No module named 'simulator.run_pilot'`.
- After: each run writes 7 JSONL streams + report.json (sort_keys, "\n", UTF-8, no timestamps or paths);
  `run_many` writes `summary_<setting>_n<n>_seeds<first>-<last>.json`.

## Commands and output
    .venv/bin/python -m pytest tests/simulator/test_run_pilot.py -q   (tests first)
    -> 1 error in 0.09s  (ModuleNotFoundError: simulator.run_pilot)
    .venv/bin/python -m pytest tests/simulator -q
    -> 32 passed in 0.67s
    .venv/bin/python -m pytest tests -q   (whole suite, sanity)
    -> 396 passed, 4 skipped, 1 deselected in 9.90s
    .venv/bin/python -m simulator.run_pilot --setting null --n 200 --out <scratchpad>/out
    -> exit 0, directory pilot_null_seed7_n200 created (written outside the repo)

## Assumptions
- Population/draw seed equals the run seed.
- `--seeds` accepts "a-b" (inclusive) or a single integer; summary file name uses first and last seed in the list.
- Summary means and totals use only non-skipped seeds (difference not None); rates that are None are dropped from means.
- Summary also includes `setting` and `n` fields (extra to the spec).
- I added one extra test (`main` with `--seeds`).

## Uncertainty
- 1 deselected test in the full suite is pre-existing config, not investigated.
- Byte-identity was tested for setting "positive" at n=200 only.
- No git commands that change state were run.
