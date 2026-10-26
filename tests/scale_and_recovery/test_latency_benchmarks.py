"""Tests for leaderboard latency measurement and SLA threshold verification (M8)."""

from src.leaderboard.service import LeaderboardService
from src.leaderboard.store import SQLiteLeaderboardStore
from src.scale_and_recovery.latency import (
    LatencyThresholds,
    _calc_percentiles,
    measure_leaderboard_latency,
)


def test_percentile_calculation_ordering():
    data = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    p = _calc_percentiles(data)
    assert p.count == 10
    assert p.min_ms == 1.0
    assert p.max_ms == 10.0
    assert p.p50 <= p.p95 <= p.p99


def test_latency_measurement_satisfies_standard_sla(tmp_path):
    db_path = str(tmp_path / "latency_test.db")
    store = SQLiteLeaderboardStore(db_path)
    service = LeaderboardService(store)
    epoch = "2026-W42"
    cohort_id = "cohort_alpha"

    # Pre-populate sample entries
    learners = [f"learner_{i:04d}" for i in range(30)]
    for i, lid in enumerate(learners):
        store.upsert_score("cohort", cohort_id, epoch, lid, score=100 + i * 10,
                           first_reached_at="2026-10-12T10:00:00Z",
                           updated_at="2026-10-12T10:00:00Z",
                           watermark_offset=i + 1)

    thresholds = LatencyThresholds(
        update_p99_ms=50.0,
        topk_p99_ms=50.0,
        surrounding_p99_ms=50.0,
        rank_p99_ms=50.0,
    )

    result = measure_leaderboard_latency(
        service=service,
        store=store,
        epoch=epoch,
        cohort_id=cohort_id,
        sample_learners=learners,
        num_query_samples=20,
        thresholds=thresholds,
    )

    assert result.passed is True
    assert result.violations == []
    assert result.topk_latency.p99 < 50.0
    assert result.rank_latency.p99 < 50.0

    store.close()


def test_latency_measurement_reports_violations_on_strict_threshold(tmp_path):
    db_path = str(tmp_path / "latency_strict.db")
    store = SQLiteLeaderboardStore(db_path)
    service = LeaderboardService(store)
    epoch = "2026-W42"
    cohort_id = "cohort_alpha"

    learners = [f"learner_{i:04d}" for i in range(10)]
    for i, lid in enumerate(learners):
        store.upsert_score("cohort", cohort_id, epoch, lid, score=100,
                           first_reached_at="2026-10-12T10:00:00Z",
                           updated_at="2026-10-12T10:00:00Z",
                           watermark_offset=i + 1)

    # Impossibly tight threshold (0.000001 ms) to verify violation reporting
    impossible_thresholds = LatencyThresholds(update_p99_ms=0.000001)

    result = measure_leaderboard_latency(
        service=service,
        store=store,
        epoch=epoch,
        cohort_id=cohort_id,
        sample_learners=learners,
        num_query_samples=10,
        thresholds=impossible_thresholds,
    )

    assert result.passed is False
    assert len(result.violations) > 0
    assert any("Update P99" in v for v in result.violations)

    store.close()
