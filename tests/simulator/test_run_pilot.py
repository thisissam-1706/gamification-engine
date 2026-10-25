"""Pilot 4: file-writing runner for simulated pilots."""

import json
from collections import Counter

import pytest

from simulator.run_pilot import main, run_many, run_single

STREAMS = (
    "assignments", "eligibility", "decisions", "deliveries",
    "exposures", "attempts", "completions",
)


def _run_dir(root, setting="null", seed=7, n=200):
    return root / f"pilot_{setting}_seed{seed}_n{n}"


def test_run_single_writes_all_files_with_one_line_per_learner(tmp_path):
    run_single("null", 7, 200, str(tmp_path))
    directory = _run_dir(tmp_path)
    names = {p.name for p in directory.iterdir()}
    assert names == {f"{s}.jsonl" for s in STREAMS} | {"ledger.jsonl", "report.json"}
    for stream in ("assignments", "eligibility", "decisions"):
        lines = (directory / f"{stream}.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 200


def test_run_single_output_is_byte_identical_across_runs(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    run_single("positive", 7, 200, str(first))
    run_single("positive", 7, 200, str(second))
    files = sorted(p.name for p in _run_dir(first, "positive").iterdir())
    assert files
    for name in files:
        assert (_run_dir(first, "positive") / name).read_bytes() == (
            _run_dir(second, "positive") / name
        ).read_bytes()


def test_report_json_records_the_data_cutoff(tmp_path):
    report = run_single("null", 7, 200, str(tmp_path))
    written = json.loads((_run_dir(tmp_path) / "report.json").read_text(encoding="utf-8"))
    assert written["meta"]["data_cutoff"] == "2026-09-29T00:00:00Z"
    assert report["meta"]["data_cutoff"] == "2026-09-29T00:00:00Z"


def test_run_many_writes_summary(tmp_path):
    summary = run_many("null", [1, 2, 3], 200, str(tmp_path))
    assert len(summary["per_seed"]) == 3
    assert (tmp_path / "summary_null_n200_seeds1-3.json").is_file()
    assert "skipped_seeds" in summary and "mean_difference" in summary


def test_main_returns_zero(tmp_path):
    argv = ["--setting", "null", "--seed", "3", "--n", "200", "--out", str(tmp_path)]
    assert main(argv) == 0
    assert _run_dir(tmp_path, "null", 3).is_dir()


def test_main_with_seeds_runs_many(tmp_path):
    argv = ["--setting", "null", "--n", "200", "--out", str(tmp_path), "--seeds", "1-2"]
    assert main(argv) == 0
    assert (tmp_path / "summary_null_n200_seeds1-2.json").is_file()


def test_main_rejects_unknown_setting(tmp_path):
    with pytest.raises(SystemExit):
        main(["--setting", "bogus", "--out", str(tmp_path)])


def _ledger(root, setting="null", seed=7, n=200):
    path = _run_dir(root, setting, seed, n) / "ledger.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_ledger_has_one_unique_row_per_learner(tmp_path):
    run_single("null", 7, 200, str(tmp_path))
    rows = _ledger(tmp_path)
    assert len(rows) == 200
    ids = [r["learner_id"] for r in rows]
    assert len(set(ids)) == 200 and ids == sorted(ids)


def test_ledger_bucket_counts_match_the_report(tmp_path):
    report = run_single("positive", 7, 200, str(tmp_path))
    rows = _ledger(tmp_path, "positive")
    for arm, variant in (("treatment", "variant_a"), ("control", "variant_b")):
        counts = Counter(r["outcome_bucket"] for r in rows if r["variant"] == variant)
        buckets = report["arms"][arm]["buckets"]
        assert counts["mastered"] == report["arms"][arm]["mastery"]["mastered"]
        assert counts["failed"] == report["arms"][arm]["mastery"]["failed"]
        assert counts["immature"] == buckets["immature"]
        assert counts["missing_no_attempt"] == buckets["missing_no_attempt"]
        assert counts["missing_no_completion"] == buckets["missing_attempt_no_completion"]
        assert (
            counts["missing_no_attempt"] + counts["missing_no_completion"]
            == buckets["mature_no_outcome"]
        )
        assert counts["invalid_ordering"] + counts["conflicting"] == buckets["mature_invalid"]
        assert sum(v for k, v in counts.items() if k != "excluded") == report["arms"][arm]["eligible"]
        assert report["ledger_summary"][variant] == {
            k: counts.get(k, 0) for k in report["ledger_summary"][variant]
        }


def test_ledger_no_action_rows_have_no_messages_or_exposure(tmp_path):
    run_single("positive", 7, 200, str(tmp_path))
    rows = _ledger(tmp_path, "positive")
    quiet = [r for r in rows if r["action"] == "no_action"]
    assert quiet and any(r["action"] == "send_reminders" for r in rows)
    for row in quiet:
        assert row["messages_sent"] == 0 and row["exposed"] is False


def test_ledger_is_byte_identical_across_runs(tmp_path):
    run_single("harmful", 7, 200, str(tmp_path / "a"))
    run_single("harmful", 7, 200, str(tmp_path / "b"))
    name = "pilot_harmful_seed7_n200/ledger.jsonl"
    assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
