# Week 4 fix 13: lock file and CI evidence

## Step G2a

**Command.** `.venv/bin/python -m pip freeze --exclude-editable`, written to `requirements-lock.txt` under a header with the Python version, the date, the command and the install line. `pyproject.toml` is unchanged.

**Python version.** 3.14.6

**Contents.** 14 pinned packages: attrs, eventsourcing, iniconfig, jsonschema, jsonschema-specifications, packaging, pluggy, Pygments, pytest, referencing, rpds-py, setuptools, typing_extensions, wheel. The working venv does not have the other dev extras (`pytest-asyncio`, `pyyaml`, `build`), so they are not in the lock.

**Verification.** Created a fresh venv in a temp folder, ran `pip install -r requirements-lock.txt`, then `pip install -e . --no-deps`, then `pytest -q --ignore=tests/leaderboard --ignore=tests/notifications`:

```
262 passed, 4 skipped, 1 deselected in 4.13s
```

This matches the Step G1c default run.

**README.** The "How to Run Locally" section has one added sentence with the install command from the lock header.

## Step G2b

**Workflow.** Added `.github/workflows/ci.yml`. It runs on `push` and `pull_request`, with one job on `ubuntu-latest` and a Python matrix of 3.11 and 3.12 (`actions/checkout@v4`, `actions/setup-python@v5`). The steps are `pip install -r requirements-lock.txt`, `pip install -e . --no-deps`, and `timeout 900 python -m pytest -q`. There is no caching, linting or other job.

**YAML check.** `yaml.safe_load` parses the file: triggers `[push, pull_request]`, matrix `['3.11', '3.12']`, 5 steps. PyYAML is not installed in the project venv, so I ran the check in a throwaway venv outside the repo. The workflow has not been run on GitHub.

**Open risk.** `requirements-lock.txt` was produced on Python 3.14.6 only. The 3.11 and 3.12 jobs install pins that were never resolved or tested on those versions, so a pin may be unavailable or incompatible there (for example `rpds-py==2026.6.3` or `typing_extensions==4.16.0`), and the 3.11 and 3.12 jobs may need a different lock. The matrix was left unchanged as instructed. The first CI run will show whether the risk is real.

## Step G2c

**Script.** Added `scripts/capture_evidence.sh` (bash, `set -euo pipefail`, executable). It runs from the repo root and refuses with a clear message and exit code 1 if `git status --porcelain` is not empty. Otherwise it creates `docs/evidence/<short-sha>/` and writes:

- `commit.txt`: `git rev-parse HEAD`, `git log -1 --oneline`, and the UTC date.
- `env.txt`: `python --version` and `python -m pip freeze`.
- `pytest_full.txt`: the full output of `python -m pytest -q`, including the summary line.

The script uses git only for reads (`status`, `rev-parse`, `log`). It prints the summary line and exits with pytest's exit code, and the output file is written either way.

**Test.** The script was not run for real. I copied the repo (without `.venv`) to a scratch folder, added an untracked `untracked_probe.txt`, and ran it. It printed the "working tree is not clean" error plus the status list, exited with code 1, and created no `docs/evidence/<short-sha>/` folder (`docs/evidence/` already holds the earlier `clean_install_*.txt` files). The copy was already dirty from the uncommitted work of Steps G1a to G2b, so the probe file was not the only trigger. This confirms the refusal path only. The clean-tree path (writing the three files) has not been exercised and should be checked after you commit. `bash -n` reports no syntax errors. The scratch copy was deleted.

**README.** Added a "Reproducing the evidence" section with bullets for `requirements-lock.txt`, `scripts/capture_evidence.sh` (clean committed tree only) and `.github/workflows/ci.yml`.
