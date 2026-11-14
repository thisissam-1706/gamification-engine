"""Cohort builder at a realistic scale (small min/max so the test stays fast), and late arrivals.

The bucket counts are NOT passed: the builder sizes them itself (activity 1; module
``max(1, group_size // (2 * min_size))`` per tier-and-module group). The hash is real (not pinned),
and learner ids are fixed (uuid5), so cohort ids are reproducible across applications.

Late-arrival rule: a new learner is first matched, group by group (same tier and module), to an
EXISTING cohort of the same tier that has room for the whole group, ranked same module first then
lowest cohort id; only groups with no such cohort get automatic bucket sizing, split and merge.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid5

from src.leaderboard.cohort_builder import _tier_and_module
from src.state_machine.learner import Learner
from tests.leaderboard.test_cohort_builder import EPOCH, PRIOR_WEEK_START, REFERENCE, _app, _week
from tests.leaderboard.test_cohort_builder_edges import _events, _run

NAMESPACE = UUID("a3c1f0de-5b27-4c0a-9d41-7e6b2f8a1c35")
LESSONS = {"casual": 1, "regular": 4, "intensive": 10}


def _make(app, label: str, tier: str, module: str) -> str:
    """One learner with a fixed id and the prior-week lessons of ``tier`` in ``module``."""
    learner = Learner._create(Learner.Created, id=uuid5(NAMESPACE, label))
    for k, (occurred_at, lesson_id) in enumerate(_week(PRIOR_WEEK_START, LESSONS[tier], module=module)):
        learner.complete_lesson(f"cmp-{k}", lesson_id, occurred_at=occurred_at.isoformat(), timezone="UTC")
    app.save(learner)
    return str(learner.id)


def _population(app, groups: dict[tuple[str, str], int]) -> dict[str, tuple[str, str]]:
    """Creates the learners of each (tier, module) group; returns learner id -> (tier, module)."""
    made: dict[str, tuple[str, str]] = {}
    for (tier, module), count in groups.items():
        for n in range(count):
            made[_make(app, f"{tier}/{module}/{n}", tier, module)] = (tier, module)
    return made


def _cohort_tier(cohort_id: str) -> str | None:
    return _tier_and_module(cohort_id, EPOCH)[0]


def _cohort_module(cohort_id: str) -> str | None:
    return _tier_and_module(cohort_id, EPOCH)[1]


# --- 300 learners, min 20, max 100 --------------------------------------------------------

SCALE_GROUPS = {
    ("casual", "math"): 90,
    ("casual", "py"): 60,
    ("regular", "math"): 60,
    ("regular", "py"): 40,
    ("intensive", "sql"): 30,
    ("intensive", "math"): 20,
}
MIN_SIZE, MAX_SIZE = 20, 100


def test_cohort_sizes_stay_within_min_and_max_except_one_that_cannot_merge(tmp_path):
    app = _app(tmp_path)
    try:
        learners = _population(app, SCALE_GROUPS)
        assert len(learners) == 300

        result = _run(app, list(learners), min_size=MIN_SIZE, max_size=MAX_SIZE)

        sizes = result.cohort_counts
        assert sum(sizes.values()) == 300
        assert max(sizes.values()) <= MAX_SIZE
        outside = [c for c, n in sizes.items() if not MIN_SIZE <= n <= MAX_SIZE]
        assert len(outside) <= 1, {c: sizes[c] for c in outside}
        for cohort in outside:  # allowed only if it truly cannot merge without breaking max_size
            assert all(n + sizes[cohort] > MAX_SIZE for c, n in sizes.items() if c != cohort)
        # Not everything collapsed: each tier-and-module group keeps a cohort of its own.
        assert len(sizes) >= len(SCALE_GROUPS)
    finally:
        app.close()


def test_cohorts_are_mostly_one_tier(tmp_path):
    app = _app(tmp_path)
    try:
        learners = _population(app, SCALE_GROUPS)

        result = _run(app, list(learners), min_size=MIN_SIZE, max_size=MAX_SIZE)

        matching = sum(
            1 for learner, (tier, _) in learners.items() if _cohort_tier(result[learner]) == tier
        )
        assert matching / len(learners) >= 0.9
        for cohort in result.cohort_counts:  # and every cohort is mostly one tier's learners
            members = [l for l, c in result.assignments.items() if c == cohort]
            top = max(
                sum(1 for l in members if learners[l][0] == t) for t in ("casual", "regular", "intensive")
            )
            assert top / len(members) >= 0.9, cohort
    finally:
        app.close()


def test_same_input_twice_gives_the_same_cohort_ids(tmp_path):
    results = []
    for name in ("one", "two"):
        (tmp_path / name).mkdir()
        app = _app(tmp_path / name)
        try:
            learners = _population(app, SCALE_GROUPS)
            results.append(_run(app, list(learners), min_size=MIN_SIZE, max_size=MAX_SIZE))
        finally:
            app.close()

    assert results[0].assignments == results[1].assignments
    assert results[0].cohort_counts == results[1].cohort_counts


def test_a_big_group_is_spread_over_several_cohorts_not_collapsed_into_one(tmp_path):
    """240 casual/math learners, min 20: the module bucket count is 240 // 40 = 6."""
    app = _app(tmp_path)
    try:
        learners = _population(app, {("casual", "math"): 240})

        result = _run(app, list(learners), min_size=MIN_SIZE, max_size=MAX_SIZE)

        sizes = sorted(result.cohort_counts.values())
        assert sum(sizes) == 240
        assert min(sizes) >= MIN_SIZE and max(sizes) <= MAX_SIZE
        assert len(sizes) >= 5, sizes  # about six cohorts of about forty, not one or two large ones
        assert max(sizes) <= 2 * (240 // 6), sizes  # none far above the average (40)
    finally:
        app.close()


# --- late arrivals: match an existing cohort first ----------------------------------------


def test_a_late_learner_joins_an_existing_same_tier_same_module_cohort_and_no_new_cohort_appears(tmp_path):
    app = _app(tmp_path)
    try:
        learners = _population(app, {("casual", "math"): 120})  # 120 // 40 = 3 module buckets
        first = _run(app, list(learners), min_size=MIN_SIZE, max_size=MAX_SIZE)
        assert len(first.cohort_counts) > 1  # several existing cohorts to choose from

        late = _make(app, "late/casual/math", "casual", "math")
        second = _run(app, [*learners, late], min_size=MIN_SIZE, max_size=MAX_SIZE)

        assert set(second.cohort_counts) == set(first.cohort_counts)  # no new cohort
        joined = second[late]
        assert (_cohort_tier(joined), _cohort_module(joined)) == ("casual", "math")
        assert joined == min(first.cohort_counts)  # same tier and module for all: lowest id with room
        assert second.cohort_counts[joined] == first.cohort_counts[joined] + 1
    finally:
        app.close()


def test_a_late_learner_prefers_the_same_module_over_a_lower_cohort_id(tmp_path):
    app = _app(tmp_path)
    try:
        learners = _population(app, {("casual", "aaa"): 6, ("casual", "math"): 6})
        first = _run(app, list(learners), min_size=2, max_size=50)
        assert len(first.cohort_counts) == 2

        late = _make(app, "late/casual/math", "casual", "math")
        second = _run(app, [*learners, late], min_size=2, max_size=50)

        assert _cohort_module(second[late]) == "math"  # not the lower id, "aaa"
        assert set(second.cohort_counts) == set(first.cohort_counts)
    finally:
        app.close()


def test_a_late_learner_whose_tier_has_no_cohort_yet_gets_a_new_cohort(tmp_path):
    app = _app(tmp_path)
    try:
        learners = _population(app, {("casual", "math"): 12})
        first = _run(app, list(learners), min_size=2, max_size=50)

        late = [_make(app, f"late/regular/math/{n}", "regular", "math") for n in range(2)]
        second = _run(app, [*learners, *late], min_size=2, max_size=50)

        new_cohorts = set(second.cohort_counts) - set(first.cohort_counts)
        assert len(new_cohorts) == 1
        (new_cohort,) = new_cohorts
        assert _cohort_tier(new_cohort) == "regular"
        assert {second[i] for i in late} == {new_cohort}
        assert second.cohort_counts[new_cohort] == 2
    finally:
        app.close()


def test_existing_assignments_are_identical_before_and_after_the_second_call(tmp_path):
    app = _app(tmp_path)
    try:
        learners = _population(app, {("casual", "math"): 60, ("intensive", "py"): 24})
        first = _run(app, list(learners), min_size=MIN_SIZE, max_size=MAX_SIZE)
        events_before = {i: _events(app, i) for i in learners}

        late = [
            _make(app, "late/casual/math", "casual", "math"),
            _make(app, "late/regular/sql", "regular", "sql"),
            _make(app, "late/intensive/py", "intensive", "py"),
        ]
        second = _run(app, [*learners, *late], min_size=MIN_SIZE, max_size=MAX_SIZE)

        assert {i: second[i] for i in learners} == dict(first.assignments)
        assert {i: _events(app, i) for i in learners} == events_before
    finally:
        app.close()


def test_running_the_second_call_twice_changes_nothing(tmp_path):
    app = _app(tmp_path)
    try:
        learners = _population(app, {("casual", "math"): 60, ("intensive", "py"): 24})
        _run(app, list(learners), min_size=MIN_SIZE, max_size=MAX_SIZE)
        late = [
            _make(app, "late/casual/math", "casual", "math"),
            _make(app, "late/regular/sql", "regular", "sql"),
        ]
        everyone = [*learners, *late]
        second = _run(app, everyone, min_size=MIN_SIZE, max_size=MAX_SIZE)
        stored = {i: _events(app, i) for i in everyone}

        third = _run(app, everyone, min_size=MIN_SIZE, max_size=MAX_SIZE)

        assert third == second
        assert {i: _events(app, i) for i in everyone} == stored
    finally:
        app.close()
