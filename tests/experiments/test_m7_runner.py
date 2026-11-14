"""M7 step 9: shared SRM check and per-arm exposure rates (exp_007). Written before the implementation.

Assumed new code:

* ``src/experiments/srm.py`` exporting ``srm_check`` and ``SRM_ALPHA``, moved unchanged from
  ``simulator/pilot_report.py``, which must then import them from there (same objects).
* ``exposure_by_arm(application, exp_id, *, eligible_ids, as_of)`` in ``src/experiments/runner.py``
  (module name is my assumption; the brief did not name one). It reads stored events and returns an
  object whose ``.arms`` maps ``"variant_a"`` / ``"variant_b"`` to a record with ``.eligible``
  (int), ``.exposed`` (int), ``.rate`` (float | None) and ``.note`` (str | None):
  - eligible = learners in ``eligible_ids`` assigned to that arm for ``exp_id``;
  - exposed = those with at least one ExperimentExposed for ``exp_id`` with ``exposed_at <= as_of``
    (``as_of`` is an aware datetime; equality counts);
  - ``variant_a``: rate = exposed / eligible (None when eligible is 0);
  - ``variant_b``: rate is None, with a note explaining that the exposure rules give variant_b no
    treatment surface (the note must contain the word "surface").

SRM pins below were produced by running today's ``simulator.pilot_report.srm_check``.
"""

import re
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest

from src.experiments.runner import exposure_by_arm
from src.experiments.service import assign_learner_to_experiment
from src.experiments.srm import SRM_ALPHA, srm_check
from src.state_machine.learner import Learner
from tests.experiments.support import (
    ASSIGNED_AT,
    EXP_ID,
    TREATMENT_SURFACE,
    open_persisted,
    record_attempt,
    uuid_for_variant,
)

YAML = Path(__file__).resolve().parents[2] / "experiments" / "exp_007.yaml"
AS_OF = ASSIGNED_AT + timedelta(days=10)


# 1 ---------------------------------------------------------------------------------------------

SRM_PINS = [
    (
        (60, 40),
        {"chi2": 4.0, "p_value": 0.045500263896358396, "alert": False},
    ),
    (
        (520, 480),
        {"chi2": 1.6, "p_value": 0.20590321073206833, "alert": False},
    ),
    (
        (600, 400),
        {"chi2": 40.0, "p_value": 2.53962858947086e-10, "alert": True},
    ),
]


@pytest.mark.parametrize("pair, expected", SRM_PINS)
def test_srm_check_matches_the_simulators_current_results(pair, expected):
    result = srm_check(*pair)
    assert (result["assigned_variant_a"], result["assigned_variant_b"]) == pair
    assert result["chi2"] == pytest.approx(expected["chi2"], rel=1e-12)
    assert result["p_value"] == pytest.approx(expected["p_value"], rel=1e-9)
    assert result["alert"] is expected["alert"]
    assert result["test"] == "chi_square_1df"
    assert result["expected_split"] == "50/50"
    assert result["alpha"] == SRM_ALPHA


def test_srm_check_of_nothing_is_not_an_alert():
    result = srm_check(0, 0)
    assert (result["chi2"], result["p_value"], result["alert"]) == (0.0, 1.0, False)


def test_srm_alpha_matches_the_yaml_alert_threshold():
    text = YAML.read_text(encoding="utf-8")
    match = re.search(r'alert_threshold:\s*"p\s*<\s*([0-9.]+)"', text)
    assert match, "alert_threshold not found in exp_007.yaml"
    assert SRM_ALPHA == float(match.group(1)) == 0.001


# 2 ---------------------------------------------------------------------------------------------


def test_simulator_pilot_report_reuses_the_src_srm_check():
    import src.experiments.srm as shared
    from simulator import pilot_report

    assert pilot_report.srm_check is shared.srm_check
    assert pilot_report.SRM_ALPHA == shared.SRM_ALPHA


# 3 ---------------------------------------------------------------------------------------------


def _learner(application, variant: str, nth: int = 0) -> str:
    learner = Learner._create(Learner.Created, id=uuid_for_variant(variant, EXP_ID, nth=nth))
    application.save(learner)
    assert assign_learner_to_experiment(application, str(learner.id), EXP_ID, ASSIGNED_AT)
    return str(learner.id)


def _expose(application, learner_id: str, view_id: str, exposed_at) -> None:
    learner = application.repository.get(UUID(learner_id))
    assert learner.record_experiment_exposure(
        EXP_ID, TREATMENT_SURFACE, view_id=view_id, exposed_at=exposed_at
    )
    application.save(learner)


def _run(application, eligible_ids, as_of=AS_OF):
    return exposure_by_arm(application, EXP_ID, eligible_ids=set(eligible_ids), as_of=as_of)


def test_treatment_rate_is_exposed_over_eligible(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        ids = [_learner(application, "variant_a", nth=i) for i in range(4)]
        _expose(application, ids[0], "v-0", ASSIGNED_AT + timedelta(days=1))
        _expose(application, ids[1], "v-1", ASSIGNED_AT + timedelta(days=2))

        arm = _run(application, ids).arms["variant_a"]
        assert (arm.eligible, arm.exposed) == (4, 2)
        assert arm.rate == 0.5


def test_a_learner_with_several_exposures_counts_once(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        ids = [_learner(application, "variant_a", nth=i) for i in range(2)]
        for n in range(3):
            _expose(application, ids[0], f"v-{n}", ASSIGNED_AT + timedelta(days=1, hours=n))

        arm = _run(application, ids).arms["variant_a"]
        assert (arm.eligible, arm.exposed, arm.rate) == (2, 1, 0.5)


def test_only_eligible_learners_count_in_numerator_and_denominator(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        eligible = [_learner(application, "variant_a", nth=i) for i in range(2)]
        excluded = _learner(application, "variant_a", nth=2)
        _expose(application, eligible[0], "v-0", ASSIGNED_AT + timedelta(days=1))
        _expose(application, excluded, "v-x", ASSIGNED_AT + timedelta(days=1))

        arm = _run(application, eligible).arms["variant_a"]
        assert (arm.eligible, arm.exposed, arm.rate) == (2, 1, 0.5)


def test_exposures_after_as_of_do_not_count_and_equality_does(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        ids = [_learner(application, "variant_a", nth=i) for i in range(3)]
        _expose(application, ids[0], "v-before", AS_OF - timedelta(seconds=1))
        _expose(application, ids[1], "v-at", AS_OF)
        _expose(application, ids[2], "v-after", AS_OF + timedelta(seconds=1))

        arm = _run(application, ids).arms["variant_a"]
        assert (arm.eligible, arm.exposed) == (3, 2)


def test_a_learner_first_exposed_after_as_of_is_not_exposed_even_with_an_earlier_view_elsewhere(
    tmp_path,
):
    with open_persisted(tmp_path) as (application, _):
        a = _learner(application, "variant_a", nth=0)
        b = _learner(application, "variant_a", nth=1)
        _expose(application, a, "v-a", AS_OF + timedelta(days=1))
        _expose(application, b, "v-b", AS_OF - timedelta(days=1))

        arm = _run(application, [a, b]).arms["variant_a"]
        assert arm.exposed == 1


def test_control_rate_is_none_with_a_note_about_the_missing_surface(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        treat = _learner(application, "variant_a")
        controls = [_learner(application, "variant_b", nth=i) for i in range(3)]
        _expose(application, treat, "v-0", ASSIGNED_AT + timedelta(days=1))

        result = _run(application, [treat, *controls])
        control = result.arms["variant_b"]
        assert control.eligible == 3
        assert control.rate is None
        assert isinstance(control.note, str) and "surface" in control.note.lower()
        # Control has no exposure by rule: the treatment arm is unaffected by the control learners.
        assert result.arms["variant_a"].rate == 1.0


def test_treatment_rate_is_none_when_no_eligible_learner_is_in_the_arm(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        control = _learner(application, "variant_b")
        arm = _run(application, [control]).arms["variant_a"]
        assert (arm.eligible, arm.exposed, arm.rate) == (0, 0, None)


# =================================================================================================
# run_experiment (M7 step 12). Written before the implementation.
#
# Assumed: ``run_experiment(application, store, notification_store, awards, *, exp_id="exp_007",
# assessment_id, as_of)`` in ``src/experiments/runner.py`` (called here as
# ``runner_module.run_experiment`` so that only these tests fail while it is missing). It returns
# an ``ExperimentRunResult`` with
#   .valid (bool), .invalid_reasons (list[str]),
#   .arms["treatment" | "control"] -> .eligible, .mastery (a MasteryReport),
#       .breach_learner_days, .breach_learners, .breach_learner_rate,
#   .srm_assigned / .srm_eligible (the dicts srm_check returns),
#   .gaming -> .breached (bool) and .weeks ({"YYYY-Www": [CohortGamingReport, ...]}).
# Violations are read with ``notification_store.list_guardrail_violations(exp_id)``; ``awards`` are
# ``XPAwardFact``-like objects; eligibility comes from ExperimentStore rows (the runner needs a way
# to list eligible ids from the store; its name is not pinned here).
# Cap breach = a violation row; learner-day = (learner, UTC date of occurred_at).
# Reason strings: cap_breach_treatment, gaming_share_breach, srm_assigned, srm_eligible,
# missing_eligibility_records, no_eligible_learners.
# =================================================================================================

import src.experiments.runner as runner_module
from src.abuse.guardrail import XPAwardFact
from src.abuse.rules import DEFAULT_ABUSE_RULES_VERSION, get_abuse_rules
from src.experiments.metrics import mastery_report as _mastery_report
from src.experiments.store import ExperimentStore
from src.notifications.store import NotificationStore

ASSESSMENT = "asmt-1"
AS_OF_30 = ASSIGNED_AT + timedelta(days=30)
D1 = ASSIGNED_AT + timedelta(days=1)  # 2026-10-02T12:00Z


@pytest.fixture
def env(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        store = ExperimentStore(":memory:")
        nstore = NotificationStore(str(tmp_path / "notifications.db"))
        try:
            yield application, store, nstore
        finally:
            store.close()
            nstore.close()


def _elig(store, learner_id, variant, *, eligible=True, reason=None) -> None:
    store.record_eligibility(
        {
            "exp_id": EXP_ID,
            "learner_id": learner_id,
            "variant": variant,
            "assigned_at": ASSIGNED_AT.isoformat(),
            "eligible": int(eligible),
            "reason": reason,
            "opted_out_push": 0,
            "opted_out_streak_risk": 0,
            "timezone": "Asia/Kolkata",
            "tenure_days": 40,
        }
    )


def _population(env, treatment=3, control=3, *, eligible_treatment=None, eligible_control=None):
    """Assigned learners with eligibility rows; returns {"a": [...], "b": [...]} (all ids)."""
    application, store, _ = env
    ids = {"a": [], "b": []}
    for key, variant, count, keep in (
        ("a", "variant_a", treatment, eligible_treatment),
        ("b", "variant_b", control, eligible_control),
    ):
        keep = count if keep is None else keep
        for i in range(count):
            learner_id = _learner(application, variant, nth=i)
            ids[key].append(learner_id)
            _elig(
                store, learner_id, variant,
                eligible=i < keep, reason=None if i < keep else "excluded_tenure",
            )
    return ids


_violation_counter = iter(range(10_000))


def _violate(nstore, learner_id, occurred_at, *, variant="variant_a", exp_id=EXP_ID) -> None:
    n = next(_violation_counter)
    assert nstore.record_guardrail_violation(
        f"viol-{n}", exp_id, variant, learner_id, f"prop-{n}", "daily_cap_exceeded", occurred_at
    )


def _award(learner_id, occurred_at, xp, repeat_index=None) -> XPAwardFact:
    return XPAwardFact(learner_id, occurred_at, xp, repeat_index)


def _run_experiment(env, awards=(), as_of=AS_OF, **kwargs):
    application, store, nstore = env
    return runner_module.run_experiment(
        application, store, nstore, list(awards), assessment_id=ASSESSMENT, as_of=as_of, **kwargs
    )


# as_of ----------------------------------------------------------------------------------------


def test_run_experiment_requires_as_of(env):
    application, store, nstore = env
    _population(env)
    with pytest.raises(ValueError):
        runner_module.run_experiment(
            application, store, nstore, [], assessment_id=ASSESSMENT, as_of=None
        )


# 6 clean run ----------------------------------------------------------------------------------


def test_clean_run_is_valid_with_no_reasons(env):
    _population(env)
    result = _run_experiment(env)
    assert result.valid is True
    assert list(result.invalid_reasons) == []
    assert result.srm_assigned["alert"] is False and result.srm_eligible["alert"] is False
    assert result.gaming.breached is False
    assert result.arms["treatment"].eligible == 3 and result.arms["control"].eligible == 3
    assert result.arms["treatment"].breach_learner_days == 0
    assert result.arms["treatment"].breach_learner_rate == 0.0


# 1 cap ----------------------------------------------------------------------------------------


def test_cap_breaches_are_grouped_by_learner_and_utc_date(env):
    ids = _population(env)
    _, _, nstore = env
    t0, t1 = ids["a"][0], ids["a"][1]
    _violate(nstore, t0, "2026-10-02T23:30:00+00:00")
    _violate(nstore, t0, "2026-10-03T02:00:00+05:30")  # = 2026-10-02T20:30Z: same UTC day
    _violate(nstore, t0, "2026-10-03T00:30:00+00:00")  # next UTC day
    _violate(nstore, t1, "2026-10-02T10:00:00+00:00")

    arm = _run_experiment(env).arms["treatment"]
    assert arm.breach_learner_days == 3  # t0 on two UTC dates, t1 on one
    assert arm.breach_learners == 2
    assert arm.breach_learner_rate == pytest.approx(2 / 3)


def test_any_treatment_breach_invalidates_the_run_experiment(env):
    ids = _population(env)
    _violate(env[2], ids["a"][0], D1.isoformat())
    result = _run_experiment(env)
    assert result.valid is False
    assert "cap_breach_treatment" in result.invalid_reasons


def test_control_only_breach_is_reported_but_does_not_invalidate(env):
    ids = _population(env)
    _violate(env[2], ids["b"][0], D1.isoformat(), variant="variant_b")
    result = _run_experiment(env)
    control = result.arms["control"]
    assert (control.breach_learner_days, control.breach_learners) == (1, 1)
    assert control.breach_learner_rate == pytest.approx(1 / 3)
    assert result.arms["treatment"].breach_learner_days == 0
    assert result.valid is True
    assert list(result.invalid_reasons) == []


def test_arm_comes_from_the_assignment_not_the_row_variant(env):
    ids = _population(env)
    _, _, nstore = env
    # The row labels are swapped relative to the assignments in the event log.
    _violate(nstore, ids["a"][0], D1.isoformat(), variant="variant_b")
    _violate(nstore, ids["b"][0], D1.isoformat(), variant="variant_a")
    result = _run_experiment(env)
    assert result.arms["treatment"].breach_learners == 1
    assert result.arms["control"].breach_learners == 1
    assert "cap_breach_treatment" in result.invalid_reasons


def test_rows_after_as_of_and_for_ineligible_learners_and_other_experiments_are_ignored(env):
    ids = _population(env, eligible_treatment=2)  # a[2] is ineligible
    _, _, nstore = env
    _violate(nstore, ids["a"][0], (AS_OF + timedelta(seconds=1)).isoformat())
    _violate(nstore, ids["a"][2], D1.isoformat())
    _violate(nstore, ids["a"][1], D1.isoformat(), exp_id="exp_other")
    result = _run_experiment(env)
    arm = result.arms["treatment"]
    assert (arm.breach_learner_days, arm.breach_learners, arm.breach_learner_rate) == (0, 0, 0.0)
    assert arm.eligible == 2
    assert result.valid is True


# 2 gaming -------------------------------------------------------------------------------------


def test_gaming_reports_every_iso_week_from_first_assignment_through_as_of(env):
    ids = _population(env)
    result = _run_experiment(env, [_award(ids["a"][0], D1, 100, None)])
    # ASSIGNED_AT is 2026-10-01 (ISO week 40); AS_OF is 2026-10-11 (ISO week 41).
    assert sorted(result.gaming.weeks) == ["2026-W40", "2026-W41"]
    for reports in result.gaming.weeks.values():
        assert {r.cohort_id for r in reports} == {"variant_a", "variant_b"}


def test_gaming_uses_the_default_rules_threshold_and_flags_a_breach_in_either_arm(env):
    ids = _population(env)
    threshold = get_abuse_rules(DEFAULT_ABUSE_RULES_VERSION).cohort_gaming_threshold
    # Control arm: 100 XP in week 40, 40 of it on repeat completions (share 0.4 > threshold).
    awards = [
        _award(ids["b"][0], D1, 60, None),
        _award(ids["b"][1], D1, 40, 1),
        _award(ids["a"][0], D1, 100, None),
    ]
    result = _run_experiment(env, awards)
    assert result.gaming.breached is True
    assert "gaming_share_breach" in result.invalid_reasons
    week40 = {r.cohort_id: r for r in result.gaming.weeks["2026-W40"]}
    assert week40["variant_b"].breached is True and week40["variant_a"].breached is False
    assert week40["variant_b"].threshold == threshold
    assert week40["variant_b"].share == pytest.approx(0.4)


def test_share_exactly_at_the_default_threshold_is_not_a_breach(env):
    ids = _population(env)
    threshold = get_abuse_rules(DEFAULT_ABUSE_RULES_VERSION).cohort_gaming_threshold
    repeat = int(threshold * 100)  # exactly threshold share of 100 XP
    awards = [
        _award(ids["a"][0], D1, 100 - repeat, None),
        _award(ids["a"][1], D1, repeat, 1),
    ]
    result = _run_experiment(env, awards)
    assert result.gaming.breached is False
    assert "gaming_share_breach" not in result.invalid_reasons


def test_awards_of_ineligible_learners_are_not_counted_for_gaming(env):
    ids = _population(env, eligible_treatment=2)  # a[2] ineligible
    awards = [
        _award(ids["a"][0], D1, 100, None),
        _award(ids["a"][2], D1, 500, 1),  # all repeat XP, but not in cohort_of
    ]
    result = _run_experiment(env, awards)
    assert result.gaming.breached is False
    week40 = {r.cohort_id: r for r in result.gaming.weeks["2026-W40"]}
    assert week40["variant_a"].total_xp == 100


# 3 SRM ----------------------------------------------------------------------------------------


def test_srm_alert_on_assigned_counts(env):
    # 35 vs 10 assigned (alert); eligible counts are 10 vs 10 (no alert).
    _population(env, treatment=35, control=10, eligible_treatment=10)
    result = _run_experiment(env)
    assert result.srm_assigned["alert"] is True
    assert (result.srm_assigned["assigned_variant_a"], result.srm_assigned["assigned_variant_b"]) == (35, 10)
    assert result.srm_eligible["alert"] is False
    assert "srm_assigned" in result.invalid_reasons
    assert "srm_eligible" not in result.invalid_reasons
    assert result.valid is False


def test_srm_alert_on_eligible_counts(env):
    # 30 vs 30 assigned (no alert); eligible counts are 30 vs 8 (alert).
    _population(env, treatment=30, control=30, eligible_control=8)
    result = _run_experiment(env)
    assert result.srm_assigned["alert"] is False
    assert result.srm_eligible["alert"] is True
    assert (result.srm_eligible["assigned_variant_a"], result.srm_eligible["assigned_variant_b"]) == (30, 8)
    assert "srm_eligible" in result.invalid_reasons
    assert "srm_assigned" not in result.invalid_reasons


# 4 integrity ----------------------------------------------------------------------------------


def test_assigned_learner_without_eligibility_row_is_flagged(env):
    application, store, _ = env
    _population(env)
    stray = _learner(application, "variant_a", nth=10)  # assigned in the log, no row in the store
    result = _run_experiment(env)
    assert "missing_eligibility_records" in result.invalid_reasons
    assert result.valid is False


def test_no_eligible_learners_is_flagged_and_does_not_raise(env):
    _population(env, treatment=3, control=3, eligible_treatment=0, eligible_control=0)
    result = _run_experiment(env)
    assert "no_eligible_learners" in result.invalid_reasons
    assert result.valid is False
    assert result.arms["treatment"].eligible == 0 and result.arms["control"].eligible == 0


# 5 invalid run still reports mastery ------------------------------------------------------------


def test_invalid_run_still_returns_mastery_per_arm(env):
    application, _, nstore = env
    ids = _population(env)
    record_attempt(application, ids["a"][0], "att-t-pass", passed=True)
    record_attempt(application, ids["b"][0], "att-c-fail", passed=False)
    _violate(nstore, ids["a"][1], D1.isoformat())  # makes the run invalid

    result = _run_experiment(env, as_of=AS_OF_30)
    assert result.valid is False and "cap_breach_treatment" in result.invalid_reasons
    treatment, control = result.arms["treatment"].mastery, result.arms["control"].mastery
    assert (treatment.assigned, treatment.mastered, treatment.missing_outcome) == (3, 1, 2)
    assert (control.assigned, control.failed, control.missing_outcome) == (3, 1, 2)
    assert treatment.rate == pytest.approx(1 / 3) and control.rate == 0.0


# 7 mastery inputs -----------------------------------------------------------------------------


def test_mastery_counts_only_eligible_learners_and_the_given_assessment(env):
    application, _, _ = env
    ids = _population(env, treatment=3, control=3, eligible_treatment=2)  # a[2] ineligible
    record_attempt(application, ids["a"][0], "att-1", passed=True)
    record_attempt(application, ids["a"][2], "att-ineligible", passed=True)  # must not count
    record_attempt(application, ids["a"][1], "att-other", assessment_id="asmt-2", passed=True)

    treatment = _run_experiment(env, as_of=AS_OF_30).arms["treatment"].mastery
    assert treatment.assigned == 2
    assert treatment.mastered == 1
    assert treatment.missing_outcome == 1  # a[1]'s only attempt was for another assessment


def test_mastery_is_built_with_build_mastery_inputs_eligible_ids_and_assessment(env, monkeypatch):
    ids = _population(env, treatment=3, control=3, eligible_treatment=2)
    real = runner_module.build_mastery_inputs
    calls = []

    def spy(application, exp_id, **kwargs):
        calls.append((exp_id, kwargs))
        return real(application, exp_id, **kwargs)

    monkeypatch.setattr(runner_module, "build_mastery_inputs", spy)
    _run_experiment(env, as_of=AS_OF_30)

    assert calls, "build_mastery_inputs was not called"
    expected = {*ids["a"][:2], *ids["b"]}
    for exp_id, kwargs in calls:
        assert exp_id == EXP_ID
        assert kwargs["assessment_id"] == ASSESSMENT
        assert set(kwargs["eligible_ids"]) == expected


# Edge cases (M7 step 14) ----------------------------------------------------------------------


def test_zero_assigned_learners_is_invalid_without_crashing(env):
    result = _run_experiment(env)  # nothing assigned, nothing stored
    assert result.valid is False
    assert "no_eligible_learners" in result.invalid_reasons
    assert "missing_eligibility_records" not in result.invalid_reasons
    for arm in result.arms.values():
        assert arm.eligible == 0
        assert arm.mastery.rate is None
        assert arm.breach_learner_rate is None
        assert arm.exposure.rate is None
    assert result.srm_assigned["alert"] is False and result.srm_eligible["alert"] is False
    assert result.gaming.breached is False and result.gaming.weeks == {}
    result.to_dict()  # serialises too


def test_one_empty_arm_alerts_srm_and_still_reports(env):
    _population(env, treatment=12, control=0)  # 12 vs 0: chi-square 12, p < 0.001
    result = _run_experiment(env)
    assert result.srm_assigned["alert"] is True and result.srm_eligible["alert"] is True
    assert {"srm_assigned", "srm_eligible"} <= set(result.invalid_reasons)
    assert result.valid is False
    control = result.arms["control"]
    assert (control.eligible, control.mastery.rate, control.breach_learner_rate) == (0, None, None)
    assert result.arms["treatment"].eligible == 12


def test_everyone_immature_gives_no_rate_and_does_not_invalidate_the_run(env):
    application, _, _ = env
    ids = _population(env)
    record_attempt(application, ids["a"][0], "att-1", passed=True)
    result = _run_experiment(env)  # as_of is 10 days after assignment: windows are still open
    for arm in result.arms.values():
        assert arm.mastery.rate is None
        assert arm.mastery.immature == 3
    assert result.valid is True
    assert list(result.invalid_reasons) == []


def test_two_runs_on_the_same_data_are_identical(env):
    import json

    application, _, nstore = env
    ids = _population(env)
    record_attempt(application, ids["a"][0], "att-1", passed=True)
    _violate(nstore, ids["b"][0], D1.isoformat(), variant="variant_b")
    awards = [_award(ids["a"][0], D1, 100, None), _award(ids["b"][1], D1, 30, 1)]

    first = _run_experiment(env, awards, as_of=AS_OF_30)
    second = _run_experiment(env, awards, as_of=AS_OF_30)
    assert first.to_dict() == second.to_dict()
    assert json.dumps(first.to_dict()) == json.dumps(second.to_dict())
    assert json.dumps(first.to_dict(), sort_keys=True) == json.dumps(first.to_dict())


def test_share_just_above_the_default_threshold_is_a_breach(env):
    ids = _population(env)
    threshold = get_abuse_rules(DEFAULT_ABUSE_RULES_VERSION).cohort_gaming_threshold
    total = 1000
    repeat = int(threshold * total) + 1  # one XP above the limit
    awards = [
        _award(ids["a"][0], D1, total - repeat, None),
        _award(ids["a"][1], D1, repeat, 1),
    ]
    result = _run_experiment(env, awards)
    assert result.gaming.breached is True
    assert "gaming_share_breach" in result.invalid_reasons
    week40 = {r.cohort_id: r for r in result.gaming.weeks["2026-W40"]}
    assert week40["variant_a"].share > threshold


def test_two_rows_for_one_learner_on_one_utc_day_are_one_learner_day(env):
    ids = _population(env)
    _, _, nstore = env
    _violate(nstore, ids["a"][0], "2026-10-02T01:00:00+00:00")
    _violate(nstore, ids["a"][0], "2026-10-02T22:59:59+00:00")
    arm = _run_experiment(env).arms["treatment"]
    assert (arm.breach_learner_days, arm.breach_learners) == (1, 1)


def test_rows_with_variant_none_are_attributed_through_the_assignment(env):
    ids = _population(env)
    _, _, nstore = env
    _violate(nstore, ids["a"][0], D1.isoformat(), variant=None)
    _violate(nstore, ids["b"][0], D1.isoformat(), variant=None)
    result = _run_experiment(env)
    assert result.arms["treatment"].breach_learners == 1
    assert result.arms["control"].breach_learners == 1
    assert "cap_breach_treatment" in result.invalid_reasons
