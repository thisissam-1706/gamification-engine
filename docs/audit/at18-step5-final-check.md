# AT-18 step 5 of 5: final check (verify only)

Nothing was changed in this step except creating this file. No state-changing git commands were run.

## 1. Whole suite
```
$ python -m pytest -q
FAILED tests/simulator/test_pilot_report.py::test_null_run_control_untouched_and_analyze_is_pure
E   assert 4 == 3   (len(first["meta"]["notes"]) == 3, tests/simulator/test_pilot_report.py:139)
1 failed, 827 passed, 1 deselected in 98.18s (0:01:38)
```
- passed 827, failed **1**, skipped 0 (none reported), deselected 1 (the `integration` marker, from `addopts = "-m 'not integration'"` in `pyproject.toml`).
- Comparison with the AT-13 final check (812 passed, 0 skipped): +15 passed. Of those, 16 are the new `test_policy_comparison.py` items; so 1 previously passing test is now the failure (812 + 16 - 1 = 827). Skipped is 0 in both.
- **This check FAILS.** The failing test is the step-2 leftover: adding the requested `messages_per_learner_per_day` meta note made the notes list 4 long, and that test asserts 3. It is an old assertion, not a regression in behaviour. It is still unfixed pending your decision (change `3` to `4`, or drop the note). Per the brief I am describing it and stopping.

## 2. Separate runs
```
$ python -m pytest tests/simulator -q       -> 1 failed, 66 passed in 8.85s   (same single failure)
$ python -m pytest tests/experiments -q     -> 82 passed in 1.17s
$ python -m pytest tests/simulator/test_policy_comparison.py -q -> 16 passed in 7.21s
```
`test_policy_comparison.py` takes **7.21 s** (7.61 s earlier).

## 3. run_pilot into a temp directory outside the repo
```
$ python -m simulator.run_pilot --setting positive --seed 7 --n 500 --out /tmp/at18-pilot-VDyj
exit=0
```
`--out` is a parent directory: the files went to `/tmp/at18-pilot-VDyj/pilot_positive_seed7_n500/` (assignments, attempts, completions, decisions, deliveries, eligibility, exposures, ledger .jsonl, and `report.json`). `report.json` has `messages_per_learner_per_day` in both arms:
- treatment: 0.21074646074646075 (655 messages sent, 222 eligible; 655 / (222 x 14) matches)
- control: 0.0 (0 messages, 190 eligible)

`docs/evidence/` untouched (`git status --short docs/evidence` printed nothing). (My first read of `report.json` looked one directory too high and failed; the values above are from the right path.)

## 4. grep for "AT-18" and "to be written" in docs/ and README.md
Current docs (not audit files):
- `docs/traceability-matrix.md:3` (2026-11-07 changelog), `:44` (M7 row, "AT-18"), `:97` (2026-11-07 clarification), `README.md:3`, `:153`, `:172`: all current, not stale.
- `docs/traceability-matrix.md:5` (the **2026-11-06** changelog) still ends "AT-18 is still to be written." This is a dated historical entry, true on that date and superseded by the 2026-11-07 entry above it. Not stale in the sense of being wrong, so left alone; say if you want a "(since written, see 2026-11-07)" tweak.
- The "to be written" in the matrix M7 row and the AT-18 heading: none remain.

Audit files (history, not stale): `at13-step7-docs.md`, `week6-m4-step5-*.md`, `week6-m4-final-check.md` (records M7's "to be written" as true at the time), and the five at18 audit files describing the before state. No other "to be written" hits in `docs/` or README.

## 5. git status (read-only)
```
 M README.md
 M docs/metrics-framework.md
 M docs/traceability-matrix.md
 M simulator/pilot_report.py
?? docs/audit/at18-step1-tests.md
?? docs/audit/at18-step2-fatigue-metric.md
?? docs/audit/at18-step3-comparison-behaviour.md
?? docs/audit/at18-step4-docs.md
?? tests/simulator/test_policy_comparison.py
```
Matches your expected list. Not present: `simulator/SPEC.md` (unchanged), any changed existing test file. `docs/audit/at18-step5-final-check.md` (this file) is the fifth audit file. Nothing unexpected.

## Assumptions
- "skipped" is 0 because pytest's summary line lists no skips.
- Temp pilot output directory `/tmp/at18-pilot-VDyj` left in place (outside the repo).

## Unsure about
- Why the full suite took 98 s this time (70 s in step 3, 27 s in step 2); I did not investigate (machine load is the likely cause).
- The single failing test, as above: it needs your decision before AT-18 can be called green on the full suite.
