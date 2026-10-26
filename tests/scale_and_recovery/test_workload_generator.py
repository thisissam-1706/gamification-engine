"""Tests for deterministic synthetic workload generation (M8)."""

from datetime import datetime, timezone

from src.scale_and_recovery.workload import WorkloadProfile, generate_synthetic_workload


def test_workload_generator_identical_output_for_same_seed():
    p1 = WorkloadProfile(population_size=10, events_per_learner=5, seed=42)
    p2 = WorkloadProfile(population_size=10, events_per_learner=5, seed=42)

    w1 = generate_synthetic_workload(p1)
    w2 = generate_synthetic_workload(p2)

    assert w1.total_events == w2.total_events
    assert w1.clean_events_count == w2.clean_events_count
    assert w1.duplicate_events_count == w2.duplicate_events_count
    assert w1.late_events_count == w2.late_events_count
    assert w1.cohort_assignments == w2.cohort_assignments

    for e1, e2 in zip(w1.events, w2.events):
        assert e1 == e2


def test_workload_generator_different_output_for_different_seeds():
    p1 = WorkloadProfile(population_size=10, events_per_learner=5, seed=42)
    p2 = WorkloadProfile(population_size=10, events_per_learner=5, seed=43)

    w1 = generate_synthetic_workload(p1)
    w2 = generate_synthetic_workload(p2)

    # Different seeds yield different completion IDs / timing
    assert [e.completion_id for e in w1.events] != [e.completion_id for e in w2.events]


def test_workload_generator_noise_ratios_are_bounded():
    p = WorkloadProfile(
        population_size=50,
        events_per_learner=20,
        duplicate_rate=0.08,
        late_rate=0.06,
        seed=123,
    )
    w = generate_synthetic_workload(p)

    expected_clean = 50 * 20
    assert w.clean_events_count == expected_clean

    actual_dup_rate = w.duplicate_events_count / expected_clean
    actual_late_rate = w.late_events_count / expected_clean

    assert abs(actual_dup_rate - 0.08) < 0.01
    assert abs(actual_late_rate - 0.06) < 0.01


def test_workload_event_timestamps_are_aware_utc_iso():
    p = WorkloadProfile(population_size=5, events_per_learner=3, seed=99)
    w = generate_synthetic_workload(p)

    for event in w.events:
        assert event.occurred_at.endswith("Z")
        dt = datetime.fromisoformat(event.occurred_at.replace("Z", "+00:00"))
        assert dt.tzinfo is not None
