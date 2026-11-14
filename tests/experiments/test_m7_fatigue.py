"""M7 fatigue metrics and the treatment-vs-control contrast on the real log. Written before the implementation.

Assumed additions (names beyond the brief are marked *):
* ``ArmResult`` gains ``messages_total``, ``messages_streak_risk``, ``learner_days_observed`` (float),
  ``messages_per_learner_per_day`` (float | None), ``max_delivered_in_24h`` (int),
  ``delivered_cap_breach_learners`` (int) and ``opt_outs`` (int | None).
* ``run_experiment(..., fatigue_cap=5, current_prefs=None)``; ``current_prefs`` maps learner id ->
  ``LearnerPreferences``.
* ``ExperimentRunResult.contrast`` = {"mastery_rate_difference", "mastery_ci95", "messages_per_learner_per_day_difference"};
  *``mastery_ci95`` is {"low": float, "high": float} (Wald, 1.96 * standard error, as the simulator's ``_contrast``
  does) or None;
  *``ExperimentRunResult.notes`` is a list of strings; when ``current_prefs`` is None it contains one saying the
  preferences were not supplied.
* Window per eligible learner = [assigned_at, min(assigned_at + 14 days, as_of)), start inclusive, end exclusive.
  Messages = rows of ``notification_dispatches`` (any channel, any category) inside it. The rolling 24 h window
  is [t, t + 24 h) starting at each counted dispatch, as in the simulator's ``max_in_rolling_24h``.
* New invalid reason: ``delivered_over_cap_treatment``.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.experiments.runner import run_experiment
from src.experiments.store import ExperimentStore
from src.notifications.models import ChannelType, LearnerPreferences, NotificationCategory
from src.notifications.store import NotificationStore
from tests.experiments.support import (
    ASSESSMENT_ID,
    ASSIGNED_AT,
    EXP_ID,
    eligibility_row,
    make_learner,
    open_persisted,
    record_attempt,
)

ROOT = Path(__file__).resolve().parents[2]
AS_OF = ASSIGNED_AT + timedelta(days=10)
AS_OF_30 = ASSIGNED_AT + timedelta(days=30)
D1 = ASSIGNED_AT + timedelta(days=1)
CREATED_OLD = datetime(2026, 9, 1, tzinfo=timezone.utc)


@pytest.fixture
def env(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        store = ExperimentStore(":memory:")
        nstore = NotificationStore(str(tmp_path / "notifications.db"))
        try:
            yield SimpleNamespace(application=application, store=store, nstore=nstore)
        finally:
            store.close()
            nstore.close()


def _population(env, n_a=2, n_b=2, *, eligible_a=None, eligible_b=None) -> dict[str, list[str]]:
    ids: dict[str, list[str]] = {"a": [], "b": []}
    for key, variant, count, keep in (
        ("a", "variant_a", n_a, eligible_a),
        ("b", "variant_b", n_b, eligible_b),
    ):
        keep = count if keep is None else keep
        for i in range(count):
            learner_id = make_learner(env.application, variant, nth=i, created=CREATED_OLD)
            ids[key].append(learner_id)
            env.store.record_eligibility(
                eligibility_row(
                    learner_id, variant, eligible=i < keep, reason=None if i < keep else "excluded_tenure"
                )
            )
    return ids


def _late_learner(env, variant: str, nth: int, assigned_at: datetime) -> str:
    """An eligible learner assigned at `assigned_at` (use a time after as_of for a late one)."""
    learner_id = make_learner(
        env.application, variant, nth=nth, created=CREATED_OLD, assigned_at=assigned_at
    )
    env.store.record_eligibility(eligibility_row(learner_id, variant))
    return learner_id


def _dispatch(
    nstore,
    learner_id: str,
    at: datetime,
    *,
    channel: ChannelType = ChannelType.PUSH,
    category: NotificationCategory = NotificationCategory.STREAK_RISK,
) -> None:
    nstore.record_dispatch(
        uuid4().hex, uuid4().hex, learner_id, channel, category, at.isoformat(), {}
    )


def _burst(nstore, learner_id: str, count: int, start: datetime = D1, step_minutes: int = 10) -> None:
    for k in range(count):
        _dispatch(nstore, learner_id, start + timedelta(minutes=step_minutes * k))


def _run(env, as_of=AS_OF, **kwargs):
    return run_experiment(
        env.application, env.store, env.nstore, [], assessment_id=ASSESSMENT_ID, as_of=as_of, **kwargs
    )


def _prefs(learner_id: str, **kwargs) -> LearnerPreferences:
    return LearnerPreferences(learner_id, timezone="Asia/Kolkata", **kwargs)


# 1 messages and learner-days ------------------------------------------------------------------------


def test_messages_are_counted_across_channels_and_categories_with_per_day_rate(env):
    ids = _population(env)  # 2 treatment + 2 control, window = 10 days each
    n = env.nstore
    _dispatch(n, ids["a"][0], D1)  # push streak_risk
    _dispatch(n, ids["a"][0], D1 + timedelta(hours=1), channel=ChannelType.EMAIL)  # email streak_risk
    _dispatch(n, ids["a"][0], D1 + timedelta(hours=2), channel=ChannelType.IN_APP, category=NotificationCategory.GENERAL)
    _dispatch(n, ids["b"][0], D1, category=NotificationCategory.GENERAL)
    _dispatch(n, ids["b"][0], D1 + timedelta(hours=3))

    result = _run(env)
    t, c = result.arms["treatment"], result.arms["control"]
    assert (t.messages_total, t.messages_streak_risk) == (3, 2)
    assert (c.messages_total, c.messages_streak_risk) == (2, 1)
    assert t.learner_days_observed == pytest.approx(20.0)
    assert c.learner_days_observed == pytest.approx(20.0)
    assert t.messages_per_learner_per_day == pytest.approx(3 / 20)
    assert c.messages_per_learner_per_day == pytest.approx(2 / 20)


def test_window_start_is_inclusive_and_end_is_exclusive(env):
    ids = _population(env, n_a=1, n_b=1)
    n = env.nstore
    for at in (
        ASSIGNED_AT - timedelta(seconds=1),  # before the window
        ASSIGNED_AT,  # first instant of the window: counted
        AS_OF - timedelta(seconds=1),  # last second: counted
        AS_OF,  # as_of itself: not counted
        AS_OF + timedelta(days=1),
    ):
        _dispatch(n, ids["a"][0], at)
    arm = _run(env).arms["treatment"]
    assert arm.messages_total == 2
    assert arm.learner_days_observed == pytest.approx(10.0)


def test_the_window_is_capped_at_14_days_after_assignment(env):
    ids = _population(env, n_a=2, n_b=1)
    n = env.nstore
    _dispatch(n, ids["a"][0], ASSIGNED_AT + timedelta(days=14) - timedelta(seconds=1))  # counted
    _dispatch(n, ids["a"][0], ASSIGNED_AT + timedelta(days=14))  # not counted
    _dispatch(n, ids["a"][0], ASSIGNED_AT + timedelta(days=20))  # not counted
    arm = _run(env, as_of=AS_OF_30).arms["treatment"]
    assert arm.messages_total == 1
    assert arm.learner_days_observed == pytest.approx(28.0)  # 2 learners x 14 days
    assert arm.messages_per_learner_per_day == pytest.approx(1 / 28)


def test_a_learner_assigned_after_as_of_contributes_no_days_and_no_messages(env):
    ids = _population(env, n_a=1, n_b=1)
    late = _late_learner(env, "variant_a", nth=5, assigned_at=AS_OF + timedelta(days=1))
    n = env.nstore
    _dispatch(n, ids["a"][0], D1)
    _dispatch(n, late, D1)  # before its own assignment, but before as_of
    _dispatch(n, late, AS_OF + timedelta(days=1, hours=1))  # after its assignment, but after as_of

    arm = _run(env).arms["treatment"]
    assert arm.eligible == 2  # still an eligible learner of the arm
    assert arm.messages_total == 1
    assert arm.learner_days_observed == pytest.approx(10.0)
    assert arm.messages_per_learner_per_day == pytest.approx(1 / 10)


def test_an_arm_with_no_observed_days_has_no_rate(env):
    _population(env, n_a=1, n_b=0)
    late = _late_learner(env, "variant_b", nth=3, assigned_at=AS_OF + timedelta(days=1))
    _dispatch(env.nstore, late, AS_OF + timedelta(days=2))
    control = _run(env).arms["control"]
    assert control.eligible == 1
    assert control.learner_days_observed == 0.0
    assert control.messages_total == 0
    assert control.messages_per_learner_per_day is None


def test_dispatches_of_non_eligible_learners_are_not_counted(env):
    ids = _population(env, n_a=3, n_b=1, eligible_a=2)  # a[2] is ineligible
    _burst(env.nstore, ids["a"][2], 3)
    _dispatch(env.nstore, ids["a"][0], D1)
    arm = _run(env).arms["treatment"]
    assert arm.messages_total == 1
    assert arm.learner_days_observed == pytest.approx(20.0)  # only the two eligible learners


def test_an_arm_without_dispatches_reports_zero_messages_and_a_zero_rate(env):
    _population(env)
    arm = _run(env).arms["treatment"]
    assert (arm.messages_total, arm.messages_streak_risk) == (0, 0)
    assert arm.messages_per_learner_per_day == 0.0
    assert arm.max_delivered_in_24h == 0 and arm.delivered_cap_breach_learners == 0


# 2 delivered cap ----------------------------------------------------------------------------------------


def test_max_in_24h_and_breach_learners_are_per_learner(env):
    ids = _population(env)
    n = env.nstore
    _burst(n, ids["a"][0], 6)  # 6 within 24 h: over the cap of 5
    _burst(n, ids["a"][1], 5)  # exactly 5: not a breach
    _dispatch(n, ids["a"][1], D1 + timedelta(hours=24))  # [t, t+24h) excludes this one from the first window
    _burst(n, ids["b"][0], 7)

    result = _run(env)
    t, c = result.arms["treatment"], result.arms["control"]
    assert (t.max_delivered_in_24h, t.delivered_cap_breach_learners) == (6, 1)
    assert (c.max_delivered_in_24h, c.delivered_cap_breach_learners) == (7, 1)


def test_a_treatment_delivered_cap_breach_invalidates_the_run(env):
    ids = _population(env)
    _burst(env.nstore, ids["a"][0], 6)
    result = _run(env)
    assert result.valid is False
    assert list(result.invalid_reasons) == ["delivered_over_cap_treatment"]


def test_a_control_only_delivered_cap_breach_is_reported_but_does_not_invalidate(env):
    ids = _population(env)
    _burst(env.nstore, ids["b"][0], 8)
    result = _run(env)
    assert result.arms["control"].delivered_cap_breach_learners == 1
    assert result.arms["control"].max_delivered_in_24h == 8
    assert result.valid is True and list(result.invalid_reasons) == []


def test_exactly_the_cap_is_not_a_breach_and_fatigue_cap_is_a_keyword(env):
    ids = _population(env)
    _burst(env.nstore, ids["a"][0], 5)
    assert _run(env).arms["treatment"].delivered_cap_breach_learners == 0  # default cap 5
    stricter = _run(env, fatigue_cap=4)
    assert stricter.arms["treatment"].delivered_cap_breach_learners == 1
    assert "delivered_over_cap_treatment" in stricter.invalid_reasons
    looser = _run(env, fatigue_cap=7)
    assert looser.arms["treatment"].delivered_cap_breach_learners == 0


def test_only_dispatches_inside_the_window_feed_the_24h_maximum(env):
    ids = _population(env, n_a=1, n_b=1)
    _burst(env.nstore, ids["a"][0], 6, start=ASSIGNED_AT - timedelta(hours=2))  # 6 within 24 h, all before the window
    arm = _run(env).arms["treatment"]
    assert arm.messages_total == 0 and arm.max_delivered_in_24h == 0
    assert arm.delivered_cap_breach_learners == 0


# 3 opt-outs ------------------------------------------------------------------------------------------------


def _snapshot_opted_out_push(env, learner_id: str) -> None:
    env.store.connection.execute(
        "UPDATE experiment_eligibility SET opted_out_push = 1 WHERE learner_id = ?", (learner_id,)
    )
    env.store.connection.commit()


def test_opt_outs_count_eligible_learners_who_opted_out_after_the_snapshot(env):
    ids = _population(env, n_a=5, n_b=3, eligible_a=4)  # a[4] ineligible
    _snapshot_opted_out_push(env, ids["b"][2])  # already opted out when eligibility was recorded
    current = {
        ids["a"][0]: _prefs(ids["a"][0], opted_out_channels={ChannelType.PUSH}),
        ids["a"][1]: _prefs(ids["a"][1], opted_out_categories={NotificationCategory.STREAK_RISK}),
        ids["a"][2]: _prefs(ids["a"][2]),
        ids["a"][3]: _prefs(ids["a"][3], opted_out_channels={ChannelType.EMAIL}),  # email only: not an opt-out here
        ids["a"][4]: _prefs(ids["a"][4], opted_out_channels={ChannelType.PUSH}),  # ineligible: not counted
        ids["b"][0]: _prefs(ids["b"][0], opted_out_channels={ChannelType.PUSH}),
        # ids["b"][1] is missing from current_prefs: not counted
        ids["b"][2]: _prefs(ids["b"][2], opted_out_channels={ChannelType.PUSH}),  # snapshot already opted out
    }
    result = _run(env, current_prefs=current)
    assert result.arms["treatment"].opt_outs == 2
    assert result.arms["control"].opt_outs == 1
    assert not any("not supplied" in note for note in result.notes)


def test_without_current_prefs_opt_outs_is_none_with_a_note(env):
    _population(env)
    result = _run(env)
    assert result.arms["treatment"].opt_outs is None and result.arms["control"].opt_outs is None
    assert any("not supplied" in note and "pref" in note.lower() for note in result.notes)


def test_empty_current_prefs_means_zero_opt_outs_not_unknown(env):
    _population(env)
    result = _run(env, current_prefs={})
    assert result.arms["treatment"].opt_outs == 0 and result.arms["control"].opt_outs == 0
    assert not any("not supplied" in note for note in result.notes)


# 4 contrast ----------------------------------------------------------------------------------------------


def test_contrast_has_the_three_documented_fields_with_wald_interval(env):
    import math

    ids = _population(env, n_a=3, n_b=3)
    for learner_id, attempt_id, passed in (
        (ids["a"][0], "att-a0", True), (ids["a"][1], "att-a1", True),
        (ids["b"][0], "att-b0", True), (ids["b"][1], "att-b1", False),
    ):
        record_attempt(env.application, learner_id, attempt_id, passed=passed)
    _burst(env.nstore, ids["a"][0], 3)
    _dispatch(env.nstore, ids["b"][0], D1)

    result = _run(env, as_of=AS_OF_30)
    p1, p0, n1, n0 = 2 / 3, 1 / 3, 3, 3  # missing outcomes count as not mastered
    se = math.sqrt(p1 * (1 - p1) / n1 + p0 * (1 - p0) / n0)
    contrast = result.contrast
    assert set(contrast) == {
        "mastery_rate_difference", "mastery_ci95", "messages_per_learner_per_day_difference",
    }
    assert contrast["mastery_rate_difference"] == pytest.approx(p1 - p0)
    assert contrast["mastery_ci95"]["low"] == pytest.approx((p1 - p0) - 1.96 * se)
    assert contrast["mastery_ci95"]["high"] == pytest.approx((p1 - p0) + 1.96 * se)
    # 3 messages over 3 learners x 14 days for treatment, 1 over the same for control
    assert contrast["messages_per_learner_per_day_difference"] == pytest.approx(3 / 42 - 1 / 42)


def test_contrast_mastery_parts_are_none_when_a_denominator_is_zero(env):
    ids = _population(env, n_a=3, n_b=3, eligible_b=0)  # nobody eligible in control
    _burst(env.nstore, ids["a"][0], 2)
    contrast = _run(env, as_of=AS_OF_30).contrast
    assert contrast["mastery_rate_difference"] is None
    assert contrast["mastery_ci95"] is None
    assert contrast["messages_per_learner_per_day_difference"] is None  # control has no observed days


def test_contrast_mastery_parts_are_none_while_everyone_is_immature_but_messages_still_compare(env):
    ids = _population(env, n_a=2, n_b=2)
    _burst(env.nstore, ids["a"][0], 4)
    _dispatch(env.nstore, ids["b"][0], D1)
    contrast = _run(env, as_of=AS_OF).contrast  # 10 days: every learner's 14-day window is still open
    assert contrast["mastery_rate_difference"] is None
    assert contrast["mastery_ci95"] is None
    assert contrast["messages_per_learner_per_day_difference"] == pytest.approx(4 / 20 - 1 / 20)


# 5 to_dict -------------------------------------------------------------------------------------------------


NEW_ARM_FIELDS = {
    "messages_total", "messages_streak_risk", "learner_days_observed", "messages_per_learner_per_day",
    "max_delivered_in_24h", "delivered_cap_breach_learners", "opt_outs",
}


def _walk(value, path="$"):
    yield path, value
    if isinstance(value, dict):
        keys = list(value)
        assert all(isinstance(k, str) for k in keys), path
        assert keys == sorted(keys), f"keys not sorted at {path}: {keys}"
        for key in keys:
            yield from _walk(value[key], f"{path}.{key}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _walk(item, f"{path}[{i}]")


def _fatigue_world(env):
    ids = _population(env, n_a=3, n_b=3)
    record_attempt(env.application, ids["a"][0], "att-1", passed=True)
    record_attempt(env.application, ids["b"][0], "att-2", passed=False)
    _burst(env.nstore, ids["a"][0], 3)
    _burst(env.nstore, ids["b"][1], 6)
    return {ids["a"][0]: _prefs(ids["a"][0], opted_out_channels={ChannelType.PUSH})}


def test_new_fields_are_in_to_dict_json_clean_and_sorted(env):
    current = _fatigue_world(env)
    data = _run(env, as_of=AS_OF_30, current_prefs=current).to_dict()

    text = json.dumps(data, sort_keys=True)
    assert json.loads(text) == data and json.dumps(data) == text
    allowed = (dict, list, str, int, float, bool, type(None))
    for path, node in _walk(data):
        assert type(node) in allowed, f"{path}: {type(node).__name__}"
    for arm in data["arms"].values():
        assert NEW_ARM_FIELDS <= set(arm)
    assert data["arms"]["treatment"]["messages_total"] == 3
    assert data["arms"]["treatment"]["opt_outs"] == 1
    assert data["arms"]["control"]["delivered_cap_breach_learners"] == 1
    assert set(data["contrast"]) == {
        "mastery_rate_difference", "mastery_ci95", "messages_per_learner_per_day_difference",
    }
    assert isinstance(data["notes"], list)


def test_to_dict_with_unsupplied_prefs_has_null_opt_outs_and_is_deterministic(env):
    _fatigue_world(env)
    first = json.dumps(_run(env, as_of=AS_OF_30).to_dict(), sort_keys=True)
    second = json.dumps(_run(env, as_of=AS_OF_30).to_dict(), sort_keys=True)
    assert first == second
    data = json.loads(first)
    assert data["arms"]["treatment"]["opt_outs"] is None
    assert any("not supplied" in note for note in data["notes"])


# src must not import the simulator -----------------------------------------------------------------------------


def test_src_does_not_import_from_the_simulator():
    offenders = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("import simulator", "from simulator")):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {stripped}")
    assert offenders == []


# Edge cases (M7 fatigue, step 2) -----------------------------------------------------------------------


def test_a_dispatch_exactly_at_assigned_at_counts_and_one_exactly_14_days_later_does_not(env):
    ids = _population(env, n_a=1, n_b=1)
    _dispatch(env.nstore, ids["a"][0], ASSIGNED_AT)
    _dispatch(env.nstore, ids["a"][0], ASSIGNED_AT + timedelta(days=14))
    arm = _run(env, as_of=AS_OF_30).arms["treatment"]
    assert arm.messages_total == 1
    assert arm.learner_days_observed == pytest.approx(14.0)


@pytest.mark.parametrize("cap", [1, 3, 5])
def test_exactly_the_cap_in_24h_is_not_a_breach_and_one_more_is(env, cap):
    ids = _population(env, n_a=2, n_b=1)
    _burst(env.nstore, ids["a"][0], cap)  # exactly the cap
    _burst(env.nstore, ids["a"][1], cap + 1)  # one over
    arm = _run(env, fatigue_cap=cap).arms["treatment"]
    assert arm.delivered_cap_breach_learners == 1
    assert arm.max_delivered_in_24h == cap + 1


def test_the_cap_boundary_with_the_default_cap(env):
    ids = _population(env, n_a=2, n_b=1)
    _burst(env.nstore, ids["a"][0], 5)
    _burst(env.nstore, ids["a"][1], 6)
    result = _run(env)
    assert result.arms["treatment"].delivered_cap_breach_learners == 1
    assert "delivered_over_cap_treatment" in result.invalid_reasons


def test_two_dispatches_exactly_24h_apart_are_in_different_windows(env):
    ids = _population(env, n_a=2, n_b=1)
    _dispatch(env.nstore, ids["a"][0], D1)
    _dispatch(env.nstore, ids["a"][0], D1 + timedelta(hours=24))
    # one second short of 24 h: the same window
    _dispatch(env.nstore, ids["a"][1], D1)
    _dispatch(env.nstore, ids["a"][1], D1 + timedelta(hours=24) - timedelta(seconds=1))

    assert _run(env, fatigue_cap=1).arms["treatment"].max_delivered_in_24h == 2  # from a[1]
    from src.experiments.fatigue import max_in_rolling_24h

    assert max_in_rolling_24h([D1, D1 + timedelta(hours=24)]) == 1
    assert max_in_rolling_24h([D1, D1 + timedelta(hours=24) - timedelta(seconds=1)]) == 2


def test_exactly_24h_apart_does_not_breach_a_cap_of_one(env):
    ids = _population(env, n_a=1, n_b=1)
    _dispatch(env.nstore, ids["a"][0], D1)
    _dispatch(env.nstore, ids["a"][0], D1 + timedelta(hours=24))
    arm = _run(env, fatigue_cap=1).arms["treatment"]
    assert (arm.max_delivered_in_24h, arm.delivered_cap_breach_learners) == (1, 0)


def test_a_half_observed_window_contributes_half_the_days(env):
    _population(env, n_a=2, n_b=1)
    half = _run(env, as_of=ASSIGNED_AT + timedelta(days=7)).arms["treatment"]
    assert half.learner_days_observed == pytest.approx(14.0)  # 2 learners x 7 of 14 days
    twelve_hours = _run(env, as_of=ASSIGNED_AT + timedelta(hours=12)).arms["treatment"]
    assert twelve_hours.learner_days_observed == pytest.approx(1.0)  # 2 learners x 0.5 day


def test_learners_with_different_observed_lengths_add_up(env):
    _population(env, n_a=1, n_b=1)
    # a second treatment learner assigned 4 days later has only 6 days observed at as_of (day 10)
    _late_learner(env, "variant_a", nth=7, assigned_at=ASSIGNED_AT + timedelta(days=4))
    arm = _run(env).arms["treatment"]
    assert arm.learner_days_observed == pytest.approx(10.0 + 6.0)


def test_identical_arms_give_a_zero_contrast_and_an_interval_containing_zero(env):
    ids = _population(env, n_a=3, n_b=3)
    for key in ("a", "b"):
        record_attempt(env.application, ids[key][0], f"att-{key}0", passed=True)
        record_attempt(env.application, ids[key][1], f"att-{key}1", passed=False)
        for learner_id in ids[key]:
            _burst(env.nstore, learner_id, 2)
    contrast = _run(env, as_of=AS_OF_30).contrast
    assert contrast["mastery_rate_difference"] == pytest.approx(0.0)
    assert contrast["messages_per_learner_per_day_difference"] == pytest.approx(0.0)
    interval = contrast["mastery_ci95"]
    assert interval["low"] <= 0.0 <= interval["high"]
    assert interval["low"] == pytest.approx(-interval["high"])  # symmetric around the zero difference


def test_zero_eligible_learners_give_zeros_and_nones_without_dividing(env):
    ids = _population(env, n_a=3, n_b=3, eligible_a=0, eligible_b=0)
    _burst(env.nstore, ids["a"][0], 7)  # dispatches of ineligible learners are ignored
    result = _run(env)
    for arm in result.arms.values():
        assert arm.eligible == 0
        assert (arm.messages_total, arm.messages_streak_risk) == (0, 0)
        assert arm.learner_days_observed == 0.0
        assert arm.messages_per_learner_per_day is None
        assert (arm.max_delivered_in_24h, arm.delivered_cap_breach_learners) == (0, 0)
        assert arm.opt_outs is None  # preferences were not supplied
    assert result.contrast == {
        "mastery_rate_difference": None,
        "mastery_ci95": None,
        "messages_per_learner_per_day_difference": None,
    }
    assert "no_eligible_learners" in result.invalid_reasons
    assert "delivered_over_cap_treatment" not in result.invalid_reasons
    json.dumps(result.to_dict(), sort_keys=True)


def test_zero_eligible_learners_with_current_prefs_gives_zero_opt_outs(env):
    _population(env, n_a=2, n_b=2, eligible_a=0, eligible_b=0)
    result = _run(env, current_prefs={})
    assert all(arm.opt_outs == 0 for arm in result.arms.values())
    assert result.notes == []


def test_an_empty_log_gives_zeros_and_nones_without_dividing(env):
    result = _run(env)
    for arm in result.arms.values():
        assert (arm.eligible, arm.messages_total, arm.max_delivered_in_24h) == (0, 0, 0)
        assert arm.messages_per_learner_per_day is None and arm.learner_days_observed == 0.0
    assert result.contrast["mastery_ci95"] is None


# the pure helpers (src/experiments/fatigue.py) -----------------------------------------------------------------


def test_observation_window_pure_function():
    from src.experiments.fatigue import observation_window, window_days

    start = ASSIGNED_AT
    assert observation_window(start, start + timedelta(days=30)) == (start, start + timedelta(days=14))
    assert observation_window(start, start + timedelta(days=3)) == (start, start + timedelta(days=3))
    assert observation_window(start, start) is None  # assigned exactly at as_of: empty
    assert observation_window(start, start - timedelta(days=1)) is None
    assert window_days(observation_window(start, start + timedelta(hours=6))) == pytest.approx(0.25)
    assert window_days(None) == 0.0
