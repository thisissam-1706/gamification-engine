"""Latency profiling and SLA threshold verification (p50/p95/p99) under load."""

from dataclasses import asdict, dataclass, field
import time
from typing import Any

from src.leaderboard.service import LeaderboardService
from src.leaderboard.store import SQLiteLeaderboardStore


@dataclass(frozen=True)
class MetricPercentiles:
    """Calculated latency percentiles for an operation."""

    p50: float
    p95: float
    p99: float
    min_ms: float
    max_ms: float
    count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LatencyThresholds:
    """Pre-declared numerical success criteria for leaderboard operations."""

    # SQLite single-process thresholds (SLA ceilings are 2000ms update, 500ms query)
    update_p50_ms: float = 5.0
    update_p95_ms: float = 20.0
    update_p99_ms: float = 50.0

    topk_p50_ms: float = 5.0
    topk_p95_ms: float = 20.0
    topk_p99_ms: float = 50.0

    surrounding_p50_ms: float = 5.0
    surrounding_p95_ms: float = 20.0
    surrounding_p99_ms: float = 50.0

    rank_p50_ms: float = 5.0
    rank_p95_ms: float = 20.0
    rank_p99_ms: float = 50.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LatencyBenchmarkResult:
    """Results of latency measurements compared against pre-declared thresholds."""

    thresholds: LatencyThresholds
    update_latency: MetricPercentiles
    topk_latency: MetricPercentiles
    surrounding_latency: MetricPercentiles
    rank_latency: MetricPercentiles
    passed: bool
    violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "thresholds": self.thresholds.to_dict(),
            "update_latency": self.update_latency.to_dict(),
            "topk_latency": self.topk_latency.to_dict(),
            "surrounding_latency": self.surrounding_latency.to_dict(),
            "rank_latency": self.rank_latency.to_dict(),
            "passed": self.passed,
            "violations": self.violations,
        }


def _calc_percentiles(raw_ms: list[float]) -> MetricPercentiles:
    if not raw_ms:
        return MetricPercentiles(p50=0.0, p95=0.0, p99=0.0, min_ms=0.0, max_ms=0.0, count=0)
    sorted_ms = sorted(raw_ms)
    n = len(sorted_ms)
    p50_idx = min(int(n * 0.50), n - 1)
    p95_idx = min(int(n * 0.95), n - 1)
    p99_idx = min(int(n * 0.99), n - 1)
    return MetricPercentiles(
        p50=sorted_ms[p50_idx],
        p95=sorted_ms[p95_idx],
        p99=sorted_ms[p99_idx],
        min_ms=sorted_ms[0],
        max_ms=sorted_ms[-1],
        count=n,
    )


def measure_leaderboard_latency(
    service: LeaderboardService,
    store: SQLiteLeaderboardStore,
    epoch: str,
    cohort_id: str,
    sample_learners: list[str],
    num_query_samples: int = 50,
    thresholds: LatencyThresholds | None = None,
) -> LatencyBenchmarkResult:
    """Measures query and point update latencies against predefined thresholds."""
    if thresholds is None:
        thresholds = LatencyThresholds()

    # 1. Measure Top-K queries (k=50)
    topk_times: list[float] = []
    for _ in range(num_query_samples):
        t0 = time.perf_counter()
        _ = service.get_top_k("cohort", cohort_id, epoch, k=50)
        t1 = time.perf_counter()
        topk_times.append((t1 - t0) * 1000.0)

    # 2. Measure surrounding window queries (window=5)
    surrounding_times: list[float] = []
    learners_to_sample = sample_learners[:num_query_samples] if sample_learners else ["learner_0000"]
    for lid in learners_to_sample:
        t0 = time.perf_counter()
        _ = service.get_surrounding("cohort", cohort_id, epoch, lid, window=5)
        t1 = time.perf_counter()
        surrounding_times.append((t1 - t0) * 1000.0)

    # 3. Measure individual rank lookups
    rank_times: list[float] = []
    for lid in learners_to_sample:
        t0 = time.perf_counter()
        _ = service.get_learner_rank("cohort", cohort_id, epoch, lid)
        t1 = time.perf_counter()
        rank_times.append((t1 - t0) * 1000.0)

    # 4. Measure sample updates (upsert)
    update_times: list[float] = []
    for i, lid in enumerate(learners_to_sample):
        t0 = time.perf_counter()
        store.upsert_score(
            "cohort",
            cohort_id,
            epoch,
            lid,
            score=500 + i,
            first_reached_at="2026-10-12T12:00:00Z",
            updated_at="2026-10-12T12:00:00Z",
            watermark_offset=999990 + i,
        )
        t1 = time.perf_counter()
        update_times.append((t1 - t0) * 1000.0)

    update_p = _calc_percentiles(update_times)
    topk_p = _calc_percentiles(topk_times)
    surr_p = _calc_percentiles(surrounding_times)
    rank_p = _calc_percentiles(rank_times)

    violations: list[str] = []
    if update_p.p99 > thresholds.update_p99_ms:
        violations.append(f"Update P99 {update_p.p99:.2f}ms > threshold {thresholds.update_p99_ms}ms")
    if topk_p.p99 > thresholds.topk_p99_ms:
        violations.append(f"Top-K P99 {topk_p.p99:.2f}ms > threshold {thresholds.topk_p99_ms}ms")
    if surr_p.p99 > thresholds.surrounding_p99_ms:
        violations.append(f"Surrounding P99 {surr_p.p99:.2f}ms > threshold {thresholds.surrounding_p99_ms}ms")
    if rank_p.p99 > thresholds.rank_p99_ms:
        violations.append(f"Rank lookup P99 {rank_p.p99:.2f}ms > threshold {thresholds.rank_p99_ms}ms")

    return LatencyBenchmarkResult(
        thresholds=thresholds,
        update_latency=update_p,
        topk_latency=topk_p,
        surrounding_latency=surr_p,
        rank_latency=rank_p,
        passed=len(violations) == 0,
        violations=violations,
    )
