"""CLI runner for Milestone M8 Scale and Recovery benchmarks and reproducibility fixtures."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import sys
import tempfile
from datetime import timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from src.experiments.assignment import assign
from src.experiments.service import assign_learner_to_experiment
from src.experiments.store import ExperimentStore
from src.scale_and_recovery.experiment_repro import (
    verify_engine_reproduction,
    verify_experiment_reproduction,
)
from src.state_machine.learner import Learner, LearnerApplication
from src.scale_and_recovery.hardware import capture_hardware_environment
from src.scale_and_recovery.latency import LatencyThresholds, measure_leaderboard_latency
from src.scale_and_recovery.outage import NotificationOutageHarness
from src.scale_and_recovery.rebuild import FullStateRebuildHarness
from src.scale_and_recovery.workload import WorkloadProfile, generate_synthetic_workload


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run Milestone M8 Scale and Recovery benchmark.")
    parser.add_argument(
        "--profile",
        choices=["small", "large", "xlarge"],
        default="small",
        help="Select 'small' reproducibility fixture, 'large' or 'xlarge' load profile.",
    )
    parser.add_argument("--seed", type=int, default=42, help="Pinned random seed.")
    parser.add_argument("--out", type=str, default=None, help="Optional output JSON report path.")
    return parser.parse_args(argv)


TOTAL_STEPS = 6
ENGINE_EXP_ID = "exp_007"
ENGINE_ASSESSMENT_ID = "asmt-1"
ENGINE_ASSIGNED_AT = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
ENGINE_AS_OF = ENGINE_ASSIGNED_AT + timedelta(days=30)


def build_engine_repro_log(db_path: str, population_size: int, seed: int) -> dict:
    """Persists a log for the experiment engine: learners, exp_007 assignments, eligibility rows, attempts.

    Modelled on the log builder of tests/experiments/test_m7_pipeline_e2e.py: ids are uuid5 values (as
    ``assign`` hashes them) taken in order until ``population_size`` learners exist, each saved, assigned to
    the experiment and given an eligible row; then a seeded 70% of them make one checkpoint attempt, passing
    with probability 0.6. Returns ``{"learners": n, "attempts": n}``.
    """
    rng = random.Random(seed)
    application = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db_path}
    )
    store = ExperimentStore(db_path)
    attempts = 0
    try:
        learner_ids: list[str] = []
        candidate = 0
        while len(learner_ids) < population_size:
            learner_id = uuid5(NAMESPACE_URL, f"learner-{candidate}")
            candidate += 1
            variant = assign(str(learner_id), ENGINE_EXP_ID)[0]
            learner = Learner._create(Learner.Created, id=learner_id)
            application.save(learner)
            assert assign_learner_to_experiment(application, str(learner_id), ENGINE_EXP_ID, ENGINE_ASSIGNED_AT)
            store.record_eligibility({
                "exp_id": ENGINE_EXP_ID, "learner_id": str(learner_id), "variant": variant,
                "assigned_at": ENGINE_ASSIGNED_AT.isoformat(), "eligible": 1, "reason": None,
                "opted_out_push": 0, "opted_out_streak_risk": 0, "timezone": "Asia/Kolkata",
                "tenure_days": 40,
            })
            learner_ids.append(str(learner_id))
        for n, learner_id in enumerate(learner_ids):
            if rng.random() >= 0.7:
                continue
            learner = application.repository.get(UUID(learner_id))
            assert learner.record_checkpoint_attempt(
                f"att-{n}", ENGINE_ASSESSMENT_ID, 1, rng.random() < 0.6, timezone="Asia/Kolkata",
                occurred_at=ENGINE_ASSIGNED_AT + timedelta(days=1, minutes=n),
            )
            application.save(learner)
            attempts += 1
    finally:
        store.close()
        application.close()
    return {"learners": len(learner_ids), "attempts": attempts}


def run_benchmark(profile_name: str = "small", seed: int = 42) -> dict:
    # 1. Pinned parameters
    if profile_name == "small":
        profile = WorkloadProfile(
            population_size=20,
            cohort_count=2,
            events_per_learner=15,
            duplicate_rate=0.05,
            late_rate=0.05,
            seed=seed,
        )
        outage_proposals = 30
        latency_queries = 30
    elif profile_name == "xlarge":
        profile = WorkloadProfile(
            population_size=1000,
            cohort_count=2,
            events_per_learner=30,
            duplicate_rate=0.05,
            late_rate=0.05,
            seed=seed,
        )
        outage_proposals = 500
        latency_queries = 500
    else:
        profile = WorkloadProfile(
            population_size=100,
            cohort_count=2,
            events_per_learner=30,
            duplicate_rate=0.05,
            late_rate=0.05,
            seed=seed,
        )
        outage_proposals = 100
        latency_queries = 100

    hw = capture_hardware_environment()
    print("=" * 70)
    print("  MILESTONE M8: SCALE AND RECOVERY BENCHMARK")
    print("=" * 70)
    print(f"Profile:            {profile_name.upper()} (Seed: {profile.seed})")
    print(f"Population Size:    {profile.population_size} learners")
    print(f"Duplicate Rate:     {profile.duplicate_rate * 100:.1f}%")
    print(f"Late Window Rate:   {profile.late_rate * 100:.1f}%")
    print(f"Platform:           {hw.os_system} {hw.os_release} ({hw.processor}, {hw.logical_cpus} CPUs)")
    print(f"Python / SQLite:    Python {hw.python_version} / SQLite {hw.sqlite_version}")
    print("-" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        events_db = str(tmp_path / "events.db")
        lb_db = str(tmp_path / "leaderboard.db")
        notif_db = str(tmp_path / "notifications.db")

        # Step 1: Generate pinned synthetic workload
        print(f"[1/{TOTAL_STEPS}] Generating pinned synthetic workload...")
        workload = generate_synthetic_workload(profile)
        print(f"      Generated {workload.total_events} events ({workload.clean_events_count} clean, "
              f"{workload.duplicate_events_count} duplicates, {workload.late_events_count} late).")

        # Step 2: Full-state rebuild and disaster recovery (AT-15, AT-2)
        print(f"[2/{TOTAL_STEPS}] Executing full-state disaster recovery rebuild...")
        harness = FullStateRebuildHarness(events_db, lb_db, epoch=profile.epoch)
        rebuild_result = harness.run_rebuild_verification(workload)
        print(f"      Rebuild status: {'PASS' if rebuild_result.bit_identical else 'FAIL'}")
        print(f"      Replayed {rebuild_result.total_events} events in {rebuild_result.rebuild_duration_sec:.3f}s "
              f"({rebuild_result.throughput_events_per_sec:.1f} events/sec).")
        print(f"      Checksum match: {rebuild_result.pre_checksum[:16]}... == {rebuild_result.post_checksum[:16]}...")

        # Step 3: Latency benchmarking under load
        print(f"[3/{TOTAL_STEPS}] Measuring leaderboard latency percentiles...")
        harness.initialize_live_system()
        try:
            latency_res = measure_leaderboard_latency(
                service=harness.service,
                store=harness.store,
                epoch=profile.epoch,
                # Cohorts come from the cohort builder (filled in during ingestion): use the largest.
                cohort_id=max(sorted(set(workload.cohort_assignments.values())),
                              key=list(workload.cohort_assignments.values()).count),
                sample_learners=workload.learner_ids,
                num_query_samples=latency_queries,
            )
            print(f"      Update latency: P50={latency_res.update_latency.p50:.2f}ms, "
                  f"P95={latency_res.update_latency.p95:.2f}ms, P99={latency_res.update_latency.p99:.2f}ms")
            print(f"      Top-K latency:  P50={latency_res.topk_latency.p50:.2f}ms, "
                  f"P95={latency_res.topk_latency.p95:.2f}ms, P99={latency_res.topk_latency.p99:.2f}ms")
            print(f"      Rank latency:   P50={latency_res.rank_latency.p50:.2f}ms, "
                  f"P95={latency_res.rank_latency.p95:.2f}ms, P99={latency_res.rank_latency.p99:.2f}ms")
        finally:
            harness.close()

        # Step 4: Notification outage and recovery
        print(f"[4/{TOTAL_STEPS}] Simulating notification outage and DLQ recovery drain...")
        notif_harness = NotificationOutageHarness(notif_db)
        try:
            outage_res = notif_harness.run_outage_and_recovery(num_proposals=outage_proposals)
            print(f"      DLQ capture rate: {outage_res.dlq_capture_rate * 100:.1f}% ({outage_res.dlq_routed_count}/{outage_res.proposals_dispatched})")
            print(f"      Recovery drain:   {outage_res.recovery_drained_count} drained in {outage_res.recovery_drain_duration_sec:.3f}s "
                  f"({outage_res.drain_throughput_msgs_per_sec:.1f} msgs/sec)")
            print(f"      Duplicates:       {outage_res.duplicate_deliveries_count}")
        finally:
            notif_harness.close()

        # Step 5: Raw-log experiment outcome reproduction
        print(f"[5/{TOTAL_STEPS}] Reproducing experiment metrics from raw logs...")
        exp_res = verify_experiment_reproduction(workload.learner_ids, seed=profile.seed)
        print(f"      Reproduction: {'PASS' if exp_res.bit_identical else 'FAIL'}")
        print(f"      Checksum match: {exp_res.checksum_first[:16]}... == {exp_res.checksum_second[:16]}...")

        # Step 6: the real experiment engine over a persisted log
        print(f"[6/{TOTAL_STEPS}] Reproducing the experiment engine from a persisted log...")
        engine_db = str(tmp_path / "engine_repro.db")
        built = build_engine_repro_log(engine_db, profile.population_size, profile.seed)
        engine_res = verify_engine_reproduction(
            engine_db, exp_id=ENGINE_EXP_ID, assessment_id=ENGINE_ASSESSMENT_ID, as_of=ENGINE_AS_OF
        )
        print(f"      Reproduction: {'PASS' if engine_res.bit_identical else 'FAIL'} "
              f"({engine_res.population_size} learners, {engine_res.total_attempts} attempts)")
        print(f"      Checksum match: {engine_res.checksum_first[:16]}... == {engine_res.checksum_second[:16]}...")

    overall_passed = (
        rebuild_result.bit_identical
        and latency_res.passed
        and outage_res.passed
        and exp_res.bit_identical
        and engine_res.bit_identical
    )

    print("=" * 70)
    print(f"OVERALL BENCHMARK RESULT: {'PASS' if overall_passed else 'FAIL'}")
    print("=" * 70)

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "profile": profile.to_dict(),
        "hardware": hw.to_dict(),
        "rebuild": rebuild_result.to_dict(),
        "latency": latency_res.to_dict(),
        "outage_recovery": outage_res.to_dict(),
        "experiment_repro": exp_res.to_dict(),
        "engine_repro": {
            "bit_identical": engine_res.bit_identical,
            "checksum_first": engine_res.checksum_first,
            "checksum_second": engine_res.checksum_second,
            "population_size": engine_res.population_size,
            "total_attempts": engine_res.total_attempts,
            "log_learners": built["learners"],
            "log_attempts": built["attempts"],
        },
        "overall_passed": overall_passed,
    }
    return report


def main(argv=None):
    args = parse_args(argv)
    report = run_benchmark(profile_name=args.profile, seed=args.seed)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Report written to: {args.out}")
    return 0 if report["overall_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
