"""Scale and recovery orchestration module (M8).

Provides:
- Pinned synthetic workload generation with duplicate and late event mix.
- Full-state disaster recovery and replay orchestration (AT-2, AT-15).
- Leaderboard latency profiling (p50/p95/p99) against pre-declared SLAs.
- Notification outage simulation and DLQ recovery drain.
- Raw-log experiment outcome reproduction.
- System hardware and execution environment metadata pinning.
"""

from .experiment_repro import ExperimentReproductionResult, verify_experiment_reproduction
from .hardware import HardwareEnvironment, capture_hardware_environment
from .latency import LatencyBenchmarkResult, LatencyThresholds, MetricPercentiles, measure_leaderboard_latency
from .outage import ControllableOutageAdapter, NotificationOutageHarness, OutageRecoveryResult
from .rebuild import FullStateRebuildHarness, LearnerSnapshot, RebuildResult, read_all_notifications
from .workload import SyntheticEvent, SyntheticWorkload, WorkloadProfile, generate_synthetic_workload

__all__ = [
    "WorkloadProfile",
    "SyntheticEvent",
    "SyntheticWorkload",
    "generate_synthetic_workload",
    "LearnerSnapshot",
    "RebuildResult",
    "FullStateRebuildHarness",
    "read_all_notifications",
    "LatencyThresholds",
    "MetricPercentiles",
    "LatencyBenchmarkResult",
    "measure_leaderboard_latency",
    "ControllableOutageAdapter",
    "OutageRecoveryResult",
    "NotificationOutageHarness",
    "ExperimentReproductionResult",
    "verify_experiment_reproduction",
    "HardwareEnvironment",
    "capture_hardware_environment",
]
