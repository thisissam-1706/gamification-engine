"""The latency step must sample only members of the cohort it measures.

A learner belongs to one cohort, so sampling every learner against the largest cohort made most rank and
surrounding queries misses (and the update step then upserted the non-members into that cohort).
``latency_cohort_and_samples`` in the benchmark script picks the cohort the benchmark measures and the UUID ids
of its members, cycling them when there are fewer members than queries.
"""

import pytest

from src.scale_and_recovery.rebuild import FullStateRebuildHarness
from src.scale_and_recovery.workload import WorkloadProfile, generate_synthetic_workload


@pytest.fixture
def ingested(tmp_path):
    profile = WorkloadProfile(population_size=20, events_per_learner=5, seed=42)
    workload = generate_synthetic_workload(profile)
    harness = FullStateRebuildHarness(str(tmp_path / "events.db"), str(tmp_path / "board.db"), epoch=profile.epoch)
    harness.initialize_live_system()
    try:
        harness.ingest_workload(workload)
        yield harness, workload, profile
    finally:
        harness.close()


def test_every_sampled_id_is_a_member_of_the_chosen_cohort(ingested):
    from scripts.run_scale_benchmark import latency_cohort_and_samples

    harness, workload, profile = ingested

    cohort_id, sample_ids = latency_cohort_and_samples(workload, num_query_samples=30)

    members = sum(1 for c in workload.cohort_assignments.values() if c == cohort_id)
    assert 0 < members < 30  # more queries than members: the members are cycled
    assert len(sample_ids) == 30
    assert len(set(sample_ids)) == members
    for learner_id in sample_ids:
        assert harness.store.get_member_cohort(profile.epoch, learner_id) == cohort_id, learner_id


def test_fewer_queries_than_members_takes_the_first_members(ingested):
    from scripts.run_scale_benchmark import latency_cohort_and_samples

    harness, workload, profile = ingested

    cohort_id, sample_ids = latency_cohort_and_samples(workload, num_query_samples=2)

    assert len(sample_ids) == 2 and len(set(sample_ids)) == 2
    for learner_id in sample_ids:
        assert harness.store.get_member_cohort(profile.epoch, learner_id) == cohort_id


def test_the_chosen_cohort_is_the_largest_one_with_ties_to_the_lowest_id(ingested):
    from scripts.run_scale_benchmark import latency_cohort_and_samples

    _, workload, _ = ingested
    sizes = {}
    for cohort in workload.cohort_assignments.values():
        sizes[cohort] = sizes.get(cohort, 0) + 1
    expected = sorted(sizes, key=lambda c: (-sizes[c], c))[0]

    cohort_id, _ = latency_cohort_and_samples(workload, num_query_samples=5)

    assert cohort_id == expected
