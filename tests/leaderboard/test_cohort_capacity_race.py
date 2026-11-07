"""M3 cohort capacity: two connections must not both take the last seat.

assign_cohort reads the member count and checks admission before it opens its
write transaction. Two connections to one SQLite file can therefore both see
max_size - 1 members, both pass the check, and both write.

Each worker thread opens its own store (a sqlite3 connection cannot be shared
across threads). The race is made deterministic by pausing both workers right
after they read the member count, on a shared barrier.
"""

import threading
import time
import uuid
from datetime import datetime, timezone

import pytest

from src.leaderboard.cohorts import CohortCapacityOverflowError, CohortSealedError
from src.leaderboard.models import CohortDefinition
from src.leaderboard.projection import LeaderboardProjection
from src.leaderboard.store import InMemoryLeaderboardStore, SQLiteLeaderboardStore
from src.state_machine.learner import Learner

EPOCH = "2026-W42"
COHORT = "cohort_full_soon"
ASSIGNED_AT = "2026-10-12T08:00:00Z"


def _cohort(cohort_id=COHORT, max_size=3):
    return CohortDefinition(
        cohort_id=cohort_id, strategy="activity_volume", epoch=EPOCH,
        min_size=1, max_size=max_size, status="active",
    )


def _seeded_db(tmp_path, existing=("m1", "m2"), max_size=3):
    """A SQLite file with an active cohort of `max_size` seats and `existing` members in it."""
    db = str(tmp_path / "cohort_race.db")
    store = SQLiteLeaderboardStore(db)
    proj = LeaderboardProjection(store)
    proj.register_cohort(_cohort(max_size=max_size))
    for offset, subject in enumerate(existing, start=1):
        proj.assign_cohort(subject, COHORT, EPOCH, ASSIGNED_AT, watermark_offset=offset)
    store.connection.close()
    return db


def _members(db, cohort_id=COHORT):
    store = SQLiteLeaderboardStore(db)
    try:
        rows = store.connection.execute(
            "SELECT DISTINCT subject_id FROM leaderboard_entries "
            "WHERE scope_type = 'cohort' AND scope_id = ? AND epoch = ?",
            (cohort_id, EPOCH),
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        store.connection.close()


def test_two_connections_cannot_both_fill_the_last_seat(tmp_path):
    db = _seeded_db(tmp_path)
    assert len(_members(db)) == 2

    barrier = threading.Barrier(2, timeout=1.0)
    results = {}

    def worker(subject, offset):
        store = SQLiteLeaderboardStore(db)
        original = store.get_cohort_member_count

        def count_then_wait(cohort_id, epoch):
            count = original(cohort_id, epoch)
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass
            return count

        store.get_cohort_member_count = count_then_wait
        proj = LeaderboardProjection(store)
        try:
            proj.assign_cohort(subject, COHORT, EPOCH, ASSIGNED_AT, watermark_offset=offset)
            results[subject] = "admitted"
        except CohortCapacityOverflowError:
            results[subject] = "refused"
        except Exception as exc:  # surface anything unexpected in the assertion below
            results[subject] = f"error: {exc!r}"
        finally:
            store.connection.close()

    threads = [
        threading.Thread(target=worker, args=("new_a", 10)),
        threading.Thread(target=worker, args=("new_b", 11)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads), "worker thread hung"

    members = _members(db)
    assert sorted(results.values()) == ["admitted", "refused"], results
    assert len(members) == 3, members


def test_reassignment_into_a_full_cohort_is_refused_and_keeps_the_old_membership(tmp_path):
    db = str(tmp_path / "reassign.db")
    store = SQLiteLeaderboardStore(db)
    proj = LeaderboardProjection(store)
    proj.register_cohort(_cohort("cohort_A", max_size=3))
    proj.register_cohort(_cohort("cohort_B", max_size=2))
    proj.assign_cohort("b1", "cohort_B", EPOCH, ASSIGNED_AT, watermark_offset=1)
    proj.assign_cohort("b2", "cohort_B", EPOCH, ASSIGNED_AT, watermark_offset=2)
    proj.assign_cohort("mover", "cohort_A", EPOCH, ASSIGNED_AT, watermark_offset=3)

    before = store.get_entry("cohort", "cohort_A", EPOCH, "mover")
    assert before is not None

    with pytest.raises(CohortCapacityOverflowError):
        proj.assign_cohort("mover", "cohort_B", EPOCH, "2026-10-13T08:00:00Z", watermark_offset=4)

    assert store.get_member_cohorts(EPOCH, "mover") == ["cohort_A"]
    assert store.get_entry("cohort", "cohort_A", EPOCH, "mover") == before
    assert store.get_entry("cohort", "cohort_B", EPOCH, "mover") is None
    assert store.get_cohort_member_count("cohort_B", EPOCH) == 2
    store.connection.close()


def test_single_connection_capacity_still_enforced(tmp_path):
    db = _seeded_db(tmp_path, existing=("m1", "m2", "m3"))
    store = SQLiteLeaderboardStore(db)
    proj = LeaderboardProjection(store)
    with pytest.raises(CohortCapacityOverflowError):
        proj.assign_cohort("late", COHORT, EPOCH, ASSIGNED_AT, watermark_offset=9)
    store.connection.close()
    assert _members(db) == {"m1", "m2", "m3"}


# --- Step 3: edge cases -------------------------------------------------------


def _race(db, subjects, barrier_timeout=1.0, via_handler=False):
    """Run one thread per subject, each on its own connection, all pausing after the count read.

    Returns {subject: "admitted" | "refused" | "error: ..."}.
    """
    barrier = threading.Barrier(len(subjects), timeout=barrier_timeout)
    results = {}

    def worker(subject, offset):
        store = SQLiteLeaderboardStore(db)
        original = store.get_cohort_member_count

        def count_then_wait(cohort_id, epoch):
            count = original(cohort_id, epoch)
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass
            return count

        store.get_cohort_member_count = count_then_wait
        proj = LeaderboardProjection(store)
        try:
            if via_handler:
                event = Learner.CohortAssigned(
                    originator_id=uuid.uuid4(),
                    originator_version=1,
                    timestamp=datetime(2026, 10, 12, 8, 0, 0, tzinfo=timezone.utc),
                    cohort_id=COHORT,
                    membership_epoch=EPOCH,
                    grouping_rule_version="v1",
                    assigned_at=ASSIGNED_AT,
                )
                # the handler keys rows by originator_id, so remember which subject this is
                results[subject] = None
                proj.apply(event, offset=offset)
                results[subject] = ("admitted", str(event.originator_id))
            else:
                proj.assign_cohort(subject, COHORT, EPOCH, ASSIGNED_AT, watermark_offset=offset)
                results[subject] = ("admitted", subject)
        except CohortCapacityOverflowError:
            results[subject] = ("refused", None)
        except Exception as exc:
            results[subject] = (f"error: {exc!r}", None)
        finally:
            store.connection.close()

    threads = [
        threading.Thread(target=worker, args=(subject, 100 + i)) for i, subject in enumerate(subjects)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads), "worker thread hung"
    return results


def _snapshot(store):
    rows = store.connection.execute(
        "SELECT scope_type, scope_id, epoch, subject_id, score, first_reached_at, last_updated_at, "
        "watermark_offset FROM leaderboard_entries ORDER BY 1, 2, 3, 4"
    ).fetchall()
    return rows


def test_refused_admission_leaves_no_partial_state(tmp_path):
    db = str(tmp_path / "partial.db")
    store = SQLiteLeaderboardStore(db)
    proj = LeaderboardProjection(store)
    proj.register_cohort(_cohort("cohort_X", max_size=2))
    proj.register_cohort(_cohort("cohort_A", max_size=5))
    proj.assign_cohort("x1", "cohort_X", EPOCH, ASSIGNED_AT, watermark_offset=1)
    proj.assign_cohort("x2", "cohort_X", EPOCH, ASSIGNED_AT, watermark_offset=2)
    proj.assign_cohort("mover", "cohort_A", EPOCH, ASSIGNED_AT, watermark_offset=3)
    rows_before = _snapshot(store)
    defs_before = {c: store.get_cohort_definition(c) for c in ("cohort_X", "cohort_A")}

    for subject in ("newbie", "mover"):
        with pytest.raises(CohortCapacityOverflowError):
            proj.assign_cohort(subject, "cohort_X", EPOCH, "2026-10-13T08:00:00Z", watermark_offset=9)

    assert _snapshot(store) == rows_before
    assert store.get_member_cohorts(EPOCH, "newbie") == []
    assert store.get_entry("cohort", "cohort_X", EPOCH, "newbie") is None
    assert store.get_entry("cohort", "cohort_X", EPOCH, "mover") is None
    assert {c: store.get_cohort_definition(c) for c in defs_before} == defs_before
    store.connection.close()


def test_forming_cohort_auto_advances_to_active_once_and_persists(tmp_path):
    db = str(tmp_path / "forming.db")
    store = SQLiteLeaderboardStore(db)
    proj = LeaderboardProjection(store)
    proj.register_cohort(CohortDefinition(
        cohort_id="cohort_F", strategy="activity_volume", epoch=EPOCH,
        min_size=2, max_size=5, status="forming",
    ))
    proj.assign_cohort("f1", "cohort_F", EPOCH, ASSIGNED_AT, watermark_offset=1)
    assert store.get_cohort_definition("cohort_F").status == "forming"

    proj.assign_cohort("f2", "cohort_F", EPOCH, ASSIGNED_AT, watermark_offset=2)
    assert store.get_cohort_definition("cohort_F").status == "active"

    # A further admission must not try to advance it again.
    proj.assign_cohort("f3", "cohort_F", EPOCH, ASSIGNED_AT, watermark_offset=3)
    assert store.get_cohort_definition("cohort_F").status == "active"
    store.connection.close()

    reopened = SQLiteLeaderboardStore(db)
    definition = reopened.get_cohort_definition("cohort_F")
    assert definition.status == "active"
    assert (definition.min_size, definition.max_size) == (2, 5)
    assert reopened.get_cohort_member_count("cohort_F", EPOCH) == 3
    reopened.connection.close()


@pytest.mark.parametrize("status", ["sealed", "finalized"])
def test_admission_to_sealed_or_finalized_cohort_is_refused_and_writes_nothing(tmp_path, status):
    db = str(tmp_path / f"{status}.db")
    store = SQLiteLeaderboardStore(db)
    proj = LeaderboardProjection(store)
    proj.register_cohort(_cohort("cohort_S", max_size=5))
    proj.assign_cohort("s1", "cohort_S", EPOCH, ASSIGNED_AT, watermark_offset=1)
    proj.register_cohort(CohortDefinition(
        cohort_id="cohort_S", strategy="activity_volume", epoch=EPOCH,
        min_size=1, max_size=5, status=status,
    ))
    rows_before = _snapshot(store)
    definition_before = store.get_cohort_definition("cohort_S")

    with pytest.raises(CohortSealedError):
        proj.assign_cohort("late", "cohort_S", EPOCH, ASSIGNED_AT, watermark_offset=2)

    assert _snapshot(store) == rows_before
    assert store.get_member_cohorts(EPOCH, "late") == []
    assert store.get_cohort_definition("cohort_S") == definition_before
    store.connection.close()


def test_reassigning_into_the_same_full_cohort_is_allowed(tmp_path):
    db = _seeded_db(tmp_path, existing=("m1", "m2", "m3"))
    store = SQLiteLeaderboardStore(db)
    # A fresh projection has no in-memory cache, so the old cohort is read from the database.
    proj = LeaderboardProjection(store)
    proj.assign_cohort("m1", COHORT, EPOCH, "2026-10-13T08:00:00Z", watermark_offset=9)
    # The same projection again, now with its cache populated.
    proj.assign_cohort("m1", COHORT, EPOCH, "2026-10-14T08:00:00Z", watermark_offset=10)

    assert store.get_cohort_member_count(COHORT, EPOCH) == 3
    assert store.get_member_cohorts(EPOCH, "m1") == [COHORT]
    rows = store.connection.execute(
        "SELECT COUNT(*) FROM leaderboard_entries WHERE scope_type='cohort' AND scope_id=? "
        "AND epoch=? AND subject_id='m1'", (COHORT, EPOCH),
    ).fetchone()[0]
    assert rows == 1
    store.connection.close()


def test_race_repeated_ten_rounds_always_has_exactly_one_winner(tmp_path):
    for round_no in range(10):
        db = str(tmp_path / f"round_{round_no}.db")
        store = SQLiteLeaderboardStore(db)
        proj = LeaderboardProjection(store)
        proj.register_cohort(_cohort(max_size=3))
        proj.assign_cohort("m1", COHORT, EPOCH, ASSIGNED_AT, watermark_offset=1)
        proj.assign_cohort("m2", COHORT, EPOCH, ASSIGNED_AT, watermark_offset=2)
        store.connection.close()

        results = _race(db, ["new_a", "new_b"], barrier_timeout=0.3)
        outcomes = sorted(r[0] for r in results.values())
        members = _members(db)
        assert outcomes == ["admitted", "refused"], (round_no, results)
        assert len(members) == 3, (round_no, members)


@pytest.mark.parametrize("holder_takes_last_seat", [False, True])
def test_waiting_connection_finishes_cleanly_after_the_writer_commits(tmp_path, holder_takes_last_seat):
    db = _seeded_db(tmp_path)  # 2 of 3 seats taken
    holder = SQLiteLeaderboardStore(db)
    outcome = {}
    ready, go = threading.Event(), threading.Event()

    def waiter():
        # Open the connection first: creating a store runs schema DDL, which would itself
        # block while the holder owns the write lock.
        store = SQLiteLeaderboardStore(db)
        store.connection.execute("PRAGMA busy_timeout=2000")  # this connection only
        proj = LeaderboardProjection(store)
        ready.set()
        go.wait(timeout=10)
        t0 = time.monotonic()
        try:
            proj.assign_cohort("waiter", COHORT, EPOCH, ASSIGNED_AT, watermark_offset=50)
            outcome["result"] = "admitted"
        except CohortCapacityOverflowError:
            outcome["result"] = "refused"
        except Exception as exc:
            outcome["result"] = f"error: {exc!r}"
        finally:
            outcome["waited"] = time.monotonic() - t0
            store.connection.close()

    thread = threading.Thread(target=waiter)
    thread.start()
    assert ready.wait(timeout=10)
    with holder.transaction(immediate=True):
        # Holder owns the write lock: either takes the last seat or writes elsewhere.
        target = COHORT if holder_takes_last_seat else "cohort_elsewhere"
        holder.upsert_score(
            scope_type="cohort", scope_id=target, epoch=EPOCH, subject_id="holder",
            score=0, first_reached_at=ASSIGNED_AT, updated_at=ASSIGNED_AT, watermark_offset=40,
        )
        go.set()
        time.sleep(0.4)
        assert thread.is_alive(), "waiter should be blocked while the writer holds the lock"
        assert "result" not in outcome
    thread.join(timeout=10)
    holder.connection.close()

    assert not thread.is_alive(), "waiter hung"
    assert outcome["waited"] >= 0.3
    assert outcome["result"] == ("refused" if holder_takes_last_seat else "admitted"), outcome
    assert len(_members(db)) == 3


def test_in_memory_store_capacity_behaviour_unchanged():
    store = InMemoryLeaderboardStore()
    proj = LeaderboardProjection(store)
    proj.register_cohort(_cohort("cohort_M", max_size=3))
    proj.register_cohort(_cohort("cohort_N", max_size=5))
    for offset, subject in enumerate(("m1", "m2", "m3"), start=1):
        proj.assign_cohort(subject, "cohort_M", EPOCH, ASSIGNED_AT, watermark_offset=offset)
    proj.assign_cohort("m1", "cohort_M", EPOCH, ASSIGNED_AT, watermark_offset=4)  # same cohort, allowed
    proj.assign_cohort("mover", "cohort_N", EPOCH, ASSIGNED_AT, watermark_offset=5)

    for subject in ("late", "mover"):
        with pytest.raises(CohortCapacityOverflowError):
            proj.assign_cohort(subject, "cohort_M", EPOCH, ASSIGNED_AT, watermark_offset=6)
    assert store.get_cohort_member_count("cohort_M", EPOCH) == 3
    assert store.get_member_cohorts(EPOCH, "mover") == ["cohort_N"]
    assert store.get_member_cohorts(EPOCH, "late") == []


def test_event_handler_path_two_connections_cannot_both_fill_the_last_seat(tmp_path):
    """Drives LeaderboardProjection.apply(CohortAssigned), which wraps assign_cohort in an
    outer transaction(immediate=True)."""
    db = _seeded_db(tmp_path)
    results = _race(db, ["handler_a", "handler_b"], via_handler=True)

    outcomes = sorted(r[0] for r in results.values())
    assert outcomes == ["admitted", "refused"], results
    assert len(_members(db)) == 3
    winner_ids = {r[1] for r in results.values() if r[0] == "admitted"}
    assert winner_ids <= _members(db)
