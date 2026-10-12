"""Performance benchmark for measuring update and query latencies against M3 P99 SLA (<2.0s)."""

import time
import pytest

from src.leaderboard.store import SQLiteLeaderboardStore, InMemoryLeaderboardStore
from src.leaderboard.service import LeaderboardService


@pytest.mark.parametrize("store_type", ["sqlite", "in_memory"])
def test_update_and_query_latency_benchmarks(tmp_path, store_type):
    epoch = "2026-W41"
    cohort_id = "cohort_perf_01"

    if store_type == "sqlite":
        db_path = str(tmp_path / "perf.db")
        store = SQLiteLeaderboardStore(db_path)
    else:
        store = InMemoryLeaderboardStore()

    service = LeaderboardService(store)
    num_learners = 1000

    # 1. Measure Update Latencies
    update_latencies: list[float] = []
    for i in range(num_learners):
        subject_id = f"learner_{i:04d}"
        score = (i * 17) % 5000
        ts = f"2026-10-12T10:{i % 60:02d}:00Z"

        t0 = time.perf_counter()
        store.upsert_score("cohort", cohort_id, epoch, subject_id, score, ts, ts, watermark_offset=i + 1)
        t1 = time.perf_counter()
        update_latencies.append((t1 - t0) * 1000.0)  # in ms

    update_latencies.sort()
    p50_update = update_latencies[int(num_learners * 0.50)]
    p95_update = update_latencies[int(num_learners * 0.95)]
    p99_update = update_latencies[int(num_learners * 0.99)]

    # Assert P99 update latency is well under the 2000 ms (2.0s) SLA
    assert p99_update < 2000.0, f"P99 update latency exceeded 2s: {p99_update}ms"

    # 2. Measure Query Latency: Top-K (Top 10 and Top 50)
    top_k_latencies: list[float] = []
    for _ in range(100):
        t0 = time.perf_counter()
        results = service.get_top_k("cohort", cohort_id, epoch, k=50)
        t1 = time.perf_counter()
        top_k_latencies.append((t1 - t0) * 1000.0)
        assert len(results) == 50

    top_k_latencies.sort()
    p50_topk = top_k_latencies[50]
    p95_topk = top_k_latencies[95]
    p99_topk = top_k_latencies[99]
    assert p99_topk < 500.0, f"P99 Top-K query latency exceeded 500ms: {p99_topk}ms"

    # 3. Measure Query Latency: Surrounding Window (window = 5)
    surrounding_latencies: list[float] = []
    sample_learners = [f"learner_{i:04d}" for i in range(0, num_learners, 20)]
    for subj in sample_learners:
        t0 = time.perf_counter()
        window_results = service.get_surrounding("cohort", cohort_id, epoch, subj, window=5)
        t1 = time.perf_counter()
        surrounding_latencies.append((t1 - t0) * 1000.0)
        assert len(window_results) > 0

    surrounding_latencies.sort()
    p50_surrounding = surrounding_latencies[int(len(surrounding_latencies) * 0.50)]
    p95_surrounding = surrounding_latencies[int(len(surrounding_latencies) * 0.95)]
    p99_surrounding = surrounding_latencies[int(len(surrounding_latencies) * 0.99)]
    assert p99_surrounding < 500.0, f"P99 surrounding query latency exceeded 500ms: {p99_surrounding}ms"

    # 4. Measure Query Latency: Individual Rank Lookup
    rank_latencies: list[float] = []
    for subj in sample_learners:
        t0 = time.perf_counter()
        standing = service.get_learner_rank("cohort", cohort_id, epoch, subj)
        t1 = time.perf_counter()
        rank_latencies.append((t1 - t0) * 1000.0)
        assert standing is not None

    rank_latencies.sort()
    p50_rank = rank_latencies[int(len(rank_latencies) * 0.50)]
    p95_rank = rank_latencies[int(len(rank_latencies) * 0.95)]
    p99_rank = rank_latencies[int(len(rank_latencies) * 0.99)]
    assert p99_rank < 500.0, f"P99 rank lookup latency exceeded 500ms: {p99_rank}ms"

    print(f"\n[{store_type.upper()}] Benchmark Results (N={num_learners} learners):")
    print(f"  Update Latency:      p50={p50_update:.3f}ms, p95={p95_update:.3f}ms, p99={p99_update:.3f}ms")
    print(f"  Top-50 Query:        p50={p50_topk:.3f}ms, p95={p95_topk:.3f}ms, p99={p99_topk:.3f}ms")
    print(f"  Surrounding Window:  p50={p50_surrounding:.3f}ms, p95={p95_surrounding:.3f}ms, p99={p99_surrounding:.3f}ms")
    print(f"  Rank Lookup:         p50={p50_rank:.3f}ms, p95={p95_rank:.3f}ms, p99={p99_rank:.3f}ms")

    store.close()
