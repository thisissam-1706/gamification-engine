"""activity_buckets / module_buckets on build_weekly_cohorts (additive; defaults unchanged)."""

from src.leaderboard.cohorts import assign_activity_volume_cohort, assign_module_cohort
from tests.leaderboard.test_cohort_builder import EPOCH, PRIOR_WEEK_START, REFERENCE, _app, _learner, _week


def _run(app, ids, **kwargs):
    from src.leaderboard.cohort_builder import build_weekly_cohorts

    return build_weekly_cohorts(app, ids, EPOCH, min_size=1, reference_time=REFERENCE, **kwargs)


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


def test_default_bucket_counts_are_the_assign_functions_defaults(tmp_path):
    app = _app(tmp_path)
    try:
        learner_id = _learner(app, _week(PRIOR_WEEK_START, 4))

        implicit = _run(app, [learner_id])[learner_id]

        explicit = (
            f"{assign_activity_volume_cohort(learner_id, EPOCH, 4, num_cohorts=10)}"
            f"__{assign_module_cohort(learner_id, EPOCH, 'math', num_cohorts=5)}"
        )
        assert implicit == explicit
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
