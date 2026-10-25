"""Tests for Cohort Lifecycle constraints and Event-Stream Reconciliation (Week 4 LB-5)."""

from datetime import datetime, timezone
import uuid
import pytest

from src.leaderboard.cohorts import (
    CohortCapacityOverflowError,
    CohortIncompatibleEpochError,
    CohortIncompatibleStrategyError,
    CohortLifecycleService,
    CohortMembership,
    CohortSealedError,
    merge_undersized_cohorts,
)
from src.leaderboard.models import CohortDefinition
from src.leaderboard.projection import LeaderboardProjection
from src.leaderboard.reconciliation import LeaderboardReconciler
from src.leaderboard.service import LeaderboardService
from src.leaderboard.store import InMemoryLeaderboardStore, SQLiteLeaderboardStore
from src.state_machine.learner import Learner


@pytest.fixture(params=["sqlite", "in_memory"])
def store(request, tmp_path):
    if request.param == "sqlite":
        db_path = str(tmp_path / "cohort_recon_test.db")
        s = SQLiteLeaderboardStore(db_path)
    else:
        s = InMemoryLeaderboardStore()
    yield s
    s.close()


def test_assign_cohort_enforces_lifecycle_and_refuses_sealed(store):
    """1. assign_cohort admits forming cohorts, but rejects arrivals after seal with CohortSealedError."""
    epoch = "2026-W42"
    cohort_id = "cohort_alpha"
    projection = LeaderboardProjection(store)

    # Cohort starts as forming
    c_def = CohortDefinition(cohort_id, strategy="activity_volume", epoch=epoch, status="forming")
    projection.register_cohort(c_def)

    # Forming cohort admits member L1
    projection.assign_cohort("L1", cohort_id, epoch, "2026-10-12T10:00:00Z", watermark_offset=1)
    entry = store.get_entry("cohort", cohort_id, epoch, "L1")
    assert entry is not None
    assert store.get_cohort_member_count(cohort_id, epoch) == 1

    # Transition cohort forming -> active -> sealed
    c_active = projection.transition_cohort(cohort_id, "active", epoch=epoch)
    assert c_active.status == "active"
    c_sealed = projection.transition_cohort(cohort_id, "sealed", epoch=epoch)
    assert c_sealed.status == "sealed"

    # Mid-week arrival L2 attempts to join sealed cohort -> MUST be refused with CohortSealedError
    with pytest.raises(CohortSealedError) as exc_info:
        projection.assign_cohort("L2", cohort_id, epoch, "2026-10-12T11:00:00Z", watermark_offset=2)
    assert "sealed" in str(exc_info.value)

    # Verify L2 was not added to the sealed cohort
    assert store.get_entry("cohort", cohort_id, epoch, "L2") is None
    assert store.get_cohort_member_count(cohort_id, epoch) == 1

    # Also verify domain event dispatch path rejects arrivals to sealed cohorts
    domain_event = Learner.CohortAssigned(
        originator_id=uuid.uuid4(),
        originator_version=1,
        timestamp=datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc),
        cohort_id=cohort_id,
        membership_epoch=epoch,
        grouping_rule_version="v1",
        assigned_at="2026-10-12T12:00:00Z",
    )
    with pytest.raises(CohortSealedError):
        projection.apply(domain_event, offset=3)


def test_merge_undersized_cohorts_active_only_density_and_capacity():
    """2. merge_undersized_cohorts counts ONLY active members for density and capacity bounds."""
    ref_time = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
    epoch = "2026-W42"

    # Cohort 1: 400 active members + 200 inactive (>30d) members.
    # Total members = 600. But active members = 400 (< min_size=500 threshold).
    c1 = CohortDefinition("c1", strategy="activity_volume", epoch=epoch, min_size=500, max_size=5000)
    c1_members = [
        CohortMembership(
            subject_id=f"c1_act_{i}",
            cohort_id="c1",
            epoch=epoch,
            assigned_at="2026-10-01T10:00:00Z",
            status="active",
            last_active_at="2026-10-15T10:00:00Z",
        )
        for i in range(400)
    ] + [
        CohortMembership(
            subject_id=f"c1_inact_{i}",
            cohort_id="c1",
            epoch=epoch,
            assigned_at="2026-10-01T10:00:00Z",
            status="active",
            last_active_at="2026-08-01T10:00:00Z",  # >30 days inactive
        )
        for i in range(200)
    ]

    # Cohort 2 (target): 4,800 active members + 200 inactive (>30d) members.
    # Total members = 5,000. Active members = 4,800 (capacity remaining = 5,000 - 4,800 = 200 active slots).
    c2 = CohortDefinition("c2", strategy="activity_volume", epoch=epoch, min_size=500, max_size=5000)
    c2_members = [
        CohortMembership(
            subject_id=f"c2_act_{i}",
            cohort_id="c2",
            epoch=epoch,
            assigned_at="2026-10-01T10:00:00Z",
            status="active",
            last_active_at="2026-10-15T10:00:00Z",
        )
        for i in range(4800)
    ] + [
        CohortMembership(
            subject_id=f"c2_inact_{i}",
            cohort_id="c2",
            epoch=epoch,
            assigned_at="2026-10-01T10:00:00Z",
            status="active",
            last_active_at="2026-08-01T10:00:00Z",
        )
        for i in range(200)
    ]

    memberships = {"c1": c1_members, "c2": c2_members}

    # Case A: c1 has only 400 active members, so it is detected as undersized (<500).
    # But merging 400 active into c2 (which has 4,800 active) would require 4,800 + 400 = 5,200 > 5,000 active slots -> overflow!
    with pytest.raises(CohortCapacityOverflowError):
        merge_undersized_cohorts([c1, c2], memberships, reference_time=ref_time)

    # Case B: c1 has only 150 active members (< 500 threshold) and 350 inactives.
    # 4,800 + 150 = 4,950 <= 5,000 active capacity bound.
    # Total members in c2 will be 4,800 + 200 + 150 + 350 = 5,500 > 5,000,
    # but ACTIVE capacity is 4,950 <= 5,000, which MUST SUCCEED!
    c1_members_small = [
        CohortMembership(
            subject_id=f"c1_act_{i}",
            cohort_id="c1",
            epoch=epoch,
            assigned_at="2026-10-01T10:00:00Z",
            status="active",
            last_active_at="2026-10-15T10:00:00Z",
        )
        for i in range(150)
    ] + [
        CohortMembership(
            subject_id=f"c1_inact_{i}",
            cohort_id="c1",
            epoch=epoch,
            assigned_at="2026-10-01T10:00:00Z",
            status="active",
            last_active_at="2026-08-01T10:00:00Z",
        )
        for i in range(350)
    ]
    memberships_small = {"c1": c1_members_small, "c2": c2_members}
    res_cohorts, res_members = merge_undersized_cohorts(
        [c1, c2], memberships_small, reference_time=ref_time
    )

    # c1 was undersized based on active count and merged into c2
    assert len(res_cohorts) == 1
    assert res_cohorts[0].cohort_id == "c2"
    assert len(res_members["c1"]) == 0
    # All members were transferred into c2
    assert len(res_members["c2"]) == len(c2_members) + len(c1_members_small)


def test_merge_undersized_cohorts_incompatible_strategy_and_epoch():
    """3. merge_undersized_cohorts raises clear errors on incompatible strategy or epoch."""
    # Incompatible strategy
    c_vol = CohortDefinition("c_vol", strategy="activity_volume", epoch="2026-W42")
    c_mod = CohortDefinition("c_mod", strategy="course_module", epoch="2026-W42")

    with pytest.raises(CohortIncompatibleStrategyError) as exc_strat:
        merge_undersized_cohorts([c_vol, c_mod], {"c_vol": [], "c_mod": []})
    assert "strategy" in str(exc_strat.value).lower() or "strategies" in str(exc_strat.value).lower()

    # Incompatible epoch
    c_w41 = CohortDefinition("c_w41", strategy="activity_volume", epoch="2026-W41")
    c_w42 = CohortDefinition("c_w42", strategy="activity_volume", epoch="2026-W42")

    with pytest.raises(CohortIncompatibleEpochError) as exc_epoch:
        merge_undersized_cohorts([c_w41, c_w42], {"c_w41": [], "c_w42": []})
    assert "epoch" in str(exc_epoch.value).lower() or "epochs" in str(exc_epoch.value).lower()


def test_reconciliation_rebuilds_from_stream_and_fixes_drift(store):
    """4. Canonical state is rebuilt from event stream up to pinned offset, detecting and fixing drifted rows."""
    epoch = "2026-W42"
    cohort_id = "cohort_main"
    projection = LeaderboardProjection(store)
    service = LeaderboardService(store)
    reconciler = LeaderboardReconciler(store)

    # Register forming cohort and assign L1, L2
    projection.assign_cohort("L1", cohort_id, epoch, "2026-10-12T08:00:00Z", watermark_offset=1)
    projection.assign_cohort("L2", cohort_id, epoch, "2026-10-12T08:00:00Z", watermark_offset=2)

    # Real XP awards recorded in store's event log:
    # L1: 100 XP at 09:00:00 (offset 10), 100 XP at 09:15:00 (offset 20) -> Canonical: 200 XP, first_reached=09:15:00
    # L2: 150 XP at 09:30:00 (offset 30) -> Canonical: 150 XP, first_reached=09:30:00
    projection.record_xp("L1", 100, "2026-10-12T09:00:00Z", epoch=epoch, watermark_offset=10)
    projection.record_xp("L1", 100, "2026-10-12T09:15:00Z", epoch=epoch, watermark_offset=20)
    projection.record_xp("L2", 150, "2026-10-12T09:30:00Z", epoch=epoch, watermark_offset=30)

    # Intentionally introduce drift in read model:
    # 1. Corrupt L1 score down to 50 (simulate dropped event in projection)
    # 2. Insert phantom entry L_phantom not in stream
    store.upsert_score("cohort", cohort_id, epoch, "L1", 50, "2026-10-12T09:00:00Z", "2026-10-12T09:00:00Z", watermark_offset=20)
    store.upsert_score("cohort", cohort_id, epoch, "L_phantom", 80, "2026-10-12T09:10:00Z", "2026-10-12T09:10:00Z", watermark_offset=25)

    # Verify drift before reconciliation
    rank_l1_before = service.get_learner_rank("cohort", cohort_id, epoch, "L1")
    assert rank_l1_before.score == 50
    assert service.get_learner_rank("cohort", cohort_id, epoch, "L_phantom") is not None

    # Run reconciliation without passing canonical_state mapping: canonical state is rebuilt from stream!
    audit = reconciler.reconcile_scope(
        scope_type="cohort",
        scope_id=cohort_id,
        epoch=epoch,
        pinned_offset=30,
        audited_at="2026-10-12T10:00:00Z",
    )

    # Assert differential corrections and audit details
    assert audit.pinned_offset == 30
    assert audit.divergence_count == 2  # L1 updated, L_phantom deleted
    assert audit.updated_count == 1
    assert audit.deleted_count == 1
    assert "L1" in audit.corrected_subject_ids
    assert "L_phantom" in audit.corrected_subject_ids
    assert "L2" not in audit.corrected_subject_ids  # untouched

    # Verify corrected read model
    rank_l1_after = service.get_learner_rank("cohort", cohort_id, epoch, "L1")
    assert rank_l1_after.score == 200
    assert rank_l1_after.first_reached_at == "2026-10-12T09:15:00Z"
    assert service.get_learner_rank("cohort", cohort_id, epoch, "L_phantom") is None

    # Persisted audit verification
    persisted_audits = store.get_reconciliation_audits()
    assert len(persisted_audits) >= 1
    assert persisted_audits[-1].pinned_offset == 30


def test_reconciliation_ignores_rows_newer_than_pinned_offset(store):
    """5. Reconciliation strictly ignores rows newer than pinned_offset."""
    epoch = "2026-W42"
    cohort_id = "cohort_pinned"
    projection = LeaderboardProjection(store)
    service = LeaderboardService(store)
    reconciler = LeaderboardReconciler(store)

    projection.assign_cohort("L_old", cohort_id, epoch, "2026-10-12T08:00:00Z", watermark_offset=1)
    projection.assign_cohort("L_new", cohort_id, epoch, "2026-10-12T08:00:00Z", watermark_offset=2)

    # Event 1: L_old earns 100 XP at offset 10 (at/before pin 20)
    projection.record_xp("L_old", 100, "2026-10-12T08:30:00Z", epoch=epoch, watermark_offset=10)

    # Event 2: L_new earns 500 XP at offset 35 (newer than pin 20)
    projection.record_xp("L_new", 500, "2026-10-12T09:30:00Z", epoch=epoch, watermark_offset=35)

    # Corrupt L_old projection row to 50 at offset 10
    store.upsert_score("cohort", cohort_id, epoch, "L_old", 50, "2026-10-12T08:30:00Z", "2026-10-12T08:30:00Z", watermark_offset=10)

    # Also insert a phantom row with watermark_offset=40 (> pin 20)
    store.upsert_score("cohort", cohort_id, epoch, "L_future_phantom", 99, "2026-10-12T09:40:00Z", "2026-10-12T09:40:00Z", watermark_offset=40)

    # Run reconciliation pinned at offset 20
    audit = reconciler.reconcile_scope(
        scope_type="cohort",
        scope_id=cohort_id,
        epoch=epoch,
        pinned_offset=20,
        audited_at="2026-10-12T10:00:00Z",
    )

    # L_old (offset 10 <= 20) is corrected
    assert "L_old" in audit.corrected_subject_ids
    assert service.get_learner_rank("cohort", cohort_id, epoch, "L_old").score == 100

    # L_new (offset 35 > 20) MUST BE LEFT ALONE
    assert "L_new" not in audit.corrected_subject_ids
    assert service.get_learner_rank("cohort", cohort_id, epoch, "L_new").score == 500

    # L_future_phantom (offset 40 > 20) MUST BE LEFT ALONE (not deleted)
    assert "L_future_phantom" not in audit.corrected_subject_ids
    assert service.get_learner_rank("cohort", cohort_id, epoch, "L_future_phantom") is not None


def test_reconciliation_audits_same_millisecond_collision_safe(tmp_path):
    """6. Two audits executing in the same millisecond receive distinct, collision-safe IDs."""
    db_path = str(tmp_path / "recon_collision.db")
    store = SQLiteLeaderboardStore(db_path)
    reconciler = LeaderboardReconciler(store)
    epoch = "2026-W42"

    same_ms_timestamp = "2026-10-12T12:00:00.123456Z"

    # Run audit 1 and audit 2 back-to-back with the exact same millisecond timestamp
    audit1 = reconciler.reconcile_scope(
        scope_type="global",
        scope_id="global",
        epoch=epoch,
        pinned_offset=10,
        audited_at=same_ms_timestamp,
    )
    audit2 = reconciler.reconcile_scope(
        scope_type="global",
        scope_id="global",
        epoch=epoch,
        pinned_offset=10,
        audited_at=same_ms_timestamp,
    )

    # IDs must be non-empty, distinct, and collision-free
    assert audit1.audit_id != ""
    assert audit2.audit_id != ""
    assert audit1.audit_id != audit2.audit_id

    # Both must be persisted into SQLite table without UNIQUE constraint failure
    audits = store.get_reconciliation_audits()
    audit_ids = [a.audit_id for a in audits]
    assert audit1.audit_id in audit_ids
    assert audit2.audit_id in audit_ids
    assert len(audit_ids) == 2

    store.close()
