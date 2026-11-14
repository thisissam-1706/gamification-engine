"""activity_buckets / module_buckets on build_weekly_cohorts: explicit counts, and the automatic rule."""

from tests.leaderboard.test_cohort_builder import EPOCH, PRIOR_WEEK_START, REFERENCE, _app, _learner, _week


def _run(app, ids, **kwargs):
    from src.leaderboard.cohort_builder import build_weekly_cohorts

    kwargs.setdefault("min_size", 1)
    return build_weekly_cohorts(app, ids, EPOCH, reference_time=REFERENCE, **kwargs)


def test_one_bucket_makes_a_cohort_exactly_one_tier_and_module(tmp_path):
    app = _app(tmp_path)
    try:
        a = _learner(app, _week(PRIOR_WEEK_START, 4))
        b = _learner(app, _week(PRIOR_WEEK_START, 5))  # same tier and module as a
        c = _learner(app, _week(PRIOR_WEEK_START, 5, module="physics"))

        result = _run(app, [a, b, c], activity_buckets=1, module_buckets=1)

        assert result[a] == result[b] != result[c]
        assert result[a] == (
            "cohort_act_2026-W42_regular_000__cohort_mod_2026-W42_math_000"
        )
    finally:
        app.close()


def _bucket_suffixes(cohort_id: str) -> tuple[int, int]:
    """(activity bucket, module bucket) from a cohort id ``..._<n>__..._<m>``."""
    activity, _, rest = cohort_id.partition("__")
    return int(activity.rsplit("_", 1)[1]), int(rest.partition("__")[0].rsplit("_", 1)[1])


def test_auto_buckets_use_one_activity_bucket_and_group_size_over_twice_min_size(tmp_path):
    """Not passing the bucket counts: activity 1; module max(1, group_size // (2 * min_size))."""
    app = _app(tmp_path)
    try:
        big = [_learner(app, _week(PRIOR_WEEK_START, 4)) for _ in range(24)]  # regular/math group of 24
        small = [_learner(app, _week(PRIOR_WEEK_START, 1, module="physics")) for _ in range(5)]  # casual

        result = _run(app, big + small, min_size=3, max_size=100)  # no bucket arguments

        big_buckets = {_bucket_suffixes(result[i]) for i in big}
        small_buckets = {_bucket_suffixes(result[i]) for i in small}
        assert {a for a, _ in big_buckets | small_buckets} == {0}  # one activity bucket
        assert {m for _, m in big_buckets} <= set(range(24 // (2 * 3)))  # 4 module buckets: 0..3
        assert len({m for _, m in big_buckets}) > 1  # the group really is spread over several
        assert small_buckets == {(0, 0)}  # 5 // 6 -> 1 bucket: everyone shares one cohort id
        assert len({result[i] for i in small}) == 1
    finally:
        app.close()


def test_bucket_counts_below_one_are_rejected(tmp_path):
    import pytest

    app = _app(tmp_path)
    try:
        learner_id = _learner(app, [])
        with pytest.raises(ValueError):
            _run(app, [learner_id], activity_buckets=0)
        with pytest.raises(ValueError):
            _run(app, [learner_id], module_buckets=0)
    finally:
        app.close()
