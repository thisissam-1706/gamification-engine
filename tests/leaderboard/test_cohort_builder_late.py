"""Late arrivals: build_weekly_cohorts called again for the same epoch with new learners.

Rules: (a) already-assigned learners are never moved; (b) they count toward min_size and max_size of
their cohort; (c) a late learner whose base cohort id already has members joins it if the whole
late group fits, otherwise the split/merge logic applies using the existing sizes; (d) running the
builder again with no new learners changes nothing.

Every call passes the whole population (old and new learners): the builder can only count members it
is given. Hash buckets are pinned as in the edge tests, so a cohort is one tier in one module.
"""

from uuid import UUID

from src.leaderboard.projection import LeaderboardProjection
from src.leaderboard.store import InMemoryLeaderboardStore
from tests.leaderboard.test_cohort_builder import EPOCH, _app
from tests.leaderboard.test_cohort_builder_edges import _events, _fixed_buckets, _run
from tests.leaderboard.test_cohort_builder_nearest import _cohort_of, _everyone, _group
from tests.leaderboard.test_leaderboard_e2e import read_all_notifications


def _stored_cohort(app, learner_id) -> str:
    return app.repository.get(UUID(learner_id)).cohort_memberships[EPOCH]["cohort_id"]


# --- late learner joins the existing cohort ---------------------------------------------


def test_late_learner_joins_the_existing_cohort_when_it_has_room(tmp_path, monkeypatch):
    _fixed_buckets(monkeypatch)
    app = _app(tmp_path)
    try:
        roles: dict = {}
        _group(app, roles, "early", 3, "casual", "math")
        first = _run(app, _everyone(roles), min_size=2, max_size=10)

        _group(app, roles, "late", 1, "casual", "math")
        second = _run(app, _everyone(roles), min_size=2, max_size=10)

        assert _cohort_of(second, roles, "late") == _cohort_of(first, roles, "early")
        assert sorted(second.cohort_counts.values()) == [4]
    finally:
        app.close()


def test_late_learner_with_a_new_cohort_id_joins_the_nearest_existing_cohort_not_a_tiny_new_one(
    tmp_path, monkeypatch
):
    _fixed_buckets(monkeypatch)
    app = _app(tmp_path)
    try:
        roles: dict = {}
        _group(app, roles, "casual_math", 3, "casual", "math")
        _group(app, roles, "intensive_py", 3, "intensive", "py")
        _run(app, _everyone(roles), min_size=3, max_size=10)

        # A new tier-and-module combination: alone it would be a cohort of one.
        _group(app, roles, "late", 1, "intensive", "math")
        second = _run(app, _everyone(roles), min_size=3, max_size=10)

        assert _cohort_of(second, roles, "late") == _cohort_of(second, roles, "intensive_py")
        assert sorted(second.cohort_counts.values()) == [3, 4]
    finally:
        app.close()


# --- max_size counts the existing members ------------------------------------------------


def test_a_full_cohort_is_not_overfilled_by_a_late_learner(tmp_path, monkeypatch):
    _fixed_buckets(monkeypatch)
    app = _app(tmp_path)
    try:
        roles: dict = {}
        _group(app, roles, "early", 3, "casual", "math")  # size == max_size
        _run(app, _everyone(roles), min_size=1, max_size=3)

        _group(app, roles, "late", 1, "casual", "math")  # same base cohort id as the full one
        second = _run(app, _everyone(roles), min_size=1, max_size=3)

        assert max(second.cohort_counts.values()) <= 3
        assert sorted(second.cohort_counts.values()) == [1, 3]
        assert _cohort_of(second, roles, "late") != _cohort_of(second, roles, "early")
    finally:
        app.close()


def test_two_late_learners_in_the_same_call_end_up_together_and_respect_max_size(tmp_path, monkeypatch):
    _fixed_buckets(monkeypatch)
    app = _app(tmp_path)
    try:
        roles: dict = {}
        _group(app, roles, "early", 4, "casual", "math")  # room for one more at max 5
        _run(app, _everyone(roles), min_size=2, max_size=5)

        _group(app, roles, "late", 2, "casual", "math")  # both do not fit next to the four
        second = _run(app, _everyone(roles), min_size=2, max_size=5)

        assert len({second.assignments[i] for i in roles["late"]}) == 1  # together
        assert _cohort_of(second, roles, "late") != _cohort_of(second, roles, "early")
        assert max(second.cohort_counts.values()) <= 5
        assert sorted(second.cohort_counts.values()) == [2, 4]
    finally:
        app.close()


# --- existing members are never moved ----------------------------------------------------


def test_existing_members_keep_their_cohorts_and_get_no_new_events(tmp_path, monkeypatch):
    _fixed_buckets(monkeypatch)
    app = _app(tmp_path)
    try:
        roles: dict = {}
        _group(app, roles, "casual_math", 3, "casual", "math")
        _group(app, roles, "intensive_py", 2, "intensive", "py")
        _group(app, roles, "lone", 1, "regular", "sql")  # undersized, would be a merge source
        early_ids = _everyone(roles)
        first = _run(app, early_ids, min_size=3, max_size=10)
        cohorts_before = {i: _stored_cohort(app, i) for i in early_ids}
        events_before = {i: _events(app, i) for i in early_ids}

        _group(app, roles, "late", 2, "casual", "py")
        second = _run(app, _everyone(roles), min_size=3, max_size=10)

        assert {i: _stored_cohort(app, i) for i in early_ids} == cohorts_before
        assert {i: second.assignments[i] for i in early_ids} == dict(first.assignments)
        assert {i: _events(app, i) for i in early_ids} == events_before
    finally:
        app.close()


def test_running_again_with_no_new_learners_changes_nothing(tmp_path, monkeypatch):
    _fixed_buckets(monkeypatch)
    app = _app(tmp_path)
    try:
        roles: dict = {}
        _group(app, roles, "early", 3, "casual", "math")
        _run(app, _everyone(roles), min_size=2, max_size=10)
        _group(app, roles, "late", 2, "intensive", "py")
        second = _run(app, _everyone(roles), min_size=2, max_size=10)
        stored = {i: _events(app, i) for i in _everyone(roles)}

        third = _run(app, _everyone(roles), min_size=2, max_size=10)

        assert third == second
        assert {i: _events(app, i) for i in _everyone(roles)} == stored
    finally:
        app.close()


# --- replay ------------------------------------------------------------------------------


def test_a_rebuild_from_the_event_log_reproduces_the_same_cohorts(tmp_path, monkeypatch):
    _fixed_buckets(monkeypatch)
    app = _app(tmp_path)
    try:
        roles: dict = {}
        _group(app, roles, "early", 4, "casual", "math")
        _group(app, roles, "other", 2, "intensive", "py")
        _run(app, _everyone(roles), min_size=2, max_size=5)
        _group(app, roles, "late", 2, "casual", "math")  # overflows into a second cohort
        result = _run(app, _everyone(roles), min_size=2, max_size=5)
        everyone = _everyone(roles)
    finally:
        app.close()

    restarted = _app(tmp_path)  # a fresh application on the same event log
    try:
        replayed = {i: _stored_cohort(restarted, i) for i in everyone}
        assert replayed == dict(result.assignments)

        projection_store = InMemoryLeaderboardStore()
        LeaderboardProjection(projection_store).rebuild(read_all_notifications(restarted))
        assert {i: projection_store.get_member_cohort(EPOCH, i) for i in everyone} == dict(
            result.assignments
        )
        # A fresh instance asked again for the same population changes nothing.
        assert _run(restarted, everyone, min_size=2, max_size=5) == result
    finally:
        restarted.close()
