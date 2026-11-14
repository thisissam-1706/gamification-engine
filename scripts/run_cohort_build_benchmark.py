"""Benchmark the weekly cohort builder with its PRODUCTION defaults (min 500, max 5000, automatic buckets).

Generates N synthetic learners with prior-week history and module-prefixed lesson ids (the workload
helpers), ingests the history as ordinary lesson completions into a temporary event store, then runs
``build_weekly_cohorts`` once and reports what came out. Exits non-zero if any cohort is above
``max_size``.

    python scripts/run_cohort_build_benchmark.py            # 2000 learners
    python scripts/run_cohort_build_benchmark.py --learners 5000
"""

import argparse
from collections import Counter
from datetime import datetime
import inspect
from pathlib import Path
import statistics
import sys
import tempfile
import time
from uuid import UUID, uuid5

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.leaderboard.cohort_builder import build_weekly_cohorts  # noqa: E402
from src.scale_and_recovery.workload import WorkloadProfile, generate_synthetic_workload  # noqa: E402
from src.state_machine.learner import Learner, LearnerApplication  # noqa: E402

EPOCH = "2026-W42"
NAMESPACE = UUID("5d2e8b1c-0a47-4f3e-b6d9-2c7a91e4f053")
TIERS = ("casual", "regular", "intensive")


def tier_of(lesson_count: int) -> str:
    """The same thresholds as assign_activity_volume_cohort: <3 casual, 3-9 regular, >=10 intensive."""
    return "casual" if lesson_count < 3 else "regular" if lesson_count < 10 else "intensive"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Benchmark build_weekly_cohorts with production defaults.")
    parser.add_argument("--learners", type=int, default=2000, help="Population size (default 2000).")
    parser.add_argument("--seed", type=int, default=42, help="Workload seed.")
    return parser.parse_args(argv)


def run(learners: int, seed: int) -> int:
    defaults = inspect.signature(build_weekly_cohorts).parameters
    min_size, max_size = defaults["min_size"].default, defaults["max_size"].default

    # The workload is only a source of learners, modules and prior-week history here, so one epoch
    # event per learner is enough.
    profile = WorkloadProfile(population_size=learners, events_per_learner=1, seed=seed, epoch=EPOCH)
    workload = generate_synthetic_workload(profile)
    reference_time = datetime.fromisoformat(profile.base_start_iso.replace("Z", "+00:00"))
    history: dict[str, list] = {lid: [] for lid in workload.learner_ids}
    for event in workload.history_events:
        history[event.learner_id].append(event)

    with tempfile.TemporaryDirectory() as tmp:
        app = LearnerApplication(
            env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": str(Path(tmp) / "events.db")}
        )
        try:
            started = time.perf_counter()
            uuids: dict[str, str] = {}
            for lid in workload.learner_ids:
                learner = Learner._create(Learner.Created, id=uuid5(NAMESPACE, lid))
                for event in history[lid]:  # one save per learner keeps ingestion quick
                    learner.complete_lesson(
                        completion_id=event.completion_id,
                        lesson_id=event.lesson_id,
                        occurred_at=event.occurred_at,
                        timezone="UTC",
                    )
                app.save(learner)
                uuids[lid] = str(learner.id)
            ingest_seconds = time.perf_counter() - started

            started = time.perf_counter()
            result = build_weekly_cohorts(app, list(uuids.values()), EPOCH, reference_time=reference_time)
            build_seconds = time.perf_counter() - started
        finally:
            app.close()

    own_tier = {uuids[lid]: tier_of(len(history[lid])) for lid in workload.learner_ids}
    sizes = sorted(result.cohort_counts.values())
    print(f"learners:            {learners} (history ingested in {ingest_seconds:.2f}s, "
          f"{len(workload.history_events)} lessons)")
    print(f"defaults:            min_size={min_size}, max_size={max_size}, automatic buckets")
    print(f"assigned:            {len(result.assignments)}   inactive: {len(result.inactive)}")
    print(f"cohorts:             {len(sizes)}")
    if sizes:
        print(f"cohort size:         min {sizes[0]}  median {statistics.median(sizes):g}  max {sizes[-1]}")
    print("tier mix per cohort (learners by their own activity tier):")
    for cohort_id, size in sorted(result.cohort_counts.items()):
        mix = Counter(own_tier[l] for l, c in result.assignments.items() if c == cohort_id)
        label = cohort_id.replace(f"cohort_act_{EPOCH}_", "").replace(f"cohort_mod_{EPOCH}_", "")
        print(f"  {label:<34} {size:>5}  " + "  ".join(f"{t} {mix[t]}" for t in TIERS if mix[t]))
    print(f"build time:          {build_seconds:.2f}s   ({len(result.assignments) / build_seconds:.0f} learners/s)")

    too_big = {c: n for c, n in result.cohort_counts.items() if n > max_size}
    if too_big:
        print(f"FAIL: {len(too_big)} cohort(s) above max_size {max_size}: {too_big}")
        return 1
    print(f"OK: no cohort above max_size {max_size}")
    return 0


if __name__ == "__main__":
    args = parse_args()
    sys.exit(run(args.learners, args.seed))
