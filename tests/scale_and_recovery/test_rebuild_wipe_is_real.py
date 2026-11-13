"""AT-15 wipe realism: the simulated wipe must really destroy state before replay.

These tests pin the contract that simulate_projection_wipe() empties the leaderboard
read model AND discards the live LearnerApplication (reopening it), so a verification
that skips replay cannot pass.
"""

import hashlib
import json

from src.scale_and_recovery.rebuild import FullStateRebuildHarness
from src.scale_and_recovery.workload import WorkloadProfile, generate_synthetic_workload

EPOCH = "2026-W42"


def _checksum(snapshot) -> str:
    payload = json.dumps({k: v.to_dict() for k, v in sorted(snapshot.items())}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _ingested_harness(tmp_path):
    profile = WorkloadProfile(population_size=8, cohort_count=2, events_per_learner=6, seed=42)
    workload = generate_synthetic_workload(profile)
    harness = FullStateRebuildHarness(
        str(tmp_path / "events.db"), str(tmp_path / "leaderboard.db"), epoch=EPOCH
    )
    harness.initialize_live_system()
    learner_uuids = harness.ingest_workload(workload)
    return harness, workload, learner_uuids


def test_wipe_empties_leaderboard_before_replay(tmp_path):
    harness, workload, learner_uuids = _ingested_harness(tmp_path)
    try:
        pre = harness.capture_snapshot(workload, learner_uuids)
        assert any(s.cohort_rank is not None for s in pre.values())

        harness.simulate_projection_wipe()
        post = harness.capture_snapshot(workload, learner_uuids)

        assert len(post) == len(workload.learner_ids)
        for lid, snap in post.items():
            assert snap.cohort_rank is None, lid
            assert snap.global_rank is None, lid
            assert snap.cohort_score == 0, lid
    finally:
        harness.close()


def test_wipe_reopens_application(tmp_path):
    harness, workload, learner_uuids = _ingested_harness(tmp_path)
    try:
        old_app = harness.app
        assert old_app is not None
        some_uuid = next(iter(learner_uuids.values()))

        harness.simulate_projection_wipe()

        assert harness.app is not None
        assert harness.app is not old_app

        # The old application must be closed / unusable.
        try:
            list(old_app.events.get(some_uuid))
        except Exception:
            unusable = True
        else:
            unusable = False
        assert unusable, "old LearnerApplication is still usable after the wipe"
    finally:
        harness.close()


def test_skipping_replay_fails_verification(tmp_path):
    harness, workload, learner_uuids = _ingested_harness(tmp_path)
    try:
        pre = harness.capture_snapshot(workload, learner_uuids)
        harness.simulate_projection_wipe()
        post = harness.capture_snapshot(workload, learner_uuids)

        assert _checksum(pre) != _checksum(post)
    finally:
        harness.close()


def test_corrupted_score_after_replay_is_reported_as_mismatch(tmp_path):
    profile = WorkloadProfile(population_size=8, cohort_count=2, events_per_learner=6, seed=42)
    workload = generate_synthetic_workload(profile)
    harness = FullStateRebuildHarness(
        str(tmp_path / "events.db"), str(tmp_path / "leaderboard.db"), epoch=EPOCH
    )
    victim = workload.learner_ids[0]
    victim_uuid = {}
    original_ingest = harness.ingest_workload
    original_rebuild = harness.execute_full_rebuild

    def ingest_and_remember(wl):
        uuids = original_ingest(wl)
        victim_uuid["id"] = str(uuids[victim])
        return uuids

    def rebuild_then_corrupt(wl, uuids):
        # Cohorts come from the cohort builder during ingestion, so they are only known now.
        victim_cohort = wl.cohort_assignments[victim]
        original_rebuild(wl, uuids)
        row = harness.store.get_rank("cohort", victim_cohort, EPOCH, victim_uuid["id"])
        assert row is not None
        harness.store.upsert_score(
            "cohort", victim_cohort, EPOCH, victim_uuid["id"],
            score=row.score + 1000,
            first_reached_at=row.first_reached_at,
            updated_at=row.first_reached_at,
            watermark_offset=10**9,
        )
        # Re-capture so the post snapshot sees the corrupted read model.
        return harness.capture_snapshot(wl, uuids, source="replay"), 1.0, 1

    harness.ingest_workload = ingest_and_remember
    harness.execute_full_rebuild = rebuild_then_corrupt

    result = harness.run_rebuild_verification(workload)

    assert result.bit_identical is False
    assert result.pre_checksum != result.post_checksum
    assert any(victim in m for m in result.mismatches)
    # Only the corrupted learner (and possibly peers whose rank shifted) are reported;
    # the corrupted learner must be among them with a different cohort_score.
    assert len([m for m in result.mismatches if f"State mismatch for {victim}" in m]) == 1


def test_result_reports_domain_event_count_and_notification_count(tmp_path):
    profile = WorkloadProfile(population_size=8, cohort_count=2, events_per_learner=6, seed=42)
    workload = generate_synthetic_workload(profile)
    harness = FullStateRebuildHarness(
        str(tmp_path / "events.db"), str(tmp_path / "leaderboard.db"), epoch=EPOCH
    )
    result = harness.run_rebuild_verification(workload)

    assert result.bit_identical is True
    assert result.domain_event_count > 0
    assert result.total_events > 0
    assert "domain_event_count" in result.to_dict()
