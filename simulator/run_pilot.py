"""Run simulated exp_007 pilots and write their streams and reports to disk."""

import argparse
import json
from pathlib import Path

from simulator.generator import create_persona_population, generate_common_random_numbers
from simulator.pilot import SETTINGS, simulate
from simulator.pilot_report import LEDGER_BUCKETS, analyze, build_ledger

STREAMS = (
    "assignments", "eligibility", "decisions", "deliveries",
    "exposures", "attempts", "completions",
)


def _write(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _dumps(value) -> str:
    return json.dumps(value, sort_keys=True)


def run_single(setting: str, seed: int, n: int, out_dir: str) -> dict:
    population = create_persona_population(n, seed)
    draws = generate_common_random_numbers(population, seed)
    run = simulate(population, draws, setting, seed)
    report = analyze(run)
    ledger = build_ledger(run)
    report["ledger_summary"] = {
        variant: {
            bucket: sum(
                1 for row in ledger
                if row["variant"] == variant and row["outcome_bucket"] == bucket
            )
            for bucket in LEDGER_BUCKETS
        }
        for variant in ("variant_a", "variant_b")
    }

    directory = Path(out_dir) / f"pilot_{setting}_seed{seed}_n{n}"
    directory.mkdir(parents=True, exist_ok=True)
    for stream in STREAMS:
        rows = getattr(run, stream)
        _write(directory / f"{stream}.jsonl", "".join(_dumps(row) + "\n" for row in rows))
    _write(directory / "ledger.jsonl", "".join(_dumps(row) + "\n" for row in ledger))
    _write(directory / "report.json", _dumps(report) + "\n")
    return report


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def run_many(setting: str, seeds: list[int], n: int, out_dir: str) -> dict:
    reports = {seed: run_single(setting, seed, n, out_dir) for seed in seeds}
    per_seed = []
    for seed, report in reports.items():
        contrast = report["contrast"]
        per_seed.append({
            "seed": seed,
            "difference": contrast["difference"],
            "ci95_low": contrast["ci95_low"],
            "ci95_high": contrast["ci95_high"],
            "conclusive": contrast["conclusive"],
            "srm_alert": report["srm"]["alert"],
        })
    used = [row for row in per_seed if row["difference"] is not None]
    skipped = [row["seed"] for row in per_seed if row["difference"] is None]
    differences = [row["difference"] for row in used]
    used_reports = [reports[row["seed"]] for row in used]

    summary = {
        "setting": setting,
        "n": n,
        "per_seed": per_seed,
        "skipped_seeds": skipped,
        "mean_difference": _mean(differences),
        "min_difference": min(differences, default=None),
        "max_difference": max(differences, default=None),
        "conclusive_fraction": (
            sum(1 for row in used if row["conclusive"]) / len(used) if used else None
        ),
        "mean_exposure_rate_treatment": _mean(
            r["arms"]["treatment"]["exposure_rate"] for r in used_reports
        ),
        "mean_missing_outcome_rate_treatment": _mean(
            r["arms"]["treatment"]["missing_outcome_rate"] for r in used_reports
        ),
        "mean_missing_outcome_rate_control": _mean(
            r["arms"]["control"]["missing_outcome_rate"] for r in used_reports
        ),
        "total_cap_breach_learners_treatment": sum(
            r["arms"]["treatment"]["cap_breach_learners"] for r in used_reports
        ),
        "total_opt_outs_treatment": sum(r["arms"]["treatment"]["opt_outs"] for r in used_reports),
        "total_opt_outs_control": sum(r["arms"]["control"]["opt_outs"] for r in used_reports),
    }
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    first, last = seeds[0], seeds[-1]
    _write(directory / f"summary_{setting}_n{n}_seeds{first}-{last}.json", _dumps(summary) + "\n")
    return summary


def _parse_seeds(text: str) -> list[int]:
    if "-" in text:
        first, last = text.split("-", 1)
        return list(range(int(first), int(last) + 1))
    return [int(text)]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run an exp_007 simulated pilot.")
    parser.add_argument("--setting", required=True, choices=sorted(SETTINGS))
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--n", type=int, default=4000)
    parser.add_argument("--out", default="docs/evidence/pilot")
    parser.add_argument("--seeds", help='seed range such as "1-20"; runs many seeds')
    args = parser.parse_args(argv)

    if args.seeds:
        run_many(args.setting, _parse_seeds(args.seeds), args.n, args.out)
    else:
        run_single(args.setting, args.seed, args.n, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
