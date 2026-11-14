"""An undersized cohort merges into its NEAREST cohort (cohort builder fix).

Rule: for each undersized cohort (smallest first, then by id) the target is chosen among the OTHER
cohorts that have room (size after the merge <= max_size), ranked by: same tier first, then same
module, then lowest cohort id. A cohort that fits nowhere stays as it is.

Like the edge tests, the hash buckets are pinned (patching the two assign_* names inside
``cohort_builder``), so a cohort is exactly one tier in one module and ids sort by tier then module.
Tiers: 1 prior-week lesson is casual, 4 regular, 10 intensive.
"""

from src.leaderboard.cohorts import is_inactive  # noqa: F401  (documents that inactivity is not involved)
from tests.leaderboard.test_cohort_builder import PRIOR_WEEK_START, _app, _learner, _week
from tests.leaderboard.test_cohort_builder_edges import _events, _fixed_buckets, _run

LESSONS = {"casual": 1, "regular": 4, "intensive": 10}


def _group(app, roles: dict, label: str, count: int, tier: str, module: str) -> list[str]:
    """`count` learners of one tier and module; records label -> learner ids in `roles`."""
    ids = [_learner(app, _week(PRIOR_WEEK_START, LESSONS[tier], module=module)) for _ in range(count)]
    roles[label] = ids
    return ids


def _cohort_of(result, roles, label) -> set[str]:
    cohorts = {result.assignments[i] for i in roles[label]}
    assert len(cohorts) == 1, (label, cohorts)  # a group is never split here
    return cohorts.pop()


def _everyone(roles) -> list[str]:
    return [i for ids in roles.values() for i in ids]


def _signature(result, roles) -> list[tuple]:
    """Cohort composition in terms of group labels, independent of the random learner ids."""
    label_of = {i: label for label, ids in roles.items() for i in ids}
    members: dict[str, list[str]] = {}
    for learner, cohort in result.assignments.items():
        members.setdefault(cohort, []).append(label_of[learner])
    return sorted(tuple(sorted(labels)) for labels in members.values())


# --- (a) same tier beats everything, even when a casual cohort comes first by id --------


def test_a_undersized_intensive_math_merges_into_another_intensive_cohort(tmp_path, monkeypatch):
    _fixed_buckets(monkeypatch)
    app = _app(tmp_path)
    try:
        roles: dict = {}
        _group(app, roles, "source", 1, "intensive", "math")  # undersized (min 3)
        _group(app, roles, "casual_math", 3, "casual", "math")  # first by id AND same module
        _group(app, roles, "intensive_py", 3, "intensive", "py")  # same tier, other module

        result = _run(app, _everyone(roles), min_size=3, max_size=10)

        assert _cohort_of(result, roles, "source") == _cohort_of(result, roles, "intensive_py")
        assert _cohort_of(result, roles, "source") != _cohort_of(result, roles, "casual_math")
        assert sorted(result.cohort_counts.values()) == [3, 4]
    finally:
        app.close()


# --- (b) no same-tier cohort with room -> same module -------------------------------------


def test_b_without_a_same_tier_cohort_with_room_it_goes_to_a_same_module_cohort(tmp_path, monkeypatch):
    _fixed_buckets(monkeypatch)
    app = _app(tmp_path)
    try:
        roles: dict = {}
        _group(app, roles, "source", 1, "intensive", "math")  # undersized
        _group(app, roles, "intensive_py_full", 4, "intensive", "py")  # same tier but full (max 4)
        _group(app, roles, "casual_chem", 3, "casual", "chem")  # lowest id, other module
        _group(app, roles, "casual_math", 3, "casual", "math")  # same module

        result = _run(app, _everyone(roles), min_size=3, max_size=4)

        assert _cohort_of(result, roles, "source") == _cohort_of(result, roles, "casual_math")
        assert _cohort_of(result, roles, "source") != _cohort_of(result, roles, "casual_chem")
        assert _cohort_of(result, roles, "source") != _cohort_of(result, roles, "intensive_py_full")
        assert sorted(result.cohort_counts.values()) == [3, 4, 4]
    finally:
        app.close()


# --- (c) otherwise the lowest cohort id ---------------------------------------------------


def test_c_otherwise_it_goes_to_the_lowest_cohort_id_that_has_room(tmp_path, monkeypatch):
    _fixed_buckets(monkeypatch)
    app = _app(tmp_path)
    try:
        roles: dict = {}
        _group(app, roles, "source", 1, "intensive", "math")  # undersized; nobody shares tier or module
        _group(app, roles, "casual_aaa_full", 4, "casual", "aaa")  # lowest id but full (max 4)
        _group(app, roles, "casual_chem", 3, "casual", "chem")
        _group(app, roles, "regular_zzz", 3, "regular", "zzz")

        result = _run(app, _everyone(roles), min_size=3, max_size=4)

        assert _cohort_of(result, roles, "source") == _cohort_of(result, roles, "casual_chem")
        assert sorted(result.cohort_counts.values()) == [3, 4, 4]
    finally:
        app.close()


# --- (d) max_size always holds ------------------------------------------------------------


def test_d_max_size_holds_and_two_small_same_tier_cohorts_pair_up(tmp_path, monkeypatch):
    _fixed_buckets(monkeypatch)
    app = _app(tmp_path)
    try:
        roles: dict = {}
        _group(app, roles, "big", 5, "intensive", "math")
        _group(app, roles, "small_math", 1, "casual", "math")
        _group(app, roles, "small_physics", 1, "casual", "physics")

        # No room next to the big cohort for both singletons (6 would be reached by one, 7 by both).
        result = _run(app, _everyone(roles), min_size=3, max_size=6)

        assert max(result.cohort_counts.values()) <= 6
        assert sum(result.cohort_counts.values()) == 7
        assert _cohort_of(result, roles, "small_math") == _cohort_of(result, roles, "small_physics")
        assert _cohort_of(result, roles, "big") != _cohort_of(result, roles, "small_math")
        assert sorted(result.cohort_counts.values()) == [2, 5]
    finally:
        app.close()


def test_d_a_cohort_that_fits_nowhere_stays_as_it_is(tmp_path, monkeypatch):
    _fixed_buckets(monkeypatch)
    app = _app(tmp_path)
    try:
        roles: dict = {}
        _group(app, roles, "full", 3, "casual", "math")  # at max_size
        _group(app, roles, "source", 1, "casual", "py")  # undersized, nowhere to go

        result = _run(app, _everyone(roles), min_size=2, max_size=3)

        assert sorted(result.cohort_counts.values()) == [1, 3]
        assert _cohort_of(result, roles, "source") != _cohort_of(result, roles, "full")
    finally:
        app.close()


# --- (e) deterministic, and running twice changes nothing ---------------------------------


def _population(app):
    roles: dict = {}
    _group(app, roles, "i_math_1", 1, "intensive", "math")
    _group(app, roles, "i_py", 3, "intensive", "py")
    _group(app, roles, "c_math", 3, "casual", "math")
    _group(app, roles, "c_chem_1", 1, "casual", "chem")
    _group(app, roles, "r_math_2", 2, "regular", "math")
    return roles


def test_e_same_population_gives_the_same_cohorts_in_two_applications(tmp_path, monkeypatch):
    _fixed_buckets(monkeypatch)
    signatures = []
    for name in ("one", "two"):
        (tmp_path / name).mkdir()
        app = _app(tmp_path / name)
        try:
            roles = _population(app)
            result = _run(app, _everyone(roles), min_size=3, max_size=10)
            signatures.append(_signature(result, roles))
        finally:
            app.close()

    assert signatures[0] == signatures[1]


def test_e_running_twice_changes_nothing(tmp_path, monkeypatch):
    _fixed_buckets(monkeypatch)
    app = _app(tmp_path)
    try:
        roles = _population(app)
        ids = _everyone(roles)

        first = _run(app, ids, min_size=3, max_size=10)
        stored = {i: _events(app, i) for i in ids}
        second = _run(app, ids, min_size=3, max_size=10)

        assert second == first
        assert {i: _events(app, i) for i in ids} == stored
    finally:
        app.close()
