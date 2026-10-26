"""Runs the seeded persona evaluation and prints the measured table (markdown).

    python -m tests.abuse.run_evaluation
"""

import tempfile
import time
from pathlib import Path

from src.abuse.evaluation import EvaluationReport, evaluate, store_resolver
from src.abuse.service import AbuseDetectionService, RunSummary
from src.abuse.store import AbuseSignalStore
from tests.abuse.fixtures import SEED, build_population
from tests.abuse.harness import Harness


def run_once(directory: str, seed: int = SEED, copies: int = 1):
    """Builds the population, runs one detection pass and scores it."""
    h = Harness(str(Path(directory) / "events.db"))
    try:
        labels = build_population(h, seed, copies)
        store = AbuseSignalStore(h.db)
        service = AbuseDetectionService(h.application, h.evidence, store)
        started = time.perf_counter()  # measurement only; detection itself has no clock
        summary = service.run()
        elapsed = time.perf_counter() - started
        report = evaluate(labels, summary.signals, store_resolver(h.application, h.evidence))
        store.close()
        return labels, summary, report, elapsed
    finally:
        h.close()


def format_table(report: EvaluationReport, summary: RunSummary, elapsed: float) -> str:
    def cell(value):
        return "n/a" if value is None else f"{value:.2f}"

    lines = ["| signal type | TP | FP | FN | precision | recall |", "|---|---|---|---|---|---|"]
    for signal_type, m in report.per_type.items():
        lines.append(
            f"| {signal_type} | {m.true_positives} | {m.false_positives} | {m.false_negatives} "
            f"| {cell(m.precision)} | {cell(m.recall)} |"
        )
    lines += [
        "",
        f"- benign false positives: {report.benign_false_positives} of {report.benign_learners} benign personas",
        f"- designed attackers fully detected: {report.attackers_fully_detected} of {report.attacker_learners}",
        f"- evidence-backed rate: {cell(report.evidence_backed_rate)} ({report.signals_total} signals)",
        f"- detection latency: {elapsed:.3f} s for {summary.events_read} stored events "
        f"({summary.learners_scanned} learners); informational, not a pass/fail number",
        "",
        "| borderline persona | ground truth | detected | labeled outcome reproduced |",
        "|---|---|---|---|",
    ]
    for b in report.borderline:
        lines.append(
            f"| {b.persona} | {', '.join(b.truth_types) or 'none'} | {', '.join(b.detected_types) or 'none'} "
            f"| {'yes' if b.behaved_as_labeled else 'NO'} |"
        )
    return "\n".join(lines)


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        _, summary, report, elapsed = run_once(directory)
    print(format_table(report, summary, elapsed))


if __name__ == "__main__":
    main()
