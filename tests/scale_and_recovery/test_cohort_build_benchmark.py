"""Smoke test for scripts/run_cohort_build_benchmark.py (300 learners, about a second).

The 2000-learner run with the production defaults is a script run, not part of the suite.
"""

from pathlib import Path
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_cohort_build_benchmark_runs_with_production_defaults_and_reports_no_oversized_cohort():
    completed = subprocess.run(
        [sys.executable, "scripts/run_cohort_build_benchmark.py", "--learners", "300"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "min_size=500, max_size=5000, automatic buckets" in completed.stdout
    assert "assigned:            300" in completed.stdout
    assert "OK: no cohort above max_size 5000" in completed.stdout
