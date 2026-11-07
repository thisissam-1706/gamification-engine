"""M3 cohort admission edge cases (sequential and rollback behaviour).

Concurrent behaviour is covered in test_cohort_capacity_race.py; helpers are reused from there.
"""

import pytest

from src.leaderboard.cohorts import CohortCapacityOverflowError
from src.leaderboard.models import CohortDefinition
from src.leaderboard.projection import LeaderboardProjection
from src.leaderboard.store import SQLiteLeaderboardStore
from tests.leaderboard.test_cohort_capacity_race import (
    ASSIGNED_AT,
    COHORT,
    EPOCH,
    _cohort,
    _members,
    _seeded_db,
    _snapshot,
)

OVERFLOW_COHORT = "cohort_overflow"


def _open(db):
    store = SQLiteLeaderboardStore(db)
    return store, LeaderboardProjection(store)


def _assign_with_overflow(proj, subject, offset, primary=COHORT, overflow=OVERFLOW_COHORT):
    """Try the primary cohort; on a capacity refusal, use the overflow cohort. Returns the cohort used."""
    try:
        proj.assign_cohort(subject, primary, EPOCH, ASSIGNED_AT, watermark_offset=offset)
        return primary
    except CohortCapacityOverflowError:
        proj.assign_cohort(subject, overflow, EPOCH, ASSIGNED_AT, watermark_offset=offset)
        return overflow


def test_a_learner_refused_at_exact_capacity_can_join_another_cohort(tmp_path):
    db = _seeded_db(tmp_path, existing=("m1", "m2", "m3"))  # exactly full (3/3)
    store, proj = _open(db)
    proj.register_cohort(_cohort(OVERFLOW_COHORT, max_size=3))

    with pytest.raises(CohortCapacityOverflowError):
        proj.assign_cohort("late", COHORT, EPOCH, ASSIGNED_AT, watermark_offset=10)
    assert store.get_member_cohorts(EPOCH, "late") == []

    proj.assign_cohort("late", OVERFLOW_COHORT, EPOCH, ASSIGNED_AT, watermark_offset=11)
    assert store.get_member_cohorts(EPOCH, "late") == [OVERFLOW_COHORT]
    assert _members(db) == {"m1", "m2", "m3"}
    assert _members(db, OVERFLOW_COHORT) == {"late"}
    store.connection.close()


def test_a_learner_refused_at_exact_capacity_can_start_a_new_cohort(tmp_path):
    db = _seeded_db(tmp_path, existing=("m1", "m2", "m3"))
    store, proj = _open(db)
    assert store.get_cohort_definition("cohort_brand_new") is None

    with pytest.raises(CohortCapacityOverflowError):
        proj.assign_cohort("late", COHORT, EPOCH, ASSIGNED_AT, watermark_offset=10)
    proj.assign_cohort("late", "cohort_brand_new", EPOCH, ASSIGNED_AT, watermark_offset=11)

    assert store.get_cohort_definition("cohort_brand_new") is not None
    assert store.get_member_cohorts(EPOCH, "late") == ["cohort_brand_new"]
    assert store.get_cohort_member_count(COHORT, EPOCH) == 3
    store.connection.close()


def test_capacity_plus_two_learners_assigned_sequentially_fill_then_overflow(tmp_path):
    db = str(tmp_path / "sequential.db")
    store, proj = _open(db)
    proj.register_cohort(_cohort(COHORT, max_size=3))
    proj.register_cohort(_cohort(OVERFLOW_COHORT, max_size=3))

    placed = {f"s{i}": _assign_with_overflow(proj, f"s{i}", offset=i + 1) for i in range(5)}

    assert [placed[f"s{i}"] for i in range(5)] == [COHORT] * 3 + [OVERFLOW_COHORT] * 2
    assert store.get_cohort_member_count(COHORT, EPOCH) == 3
    assert store.get_cohort_member_count(OVERFLOW_COHORT, EPOCH) == 2
    for subject, cohort in placed.items():
        assert store.get_member_cohorts(EPOCH, subject) == [cohort]
    store.connection.close()


@pytest.mark.parametrize("fresh_projection", [False, True])
def test_reassigning_a_learner_does_not_duplicate_or_consume_a_slot(tmp_path, fresh_projection):
    db = _seeded_db(tmp_path, existing=("m1", "m2"))  # 2 of 3 seats taken
    store, proj = _open(db)

    for offset in (10, 11):
        if fresh_projection:  # no in-memory cache: the old cohort comes from the database
            proj = LeaderboardProjection(store)
        proj.assign_cohort("m1", COHORT, EPOCH, "2026-10-13T08:00:00Z", watermark_offset=offset)

    assert store.get_cohort_member_count(COHORT, EPOCH) == 2
    assert store.get_member_cohorts(EPOCH, "m1") == [COHORT]
    # The slot is still free for someone new, and only one.
    proj.assign_cohort("new1", COHORT, EPOCH, ASSIGNED_AT, watermark_offset=12)
    with pytest.raises(CohortCapacityOverflowError):
        proj.assign_cohort("new2", COHORT, EPOCH, ASSIGNED_AT, watermark_offset=13)
    assert store.get_cohort_member_count(COHORT, EPOCH) == 3
    store.connection.close()


def _fail_upsert(store):
    def boom(**kwargs):
        raise RuntimeError("injected failure inside the admission transaction")

    store.upsert_score = boom


def test_failure_inside_the_transaction_rolls_back_a_move(tmp_path):
    db = _seeded_db(tmp_path, existing=("m1",))
    store, proj = _open(db)
    proj.register_cohort(_cohort("cohort_B", max_size=3))
    proj.assign_cohort("mover", COHORT, EPOCH, ASSIGNED_AT, watermark_offset=5)
    rows_before = _snapshot(store)

    _fail_upsert(store)  # fires after remove_cohort_memberships has already deleted the old row
    with pytest.raises(RuntimeError, match="injected failure"):
        proj.assign_cohort("mover", "cohort_B", EPOCH, "2026-10-13T08:00:00Z", watermark_offset=6)

    assert not store.connection.in_transaction
    assert _snapshot(store) == rows_before
    assert store.get_member_cohorts(EPOCH, "mover") == [COHORT]
    assert store.get_entry("cohort", "cohort_B", EPOCH, "mover") is None
    store.connection.close()


def test_failure_inside_the_transaction_leaves_no_cohort_definition_behind(tmp_path):
    db = str(tmp_path / "rollback_definition.db")
    store, proj = _open(db)
    assert store.get_cohort_definition("cohort_ghost") is None

    _fail_upsert(store)  # the definition is created inside the transaction before the failing write
    with pytest.raises(RuntimeError, match="injected failure"):
        proj.assign_cohort("g1", "cohort_ghost", EPOCH, ASSIGNED_AT, watermark_offset=1)

    assert not store.connection.in_transaction
    assert store.get_cohort_definition("cohort_ghost") is None
    assert store.get_member_cohorts(EPOCH, "g1") == []
    assert _snapshot(store) == []
    store.connection.close()


def test_failure_inside_the_transaction_does_not_advance_a_forming_cohort(tmp_path):
    db = str(tmp_path / "rollback_forming.db")
    store, proj = _open(db)
    forming = CohortDefinition(
        cohort_id="cohort_F", strategy="activity_volume", epoch=EPOCH,
        min_size=1, max_size=3, status="forming",
    )
    proj.register_cohort(forming)

    _fail_upsert(store)  # would otherwise follow the forming -> active save
    with pytest.raises(RuntimeError, match="injected failure"):
        proj.assign_cohort("f1", "cohort_F", EPOCH, ASSIGNED_AT, watermark_offset=1)

    assert store.get_cohort_definition("cohort_F") == forming
    assert _snapshot(store) == []
    store.connection.close()

    # A fresh projection on the same file is not poisoned by the failed attempt.
    reopened, fresh = _open(db)
    fresh.assign_cohort("f1", "cohort_F", EPOCH, ASSIGNED_AT, watermark_offset=2)
    assert reopened.get_cohort_definition("cohort_F").status == "active"
    assert reopened.get_member_cohorts(EPOCH, "f1") == ["cohort_F"]
    reopened.connection.close()
