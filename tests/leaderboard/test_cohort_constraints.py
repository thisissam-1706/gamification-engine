"""Tests for cohort constraints, deterministic assignment, inactivity pruning, and merges."""

from datetime import datetime, timezone, timedelta
import pytest

from src.leaderboard.cohorts import (
    CohortCapacityOverflowError,
    CohortIncompatibleEpochError,
    assign_activity_volume_cohort,
    assign_module_cohort,
    get_calendar_week,
    is_inactive,
    merge_undersized_cohorts,
)
from src.leaderboard.models import CohortDefinition, CohortMembership


def test_calendar_week_calculation():
    # 2026-10-12 is Monday of Week 42, 2026
    dt = datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc)
    week_str = get_calendar_week(dt)
    assert week_str == "2026-W42"

    # From ISO string
    assert get_calendar_week("2026-01-01T00:00:00Z") == "2026-W01"


def test_deterministic_assignment_is_repeatable():
    epoch = "2026-W41"
    first = assign_activity_volume_cohort("L1", epoch, prior_week_lessons=5)
    second = assign_activity_volume_cohort("L1", epoch, prior_week_lessons=5)
    assert first == second

    first_mod = assign_module_cohort("L1", epoch, module_id="intro_py")
    second_mod = assign_module_cohort("L1", epoch, module_id="intro_py")
    assert first_mod == second_mod


def test_activity_tiers_separate_workloads():
    epoch = "2026-W41"
    casual = assign_activity_volume_cohort("L_same", epoch, prior_week_lessons=1)
    regular = assign_activity_volume_cohort("L_same", epoch, prior_week_lessons=5)
    intensive = assign_activity_volume_cohort("L_same", epoch, prior_week_lessons=15)

    assert "casual" in casual
    assert "regular" in regular
    assert "intensive" in intensive


def test_inactivity_30_day_drop_off():
    ref_time = datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc)

    # 10 days ago -> active
    recent_active = ref_time - timedelta(days=10)
    assert not is_inactive(recent_active, ref_time, days_threshold=30)

    # 31 days ago -> inactive
    old_active = ref_time - timedelta(days=31)
    assert is_inactive(old_active, ref_time, days_threshold=30)

    # Never active -> inactive
    assert is_inactive(None, ref_time, days_threshold=30)


def test_undersized_cohort_merge():
    """Cohorts under 500 members are merged to preserve competition density."""
    epoch = "2026-W41"
    c_small = CohortDefinition("c_small", "course_module", epoch, min_size=500)
    c_large = CohortDefinition("c_large", "course_module", epoch, min_size=500)

    # c_small has 10 members, c_large has 600 members
    small_members = [
        CohortMembership(f"u_small_{i}", "c_small", epoch, "2026-10-12T00:00:00Z")
        for i in range(10)
    ]
    large_members = [
        CohortMembership(f"u_large_{i}", "c_large", epoch, "2026-10-12T00:00:00Z")
        for i in range(600)
    ]

    memberships = {
        "c_small": small_members,
        "c_large": large_members,
    }

    viable_cohorts, merged_memberships = merge_undersized_cohorts(
        [c_small, c_large], memberships, min_size=500
    )

    # c_small should be merged into c_large
    assert len(viable_cohorts) == 1
    assert viable_cohorts[0].cohort_id == "c_large"
    assert len(merged_memberships["c_large"]) == 610


def test_merge_rejects_capacity_overflow():
    """Reviewer Finding 3: Target cohort with max_size=3 receiving a 4th member must raise overflow error."""
    epoch = "2026-W41"
    c_target = CohortDefinition("c_target", "activity_volume", epoch, min_size=2, max_size=3)
    c_small = CohortDefinition("c_small", "activity_volume", epoch, min_size=2, max_size=3)

    memberships = {
        "c_target": [
            CohortMembership(f"u_t_{i}", "c_target", epoch, "2026-10-12T00:00:00Z")
            for i in range(3)
        ],
        "c_small": [
            CohortMembership("u_overflow", "c_small", epoch, "2026-10-12T00:00:00Z")
        ],
    }

    with pytest.raises(CohortCapacityOverflowError):
        merge_undersized_cohorts([c_target, c_small], memberships, min_size=2)


def test_inactive_members_excluded_from_density_count():
    """Reviewer Finding 3: 30-day inactives must not artificially inflate active density."""
    epoch = "2026-W41"
    ref_time = datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc)
    old_active = (ref_time - timedelta(days=35)).isoformat()
    recent_active = (ref_time - timedelta(days=5)).isoformat()

    c_pseudo = CohortDefinition("c_pseudo", "course_module", epoch, min_size=500, max_size=5000)
    c_viable = CohortDefinition("c_viable", "course_module", epoch, min_size=500, max_size=5000)

    # c_pseudo has 500 members on paper, but 200 are inactive (>30d) -> only 300 active -> undersized!
    pseudo_members = [
        CohortMembership(f"u_inact_{i}", "c_pseudo", epoch, "2026-10-12T00:00:00Z", last_active_at=old_active)
        for i in range(200)
    ] + [
        CohortMembership(f"u_act_{i}", "c_pseudo", epoch, "2026-10-12T00:00:00Z", last_active_at=recent_active)
        for i in range(300)
    ]

    viable_members = [
        CohortMembership(f"u_viab_{i}", "c_viable", epoch, "2026-10-12T00:00:00Z", last_active_at=recent_active)
        for i in range(600)
    ]

    memberships = {"c_pseudo": pseudo_members, "c_viable": viable_members}

    result_cohorts, result_memberships = merge_undersized_cohorts(
        [c_pseudo, c_viable], memberships, min_size=500, reference_time=ref_time
    )

    # c_pseudo was recognized as undersized despite 500 raw members, and merged into c_viable
    assert len(result_cohorts) == 1
    assert result_cohorts[0].cohort_id == "c_viable"
    assert len(result_memberships["c_viable"]) == 1100


def test_merge_preserves_finalized_cohorts():
    """Reviewer Finding 3: Finalized cohort definitions and history must not be dropped when a merge runs."""
    epoch = "2026-W41"
    c_finalized = CohortDefinition("c_finalized", "course_module", epoch, min_size=500, status="finalized")
    c_small = CohortDefinition("c_small", "course_module", epoch, min_size=500, status="active")
    c_viable = CohortDefinition("c_viable", "course_module", epoch, min_size=500, status="active")

    memberships = {
        "c_finalized": [CohortMembership("u_fin_1", "c_finalized", epoch, "2026-10-01T00:00:00Z")],
        "c_small": [CohortMembership("u_sm_1", "c_small", epoch, "2026-10-12T00:00:00Z")],
        "c_viable": [
            CohortMembership(f"u_vi_{i}", "c_viable", epoch, "2026-10-12T00:00:00Z")
            for i in range(600)
        ],
    }

    result_cohorts, result_memberships = merge_undersized_cohorts(
        [c_finalized, c_small, c_viable], memberships, min_size=500
    )

    cohort_ids = {c.cohort_id for c in result_cohorts}
    assert "c_finalized" in cohort_ids
    assert "c_viable" in cohort_ids
    assert len(result_memberships["c_finalized"]) == 1


def test_cross_epoch_merge_rejected():
    """Reviewer Finding 3: Merges cannot mix different epochs."""
    c_w41 = CohortDefinition("c_w41", "course_module", "2026-W41", min_size=500)
    c_w42 = CohortDefinition("c_w42", "course_module", "2026-W42", min_size=500)

    with pytest.raises(CohortIncompatibleEpochError):
        merge_undersized_cohorts([c_w41, c_w42], {"c_w41": [], "c_w42": []})


def test_cohort_lifecycle_transitions_and_sealed_admissions():
    """Reviewer Finding 3: Strict lifecycle transitions and rejection of admissions to sealed cohorts."""
    from src.leaderboard.cohorts import CohortLifecycleService, CohortSealedError

    c_init = CohortDefinition("c_life", "course_module", "2026-W42", status="forming")

    # forming -> active
    c_active = CohortLifecycleService.transition(c_init, "active")
    assert c_active.status == "active"

    # active -> sealed
    c_sealed = CohortLifecycleService.transition(c_active, "sealed")
    assert c_sealed.status == "sealed"

    # sealed -> finalized
    c_finalized = CohortLifecycleService.transition(c_sealed, "finalized")
    assert c_finalized.status == "finalized"

    # Invalid jump: finalized cannot transition back to active
    with pytest.raises(ValueError):
        CohortLifecycleService.transition(c_finalized, "active")

    # Admission to sealed cohort must be rejected
    with pytest.raises(CohortSealedError):
        CohortLifecycleService.assert_can_admit_member(c_sealed, current_active_count=10)

