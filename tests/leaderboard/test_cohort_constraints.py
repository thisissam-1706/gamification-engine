"""Tests for cohort constraints, deterministic assignment, inactivity pruning, and merges."""

from datetime import datetime, timezone, timedelta
import pytest

from src.leaderboard.cohorts import (
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
