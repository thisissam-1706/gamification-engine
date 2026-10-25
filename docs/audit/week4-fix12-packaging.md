# Week 4 fix 12: packaging

## Step G1a

**What moved.** The root `schemas/` directory now lives at `src/schemas/`, with the same layout: five top-level schema files plus `events/` (18 files). File contents are unchanged. The move was done with a plain filesystem move, with no git commands. Both schema loaders now read from the package with `importlib.resources.files("src")`.

**Files changed.**

| File | Change |
|---|---|
| `src/ingestion/service.py` | `_envelope_validator` reads `envelope.schema.json` via `files("src")`. Swapped the now-unused `Path` import for `files`. |
| `src/ingestion/validation.py` | `lesson_completed_validator` reads `events/lesson_completed.schema.json` via `files("src")`. Swapped the now-unused `Path` import for `files`. |
| `CONTRIBUTING.md:21` | `schemas/events/` is now `src/schemas/events/`. |
| `docs/event-contracts.md:19` | Link target is now `../src/schemas/events/lesson_completed.schema.json`. |

No test files referenced the old path. `docs/audit/week4-fix6b-dedup-key.md` still mentions `schemas/envelope.schema.json`. It is a historical record, so it was left as written.

**Test counts.** Command: `.venv/bin/python -m pytest -q --ignore=tests/leaderboard --ignore=tests/notifications`. Result: 261 passed, 4 skipped, 0 failed. The earlier baseline was not re-run because that would need a git stash, which was off limits.

## Step G1b

**Changes.**

| File | Change |
|---|---|
| `src/state_machine/rules.py` | With `path=None`, `load_rulesets` reads `files("src.state_machine") / "rulesets.json"`. An explicit `path` is read as before. `RULESETS_FILE` is kept, because `tests/state_machine/test_rules_immutable.py` imports it. |
| `pyproject.toml` | Added `[tool.setuptools.package-data]` for `src` (`schemas/*.json`, `schemas/events/*.json`) and `src.state_machine` (`rulesets.json`). |
| `tests/packaging/__init__.py`, `tests/packaging/test_wheel_contents.py` | New. The test builds a wheel with `pip wheel . --no-deps --no-build-isolation` and asserts the three required names are in the zip. |

**Test results.**

- Full suite excluding `tests/leaderboard`, `tests/notifications` and `tests/packaging`: 261 passed, 4 skipped, 0 failed, same as Step G1a.
- `tests/packaging/test_wheel_contents.py`: 1 passed. `setuptools` and `wheel` were installed into `.venv` (by you), and the built wheel contains all three required files.

## Step G1c

**Background.** Before Step G1a, an installed wheel raised `FileNotFoundError` for the schemas (the review's finding). The loaders resolved `Path(__file__).parents[2] / "schemas"`, which does not exist in site-packages. The repo was not reverted to reproduce this.

**Changes.**

| File | Change |
|---|---|
| `pyproject.toml` | Added `build` to the dev extras. Added `addopts = "-m 'not integration'"`, so integration tests are skipped by default. |
| `tests/packaging/test_wheel_install.py` | New, one `@pytest.mark.integration` test. It builds the wheel, creates a venv in `tmp_path`, and installs the wheel there (needs network). A script in a separate folder then ingests one valid `lesson_completed` envelope. It runs with that venv's python, with cwd set to the other folder, no `PYTHONPATH`, and a minimal environment. The test asserts the script prints `accepted`. |
| `README.md` | Added the line: `pytest -m integration` runs the packaging test. |

**Test results.**

`python -m pytest -m integration tests/packaging -q`:

```
.                                                                        [100%]
1 passed, 1 deselected in 4.29s
```

`python -m pytest -q --ignore=tests/leaderboard --ignore=tests/notifications`:

```
262 passed, 4 skipped, 1 deselected in 3.93s
```

The default run grew from 261 to 262 passed because of the wheel-contents test added in G1b. The one deselected test is the new integration test.
