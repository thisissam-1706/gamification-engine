"""CLI runner for Milestone M8 Scale and Recovery benchmarks and reproducibility fixtures."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile

from src.scale_and_recovery.experiment_repro import verify_experiment_reproduction
from src.scale_and_recovery.hardware import capture_hardware_environment
from src.scale_and_recovery.latency import LatencyThresholds, measure_leaderboard_latency
from src.scale_and_recovery.outage import NotificationOutageHarness
from src.scale_and_recovery.rebuild import FullStateRebuildHarness
from src.scale_and_recovery.workload import WorkloadProfile, generate_synthetic_workload


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run Milestone M8 Scale and Recovery benchmark.")
    parser.add_argument(
        "--profile",
        choices=["small", "large"],
        default="small",
        help="Select 'small' reproducibility fixture or 'large' load profile.",
    )
    parser.add_argument("--seed", type=int, default=42, help="Pinned random seed.")
    parser.add_argument("--out", type=str, default=None, help="Optional output JSON report path.")
    return parser.parse_args(argv)


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
        print("[1/4] Generating pinned synthetic workload...")
        workload = generate_synthetic_workload(profile)
        print(f"      Generated {workload.total_events} events ({workload.clean_events_count} clean, "
              f"{workload.duplicate_events_count} duplicates, {workload.late_events_count} late).")

        # Step 2: Full-state rebuild and disaster recovery (AT-15, AT-2)
        print("[2/4] Executing full-state disaster recovery rebuild...")
        harness = FullStateRebuildHarness(events_db, lb_db, epoch=profile.epoch)
        rebuild_result = harness.run_rebuild_verification(workload)
        print(f"      Rebuild status: {'PASS' if rebuild_result.bit_identical else 'FAIL'}")
        print(f"      Replayed {rebuild_result.total_events} events in {rebuild_result.rebuild_duration_sec:.3f}s "
              f"({rebuild_result.throughput_events_per_sec:.1f} events/sec).")
        print(f"      Checksum match: {rebuild_result.pre_checksum[:16]}... == {rebuild_result.post_checksum[:16]}...")

        # Step 3: Latency benchmarking under load
        print("[3/4] Measuring leaderboard latency percentiles...")
        harness.initialize_live_system()
        try:
            latency_res = measure_leaderboard_latency(
                service=harness.service,
                store=harness.store,
                epoch=profile.epoch,
                cohort_id="cohort_a",
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
        print("[4/4] Simulating notification outage and DLQ recovery drain...")
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
        print("[5/5] Reproducing experiment metrics from raw logs...")
        exp_res = verify_experiment_reproduction(workload.learner_ids, seed=profile.seed)
        print(f"      Reproduction: {'PASS' if exp_res.bit_identical else 'FAIL'}")
        print(f"      Checksum match: {exp_res.checksum_first[:16]}... == {exp_res.checksum_second[:16]}...")

    overall_passed = (
        rebuild_result.bit_identical
        and latency_res.passed
        and outage_res.passed
        and exp_res.bit_identical
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
