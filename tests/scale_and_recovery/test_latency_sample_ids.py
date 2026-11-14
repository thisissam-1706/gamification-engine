"""The latency step must query with the ids the leaderboard is keyed by (aggregate UUID strings).

Workload ids such as "learner_0000" are not in the leaderboard, so get_learner_rank returns None for them
and the rank latency measures nothing. ``latency_sample_ids`` in the benchmark script maps workload ids to
the UUID strings the rebuild harness gives the learners.

Ranks are checked BEFORE ``measure_leaderboard_latency`` runs: its update step upserts every sampled id into the
cohort, which would make any id rankable afterwards and hide a wrong mapping. A learner is a member of only one
cohort, so a UUID id outside the measured cohort is legitimately None there (see the audit file).
"""

from collections import Counter

from src.scale_and_recovery.latency import measure_leaderboard_latency
from src.scale_and_recovery.rebuild import FullStateRebuildHarness
from src.scale_and_recovery.workload import WorkloadProfile, generate_synthetic_workload


def test_uuid_sample_ids_are_ranked_and_workload_ids_are_not(tmp_path):
    from scripts.run_scale_benchmark import latency_sample_ids

    profile = WorkloadProfile(population_size=8, events_per_learner=5, seed=42)
    workload = generate_synthetic_workload(profile)
    harness = FullStateRebuildHarness(str(tmp_path / "events.db"), str(tmp_path / "board.db"), epoch=profile.epoch)
    harness.initialize_live_system()
    try:
        harness.ingest_workload(workload)
        cohort_counts = Counter(workload.cohort_assignments.values())
        cohort_id = max(sorted(cohort_counts), key=cohort_counts.get)
        sample_ids = latency_sample_ids(workload)
        assert len(sample_ids) == 8

        def rank(learner_id):
            return harness.service.get_learner_rank("cohort", cohort_id, profile.epoch, learner_id)

        # Before measuring: every sampled id that belongs to the measured cohort is ranked ...
        members = [uid for uid, lid in zip(sample_ids, workload.learner_ids)
                   if workload.cohort_assignments[lid] == cohort_id]
        assert len(members) == cohort_counts[cohort_id] > 0
        for learner_id in members:
            assert rank(learner_id) is not None, learner_id
        # ... and the old workload ids are ranked for nobody (the bug, documented).
        for old_id in workload.learner_ids:
            assert rank(old_id) is None, old_id

        result = measure_leaderboard_latency(
            service=harness.service, store=harness.store, epoch=profile.epoch, cohort_id=cohort_id,
            sample_learners=sample_ids, num_query_samples=8,
        )
        assert result.rank_latency.count == 8
    finally:
        harness.close()
