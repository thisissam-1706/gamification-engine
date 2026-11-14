"""M7 hardening tests. Written before the fixes; some already pass on today's code.

Shared helpers live in tests/experiments/support.py (no helper is imported from another test file).

Assumptions the brief does not fix (also listed in docs/audit/m7-harden.md):
* test 3: ``MasteryInputs.ignored_unassigned_attempts`` counts CheckpointAttempted events of learners with
  no ExperimentAssigned for exp_id (any assessment). Assigned learners removed by ``eligible_ids`` are
  not "unassigned" and are not counted there.
* test 9: a path that exists but is not SQLite raises ``ValueError`` whose message contains the path.
* test 10: ``assessment_version`` is a keyword-only int, default None, on ``build_mastery_inputs`` and
  ``run_experiment``; attempts of OTHER assessments never count towards a version mix; the mix is judged
  on the attempts that remain after the eligibility filter.
"""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.abuse.service import AbuseDetectionService
from src.abuse.store import AbuseSignalStore
from src.experiments.eligibility import evaluate_eligibility
import src.experiments.inputs as inputs_module
import src.experiments.runner as runner_module
from src.experiments.inputs import build_mastery_inputs, read_assignments
from src.experiments.runner import awards_from_abuse_service, exposure_by_arm, run_experiment
from src.experiments.service import assign_and_record_eligibility
from src.experiments.store import ExperimentStore
from src.notifications.models import ChannelType, LearnerPreferences, NotificationCategory
from src.notifications.store import NotificationStore
from src.notifications.streak_risk import run_streak_risk_sweep
from src.scale_and_recovery.experiment_repro import verify_engine_reproduction
from src.state_machine.learner import Learner, LearnerApplication
from tests.experiments.support import (
    ASSESSMENT_ID,
    ASSIGNED_AT,
    EXP_ID,
    NeverDispatcher,
    make_learner,
    decision_rows,
    eligibility_row,
    open_persisted,
    record_attempt,
    record_violation,
    xp_award,
)

AS_OF = ASSIGNED_AT + timedelta(days=10)
AS_OF_30 = ASSIGNED_AT + timedelta(days=30)
D1 = ASSIGNED_AT + timedelta(days=1)
NOW = datetime(2026, 10, 12, 15, 40, tzinfo=timezone.utc)  # 21:10 in Asia/Kolkata
CREATED_OLD = datetime(2026, 9, 1, tzinfo=timezone.utc)


@pytest.fixture
def env(tmp_path):
    with open_persisted(tmp_path) as (application, evidence):
        store = ExperimentStore(":memory:")
        nstore = NotificationStore(str(tmp_path / "notifications.db"))
        try:
            yield SimpleNamespace(
                application=application, evidence=evidence, store=store, nstore=nstore
            )
        finally:
            store.close()
            nstore.close()


def _population(env, n_a=3, n_b=3, *, eligible_a=None, eligible_b=None) -> dict[str, list[str]]:
    """Assigned learners with stored eligibility rows; returns {"a": [...], "b": [...]}."""
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


def _run(env, awards=(), as_of=AS_OF, **kwargs):
    return run_experiment(
        env.application, env.store, env.nstore, list(awards),
        assessment_id=ASSESSMENT_ID, as_of=as_of, **kwargs,
    )


def _sweep(env, learner_ids, prefs, *, store=None):
    return run_streak_risk_sweep(
        env.application, NeverDispatcher(), learner_ids, prefs, now_utc=NOW, store=store
    )


def _prefs(learner_id, **kwargs) -> LearnerPreferences:
    return LearnerPreferences(learner_id, timezone="Asia/Kolkata", **kwargs)


# 1 precedence -------------------------------------------------------------------------------------

YOUNG = NOW - timedelta(days=1)
OPT_OUTS = [
    {"opted_out_channels": {ChannelType.PUSH}},
    {"opted_out_categories": {NotificationCategory.STREAK_RISK}},
]


@pytest.mark.parametrize("opt_out", OPT_OUTS, ids=["push", "streak_risk"])
def test_tenure_beats_opt_out_in_evaluate_eligibility(opt_out):
    prefs = _prefs("L1", **opt_out)
    assert evaluate_eligibility(YOUNG, prefs, NOW) == (False, "excluded_tenure")
    # the same learner, old enough, is excluded for the opt-out instead
    assert evaluate_eligibility(CREATED_OLD, prefs, NOW) == (False, "excluded_opted_out")


@pytest.mark.parametrize("created", [YOUNG, CREATED_OLD], ids=["young", "old"])
def test_missing_prefs_beats_tenure_and_opt_out_in_evaluate_eligibility(created):
    assert evaluate_eligibility(created, None, NOW) == (False, "missing_preferences")


@pytest.mark.parametrize("opt_out", OPT_OUTS, ids=["push", "streak_risk"])
def test_sweep_without_store_reports_tenure_before_opt_out(env, opt_out):
    learner_id = make_learner(env.application, "variant_a", created=YOUNG, assigned_at=YOUNG)
    result = _sweep(env, [learner_id], {learner_id: _prefs(learner_id, **opt_out)})
    assert result.outcomes[0].reason == "excluded_tenure"


@pytest.mark.parametrize("opt_out", OPT_OUTS, ids=["push", "streak_risk"])
def test_sweep_with_stored_eligibility_reports_ineligible_tenure(env, opt_out):
    learner_id = make_learner(env.application, "variant_a", created=YOUNG, assigned_at=None)
    prefs = {learner_id: _prefs(learner_id, **opt_out)}
    assigned_at = NOW - timedelta(hours=12)
    assign_and_record_eligibility(env.application, env.store, [learner_id], prefs, EXP_ID, assigned_at)
    assert env.store.get_eligibility(EXP_ID, learner_id) == (False, "excluded_tenure")  # the stored reason

    result = _sweep(env, [learner_id], prefs, store=env.store)
    assert result.outcomes[0].reason == "ineligible:excluded_tenure"
    (row,) = decision_rows(env.store)
    assert (row["outcome"], row["reason"]) == ("skipped", "ineligible:excluded_tenure")


def test_missing_prefs_beats_both_in_the_sweep_with_and_without_a_store(env):
    learner_id = make_learner(env.application, "variant_a", created=YOUNG, assigned_at=None)
    assign_and_record_eligibility(
        env.application, env.store, [learner_id], {learner_id: _prefs(learner_id, opted_out_channels={ChannelType.PUSH})},
        EXP_ID, NOW - timedelta(hours=12),
    )
    # (stored row says excluded_tenure; the sweep is now called WITHOUT preferences for the learner)
    without_store = _sweep(env, [learner_id], {})
    with_store = _sweep(env, [learner_id], {}, store=env.store)
    assert without_store.outcomes[0].reason == "missing_preferences"
    assert with_store.outcomes[0].reason == "missing_preferences"


def test_missing_prefs_is_also_what_the_assignment_step_stores(env):
    learner_id = make_learner(env.application, "variant_a", created=YOUNG, assigned_at=None)
    assign_and_record_eligibility(env.application, env.store, [learner_id], {}, EXP_ID, NOW - timedelta(hours=12))
    assert env.store.get_eligibility(EXP_ID, learner_id) == (False, "missing_preferences")


# 2 sweep decision rows -----------------------------------------------------------------------------


def test_unassigned_learner_gets_an_unassigned_row_with_the_sweep_reason(env):
    learner_id = make_learner(env.application, "variant_a", created=CREATED_OLD, assigned_at=None)
    result = _sweep(env, [learner_id], {learner_id: _prefs(learner_id)}, store=env.store)
    assert result.outcomes[0].reason == "not_treatment"
    (row,) = decision_rows(env.store)
    assert row["variant"] == "unassigned"
    assert (row["outcome"], row["reason"]) == ("skipped", "not_treatment")


def test_stored_ineligible_control_learner_is_ineligible_not_not_treatment(env):
    control = make_learner(env.application, "variant_b", created=CREATED_OLD)
    env.store.record_eligibility(
        eligibility_row(control, "variant_b", eligible=False, reason="excluded_opted_out")
    )
    prefs = {control: _prefs(control)}

    stored = _sweep(env, [control], prefs, store=env.store)
    assert stored.outcomes[0].reason == "ineligible:excluded_opted_out"
    (row,) = decision_rows(env.store)
    assert row["variant"] == "variant_b"
    assert row["reason"] == "ineligible:excluded_opted_out"

    # Without a stored row the same control learner is simply not in the treatment arm.
    assert _sweep(env, [control], prefs).outcomes[0].reason == "not_treatment"


# 3 build_mastery_inputs counts ---------------------------------------------------------------------


def test_ignored_other_assessment_count_only_covers_learners_left_after_filtering(env):
    keep = make_learner(env.application, "variant_a", nth=0)
    drop = make_learner(env.application, "variant_b", nth=0)
    record_attempt(env.application, keep, "att-keep-1", assessment_id="asmt-2")
    record_attempt(env.application, drop, "att-drop-1", assessment_id="asmt-2")
    record_attempt(env.application, drop, "att-drop-2", assessment_id="asmt-3")

    everyone = build_mastery_inputs(env.application, EXP_ID, assessment_id=ASSESSMENT_ID)
    filtered = build_mastery_inputs(env.application, EXP_ID, assessment_id=ASSESSMENT_ID, eligible_ids={keep})
    assert everyone.ignored_other_assessment_attempts == 3
    assert filtered.ignored_other_assessment_attempts == 1


def test_attempts_of_unassigned_learners_are_dropped_and_counted_separately(env):
    assigned = make_learner(env.application, "variant_a", nth=0)
    filtered_out = make_learner(env.application, "variant_b", nth=0)
    unassigned = make_learner(env.application, "variant_a", nth=1, assigned_at=None)
    record_attempt(env.application, assigned, "att-ok")
    record_attempt(env.application, filtered_out, "att-filtered")
    record_attempt(env.application, unassigned, "att-u1")
    record_attempt(env.application, unassigned, "att-u2")

    inputs = build_mastery_inputs(env.application, EXP_ID, assessment_id=ASSESSMENT_ID, eligible_ids={assigned})
    in_any_list = {row["attempt_id"] for row in inputs.attempts} | {row["attempt_id"] for row in inputs.completions}
    assert in_any_list == {"att-ok"}
    assert set(inputs.assignments) == set(inputs.variants) == {assigned}
    assert inputs.ignored_unassigned_attempts == 2  # only the unassigned learner's attempts
    assert inputs.ignored_other_assessment_attempts == 0

    unfiltered = build_mastery_inputs(env.application, EXP_ID, assessment_id=ASSESSMENT_ID)
    assert unfiltered.ignored_unassigned_attempts == 2
    assert {row["attempt_id"] for row in unfiltered.attempts} == {"att-ok", "att-filtered"}


# 4 cap breach reason -------------------------------------------------------------------------------


def test_only_daily_cap_exceeded_rows_count_as_cap_breaches(env):
    ids = _population(env)
    record_violation(env.nstore, ids["a"][0], D1.isoformat(), reason="some_other_guardrail")
    result = _run(env)
    arm = result.arms["treatment"]
    assert (arm.breach_learner_days, arm.breach_learners) == (0, 0)
    assert result.valid is True and list(result.invalid_reasons) == []

    record_violation(env.nstore, ids["a"][1], D1.isoformat())  # the real reason
    counted = _run(env).arms["treatment"]
    assert (counted.breach_learner_days, counted.breach_learners) == (1, 1)


# 5 as_of boundary ------------------------------------------------------------------------------------


def test_a_violation_at_exactly_as_of_counts_and_one_second_later_does_not(env):
    ids = _population(env)
    record_violation(env.nstore, ids["a"][0], AS_OF.isoformat())
    record_violation(env.nstore, ids["a"][1], (AS_OF + timedelta(seconds=1)).isoformat())
    arm = _run(env).arms["treatment"]
    assert (arm.breach_learner_days, arm.breach_learners) == (1, 1)


# 6 to_dict -------------------------------------------------------------------------------------------


def _walk(value, path="$"):
    """Yields (path, node) for every node; asserts dict keys are sorted strings."""
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


def _rich_world(env):
    ids = _population(env)
    record_attempt(env.application, ids["a"][0], "att-1", passed=True)
    record_attempt(env.application, ids["b"][0], "att-2", passed=False)
    record_violation(env.nstore, ids["b"][1], D1.isoformat())
    awards = [xp_award(ids["a"][0], D1, 100, None), xp_award(ids["b"][2], D1, 30, 1)]
    return ids, awards


def test_to_dict_is_plain_json_with_sorted_keys_at_every_level(env):
    _, awards = _rich_world(env)
    data = _run(env, awards, as_of=AS_OF_30).to_dict()

    text = json.dumps(data, sort_keys=True)  # serialises without a default= hook
    assert json.loads(text) == data
    assert json.dumps(data) == text  # already in sorted order
    allowed = (dict, list, str, int, float, bool, type(None))
    for path, node in _walk(data):
        assert type(node) in allowed, f"{path}: {type(node).__name__}"  # no datetime, dataclass, tuple, set
    assert data["as_of"] == AS_OF_30.isoformat()
    assert data["arms"]["treatment"]["mastery"]["assigned"] == 3


def test_two_runs_give_identical_json_text(env):
    _, awards = _rich_world(env)
    first = json.dumps(_run(env, awards, as_of=AS_OF_30).to_dict(), sort_keys=True)
    second = json.dumps(_run(env, awards, as_of=AS_OF_30).to_dict(), sort_keys=True)
    assert first == second


# 7 awards ---------------------------------------------------------------------------------------------


def test_awards_from_abuse_service_returns_the_completion_facts(env):
    learner_id = make_learner(env.application, "variant_a")
    learner = env.application.repository.get(UUID(learner_id))
    learner.complete_lesson("c-1", "lesson-1", occurred_at=D1, timezone="UTC")
    learner.complete_lesson("c-2", "lesson-2", occurred_at=D1 + timedelta(hours=1), timezone="UTC")
    env.application.save(learner)
    signals = AbuseSignalStore(":memory:")
    try:
        service = AbuseDetectionService(env.application, env.evidence, signals)
        awards = awards_from_abuse_service(service)
        assert isinstance(awards, list)
        assert awards == list(service.completion_facts()[0])
        assert len(awards) == 2
        assert {a.learner_id for a in awards} == {learner_id}
        for award in awards:
            assert isinstance(award.xp_amount, int) and award.occurred_at.tzinfo is not None
            assert hasattr(award, "repeat_index")
    finally:
        signals.close()


def test_awards_after_as_of_are_dropped_from_the_gaming_report(env):
    ids = _population(env)
    as_of = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)  # Wednesday, ISO week 2026-W42
    awards = [
        xp_award(ids["a"][0], datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc), 100, None),
        # same ISO week, but after as_of: all repeat XP, would breach if it were counted
        xp_award(ids["a"][1], datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc), 500, 1),
    ]
    result = _run(env, awards, as_of=as_of)
    week = {r.cohort_id: r for r in result.gaming.weeks["2026-W42"]}
    assert week["variant_a"].total_xp == 100
    assert result.gaming.breached is False
    assert "gaming_share_breach" not in result.invalid_reasons


# 8 list_decisions --------------------------------------------------------------------------------------


def _decision(learner_id: str, decided_at: str, exp_id: str = EXP_ID) -> dict:
    return {
        "exp_id": exp_id, "learner_id": learner_id, "variant": "variant_a", "decided_at": decided_at,
        "outcome": "skipped", "reason": "not_treatment", "proposal_id": None,
        "fact_event_id": None, "fact_status": None,
    }


def test_list_decisions_returns_all_rows_ordered_by_learner_then_decided_at():
    store = ExperimentStore(":memory:")
    try:
        for learner, at in (("b", "2026-10-12T10:00:00+00:00"), ("a", "2026-10-12T11:00:00+00:00"),
                            ("a", "2026-10-12T09:00:00+00:00"), ("b", "2026-10-12T08:00:00+00:00")):
            assert store.record_decision(_decision(learner, at))
        assert store.record_decision(_decision("a", "2026-10-12T07:00:00+00:00", exp_id="exp_other"))

        rows = store.list_decisions(EXP_ID)
        assert [(r["learner_id"], r["decided_at"]) for r in rows] == [
            ("a", "2026-10-12T09:00:00+00:00"), ("a", "2026-10-12T11:00:00+00:00"),
            ("b", "2026-10-12T08:00:00+00:00"), ("b", "2026-10-12T10:00:00+00:00"),
        ]
        one = store.list_decisions(EXP_ID, learner_id="a")
        assert [r["decided_at"] for r in one] == ["2026-10-12T09:00:00+00:00", "2026-10-12T11:00:00+00:00"]
        assert store.list_decisions(EXP_ID, learner_id="nobody") == []
        assert [r["exp_id"] for r in store.list_decisions("exp_other")] == ["exp_other"]
        assert set(rows[0]) == {
            "exp_id", "learner_id", "variant", "decided_at", "outcome", "reason",
            "proposal_id", "fact_event_id", "fact_status",
        }
    finally:
        store.close()


# 9 verify_engine_reproduction --------------------------------------------------------------------------


def _small_log(db: str) -> None:
    application = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
    )
    store, nstore = ExperimentStore(db), NotificationStore(db)
    try:
        for variant in ("variant_a", "variant_b"):
            learner_id = make_learner(application, variant)
            store.record_eligibility(eligibility_row(learner_id, variant))
    finally:
        nstore.close()
        store.close()
        application.close()


def test_verify_engine_reproduction_rejects_a_naive_as_of(tmp_path):
    db = str(tmp_path / "engine.db")
    _small_log(db)
    before = (tmp_path / "engine.db").read_bytes()
    with pytest.raises(ValueError):
        verify_engine_reproduction(
            db, exp_id=EXP_ID, assessment_id=ASSESSMENT_ID, as_of=datetime(2026, 11, 1, 12, 0)
        )
    assert (tmp_path / "engine.db").read_bytes() == before  # a failed call changed nothing


def test_verify_engine_reproduction_rejects_a_non_sqlite_file_and_leaves_it_untouched(tmp_path):
    path = tmp_path / "not-a-database.db"
    path.write_bytes(b"this is definitely not a sqlite database\n" * 40)
    before = path.read_bytes()
    with pytest.raises(ValueError) as excinfo:
        verify_engine_reproduction(
            str(path), exp_id=EXP_ID, assessment_id=ASSESSMENT_ID, as_of=AS_OF_30
        )
    assert str(path) in str(excinfo.value)
    assert "sqlite" in str(excinfo.value).lower()
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["not-a-database.db"]  # no journal or sidecar


# 10 mixed assessment versions --------------------------------------------------------------------------


def _mixed_world(env):
    ids = _population(env)
    record_attempt(env.application, ids["a"][0], "att-v1", passed=True, version=1)
    record_attempt(env.application, ids["a"][1], "att-v2", passed=True, version=2)
    return ids


def test_attempts_spanning_two_versions_make_the_run_invalid(env):
    _mixed_world(env)
    result = _run(env, as_of=AS_OF_30)
    assert result.valid is False
    assert "mixed_assessment_versions" in result.invalid_reasons
    assert result.arms["treatment"].mastery.assigned == 3  # still fully reported


def test_a_single_version_is_not_a_mix(env):
    ids = _population(env)
    record_attempt(env.application, ids["a"][0], "att-1", version=2)
    record_attempt(env.application, ids["b"][0], "att-2", version=2)
    result = _run(env, as_of=AS_OF_30)
    assert "mixed_assessment_versions" not in result.invalid_reasons
    assert result.valid is True


def test_other_assessments_versions_do_not_count_towards_a_mix(env):
    ids = _population(env)
    record_attempt(env.application, ids["a"][0], "att-1", version=1)
    record_attempt(env.application, ids["a"][1], "att-other", assessment_id="asmt-2", version=7)
    assert "mixed_assessment_versions" not in _run(env, as_of=AS_OF_30).invalid_reasons


def test_assessment_version_ignores_other_versions_and_removes_the_mix_reason(env):
    ids = _mixed_world(env)
    inputs = build_mastery_inputs(env.application, EXP_ID, assessment_id=ASSESSMENT_ID, assessment_version=1)
    assert {r["attempt_id"] for r in inputs.attempts} == {"att-v1"}
    assert {r["attempt_id"] for r in inputs.completions} == {"att-v1"}
    assert all(r["assessment_version"] == 1 for r in inputs.attempts)
    assert inputs.ignored_other_assessment_attempts == 1  # the version-2 attempt

    result = _run(env, as_of=AS_OF_30, assessment_version=1)
    assert "mixed_assessment_versions" not in result.invalid_reasons
    assert result.valid is True
    treatment = result.arms["treatment"].mastery
    assert (treatment.mastered, treatment.missing_outcome) == (1, 2)  # a1's v2 attempt did not count
    assert ids["a"][1]  # (setup sanity)


def test_a_version_with_no_attempts_gives_empty_inputs_and_no_mix(env):
    _mixed_world(env)
    inputs = build_mastery_inputs(env.application, EXP_ID, assessment_id=ASSESSMENT_ID, assessment_version=9)
    assert inputs.attempts == [] and inputs.completions == []
    assert inputs.ignored_other_assessment_attempts == 2
    assert "mixed_assessment_versions" not in _run(env, as_of=AS_OF_30, assessment_version=9).invalid_reasons


# 11 one pass over the stored events --------------------------------------------------------------------


def _count_log_reads(monkeypatch) -> list[int]:
    """Wraps ``read_all_notifications`` in every module that imports it; returns the call counter."""
    calls: list[int] = []
    for module in (runner_module, inputs_module):
        if not hasattr(module, "read_all_notifications"):
            continue  # a module that no longer reads the log itself cannot add a scan
        real = module.read_all_notifications

        def counting(*args, _real=real, **kwargs):
            calls.append(1)
            return _real(*args, **kwargs)

        monkeypatch.setattr(module, "read_all_notifications", counting)
    return calls


def _world_with_exposure(env):
    ids = _population(env)
    record_attempt(env.application, ids["a"][0], "att-1", passed=True)
    record_attempt(env.application, ids["b"][0], "att-2", passed=False)
    learner = env.application.repository.get(UUID(ids["a"][0]))
    assert learner.record_experiment_exposure(
        EXP_ID, "streak_risk_reminder", view_id="view-1", exposed_at=D1
    )
    env.application.save(learner)
    return ids


def test_run_experiment_reads_the_stored_events_exactly_once(env, monkeypatch):
    _world_with_exposure(env)
    expected = json.dumps(_run(env, as_of=AS_OF_30).to_dict(), sort_keys=True)  # unpatched baseline

    calls = _count_log_reads(monkeypatch)
    result = _run(env, as_of=AS_OF_30)

    assert len(calls) == 1, f"run_experiment scanned the log {len(calls)} times"
    assert json.dumps(result.to_dict(), sort_keys=True) == expected  # same numbers as before
    assert result.arms["treatment"].exposure.exposed == 1


def test_the_standalone_helpers_still_scan_on_their_own(env, monkeypatch):
    ids = _world_with_exposure(env)
    eligible = {i for key in ids for i in ids[key]}
    calls = _count_log_reads(monkeypatch)

    read_assignments(env.application, EXP_ID)
    assert len(calls) == 1
    build_mastery_inputs(env.application, EXP_ID, assessment_id=ASSESSMENT_ID)
    assert len(calls) == 2
    exposure_by_arm(env.application, EXP_ID, eligible_ids=eligible, as_of=AS_OF_30)
    assert len(calls) == 3
