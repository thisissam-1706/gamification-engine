"""M7 step 15: the whole experiment engine reproduces bit for bit from its persisted log.

Written before the implementation. Assumed new function in
``src/scale_and_recovery/experiment_repro.py`` (next to, and leaving untouched,
``verify_experiment_reproduction``):

* ``verify_engine_reproduction(db_path, *, exp_id, assessment_id, as_of)`` ->
  ``EngineReproductionResult(bit_identical, checksum_first, checksum_second, run_first, run_second,
  population_size, total_attempts)``. For each of two passes it opens FRESH ``LearnerApplication``,
  ``ExperimentStore`` and ``NotificationStore`` objects on the ONE sqlite file ``db_path`` (events,
  experiment tables and notification tables share it), calls ``run_experiment`` and closes
  everything. ``run_first`` / ``run_second`` are the ``ExperimentRunResult`` objects; each checksum is
  the sha256 hex digest of ``json.dumps(run.to_dict(), sort_keys=True)`` (UTF-8);
  ``bit_identical = checksum_first == checksum_second``; ``population_size`` = learners assigned for
  ``exp_id`` in the log; ``total_attempts`` = CheckpointAttempted events for ``assessment_id`` of
  those learners. ``as_of`` is an aware datetime. The awards passed to ``run_experiment`` come from
  an ``AbuseDetectionService`` built on the same database (with an ``EvidenceStore`` on the same file)
  for both passes, so the gaming guardrail is exercised; the gaming test at the bottom of this file
  relies on that.
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

from src.experiments.service import assign_learner_to_experiment
from src.experiments.store import ExperimentStore
from src.notifications.store import NotificationStore
from src.scale_and_recovery.experiment_repro import verify_engine_reproduction
from src.state_machine.learner import Learner, LearnerApplication
from tests.experiments.support import ASSIGNED_AT, EXP_ID, uuid_for_variant

ASSESSMENT = "asmt-1"
AS_OF = ASSIGNED_AT + timedelta(days=30)
T1 = ASSIGNED_AT + timedelta(days=1)  # 2026-10-02T12:00Z


def _application(db: str) -> LearnerApplication:
    return LearnerApplication(env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db})


def _attempt(application, learner_id: str, attempt_id: str, passed: bool) -> None:
    learner = application.repository.get(UUID(learner_id))
    assert learner.record_checkpoint_attempt(
        attempt_id, ASSESSMENT, 1, passed, timezone="Asia/Kolkata", occurred_at=T1
    )
    application.save(learner)


def _eligibility_row(learner_id: str, variant: str) -> dict:
    return {
        "exp_id": EXP_ID,
        "learner_id": learner_id,
        "variant": variant,
        "assigned_at": ASSIGNED_AT.isoformat(),
        "eligible": 1,
        "reason": None,
        "opted_out_push": 0,
        "opted_out_streak_risk": 0,
        "timezone": "Asia/Kolkata",
        "tenure_days": 40,
    }


def _build_log(db: str) -> dict[str, list[str]]:
    """4 treatment + 4 control learners, all eligible; 6 attempts; 4 cap-violation rows.

    treatment: t0 pass, t1 fail, t2 none, t3 pass; control: c0 pass, c1 pass, c2 fail, c3 none.
    """
    application = _application(db)
    store = ExperimentStore(db)
    nstore = NotificationStore(db)
    ids: dict[str, list[str]] = {"a": [], "b": []}
    try:
        for key, variant in (("a", "variant_a"), ("b", "variant_b")):
            for nth in range(4):
                learner = Learner._create(Learner.Created, id=uuid_for_variant(variant, EXP_ID, nth=nth))
                application.save(learner)
                learner_id = str(learner.id)
                assert assign_learner_to_experiment(application, learner_id, EXP_ID, ASSIGNED_AT)
                store.record_eligibility(_eligibility_row(learner_id, variant))
                ids[key].append(learner_id)
        for learner_id, passed, label in (
            (ids["a"][0], True, "t0"),
            (ids["a"][1], False, "t1"),
            (ids["a"][3], True, "t3"),
            (ids["b"][0], True, "c0"),
            (ids["b"][1], True, "c1"),
            (ids["b"][2], False, "c2"),
        ):
            _attempt(application, learner_id, f"att-{label}", passed)
        violations = (
            (ids["a"][1], "2026-10-02T01:00:00+00:00"),
            (ids["a"][1], "2026-10-02T09:00:00+00:00"),  # same learner, same UTC day
            (ids["b"][0], "2026-10-02T10:00:00+00:00"),
            (ids["b"][2], "2026-10-03T10:00:00+00:00"),
        )
        for n, (learner_id, occurred_at) in enumerate(violations):
            assert nstore.record_guardrail_violation(
                f"viol-{n}", EXP_ID, None, learner_id, f"prop-{n}", "daily_cap_exceeded", occurred_at
            )
    finally:
        nstore.close()
        store.close()
        application.close()
    return ids


def _verify(db: str):
    return verify_engine_reproduction(db, exp_id=EXP_ID, assessment_id=ASSESSMENT, as_of=AS_OF)


def _checksum(run) -> str:
    return hashlib.sha256(json.dumps(run.to_dict(), sort_keys=True).encode("utf-8")).hexdigest()


# 1 ---------------------------------------------------------------------------------------------


def test_two_fresh_passes_over_the_persisted_log_are_bit_identical(tmp_path):
    db = str(tmp_path / "engine.db")
    _build_log(db)

    result = _verify(db)

    assert result.bit_identical is True
    assert result.checksum_first == result.checksum_second
    assert len(result.checksum_first) == 64 and int(result.checksum_first, 16) >= 0
    assert result.run_first.to_dict() == result.run_second.to_dict()
    assert result.checksum_first == _checksum(result.run_first)
    assert result.checksum_second == _checksum(result.run_second)


def test_repeating_the_verification_gives_the_same_checksum(tmp_path):
    db = str(tmp_path / "engine.db")
    _build_log(db)
    assert _verify(db).checksum_first == _verify(db).checksum_first


# 2 ---------------------------------------------------------------------------------------------


def test_result_counts_equal_the_hand_computed_expectation(tmp_path):
    db = str(tmp_path / "engine.db")
    _build_log(db)

    result = _verify(db)

    assert result.population_size == 8
    assert result.total_attempts == 6
    for run in (result.run_first, result.run_second):
        treatment, control = run.arms["treatment"], run.arms["control"]
        assert (treatment.eligible, control.eligible) == (4, 4)
        t, c = treatment.mastery, control.mastery
        assert (t.assigned, t.mastered, t.failed, t.missing_outcome) == (4, 2, 1, 1)
        assert (c.assigned, c.mastered, c.failed, c.missing_outcome) == (4, 2, 1, 1)
        assert t.rate == 0.5 and c.rate == 0.5  # mastered / mature learners (4 each)
        # Cap violations: treatment t1 twice on one UTC day; control c0 and c2 on two days.
        assert (treatment.breach_learner_days, treatment.breach_learners) == (1, 1)
        assert (control.breach_learner_days, control.breach_learners) == (2, 2)
        assert "cap_breach_treatment" in run.invalid_reasons


# 3 ---------------------------------------------------------------------------------------------


def test_an_extra_checkpoint_attempt_changes_the_checksum(tmp_path):
    db = str(tmp_path / "engine.db")
    ids = _build_log(db)
    first = _verify(db)

    application = _application(db)
    try:
        _attempt(application, ids["a"][2], "att-extra", True)  # t2 had no attempt before
    finally:
        application.close()

    second = _verify(db)
    assert second.bit_identical is True  # still self-consistent
    assert second.checksum_first != first.checksum_first
    assert second.total_attempts == first.total_attempts + 1 == 7
    assert second.run_first.arms["treatment"].mastery.mastered == 3
    assert first.run_first.arms["treatment"].mastery.mastered == 2


# Edge cases (M7 step 17) ----------------------------------------------------------------------


def test_missing_db_path_raises_a_clear_error_and_creates_no_file(tmp_path):
    import pytest

    db = tmp_path / "does-not-exist.db"
    with pytest.raises(FileNotFoundError) as excinfo:
        _verify(str(db))
    assert str(db) in str(excinfo.value)
    assert not db.exists()
    assert list(tmp_path.iterdir()) == []  # no database, journal or sidecar file appeared


def test_empty_log_gives_a_stable_checksum_and_an_invalid_verdict(tmp_path):
    db = str(tmp_path / "empty.db")
    application = _application(db)  # creates the event tables, stores nothing
    store, nstore = ExperimentStore(db), NotificationStore(db)
    for opened in (nstore, store, application):
        opened.close()

    first = _verify(db)
    second = _verify(db)

    assert first.bit_identical is True
    assert first.checksum_first == first.checksum_second == second.checksum_first
    assert (first.population_size, first.total_attempts) == (0, 0)
    assert first.run_first.valid is False
    assert "no_eligible_learners" in first.run_first.invalid_reasons


def test_a_run_with_an_invalid_verdict_still_reproduces_bit_identically(tmp_path):
    db = str(tmp_path / "engine.db")
    _build_log(db)  # its cap violations make the run invalid

    result = _verify(db)

    for run in (result.run_first, result.run_second):
        assert run.valid is False
        assert run.invalid_reasons  # non-empty
    assert result.run_first.invalid_reasons == result.run_second.invalid_reasons
    assert result.bit_identical is True
    assert result.checksum_first == result.checksum_second
    # The invalid run still carries its numbers.
    assert result.run_first.arms["treatment"].mastery.assigned == 4


# Gaming guardrail through the abuse service (M7 repro2) ---------------------------------------------------


def _build_gaming_log(db: str) -> dict[str, list[str]]:
    """4 + 4 eligible learners, no attempts, no cap violations; repeat XP only in the treatment arm.

    Week 2026-W40 (2026-10-02): treatment t0 completes lesson-1 for 100 XP, then again for 60 XP (repeat_index 1,
    so 60 is repeat XP), t1 completes lesson-2 for 100 XP: treatment total 260, repeat 60, share 0.23 > 0.15.
    Control c0 and c1 complete one lesson each for 100 XP: share 0.
    XP amounts are explicit so the shares do not depend on the decay ruleset.
    """
    application = _application(db)
    store = ExperimentStore(db)
    nstore = NotificationStore(db)
    ids: dict[str, list[str]] = {"a": [], "b": []}
    try:
        for key, variant in (("a", "variant_a"), ("b", "variant_b")):
            for nth in range(4):
                learner = Learner._create(Learner.Created, id=uuid_for_variant(variant, EXP_ID, nth=nth))
                application.save(learner)
                learner_id = str(learner.id)
                assert assign_learner_to_experiment(application, learner_id, EXP_ID, ASSIGNED_AT)
                store.record_eligibility(_eligibility_row(learner_id, variant))
                ids[key].append(learner_id)
        completions = (
            (ids["a"][0], "c-a0-first", "lesson-1", 100, T1),
            (ids["a"][0], "c-a0-repeat", "lesson-1", 60, T1 + timedelta(hours=1)),
            (ids["a"][1], "c-a1", "lesson-2", 100, T1),
            (ids["b"][0], "c-b0", "lesson-1", 100, T1),
            (ids["b"][1], "c-b1", "lesson-2", 100, T1),
        )
        for learner_id, completion_id, lesson_id, xp, at in completions:
            learner = application.repository.get(UUID(learner_id))
            assert learner.complete_lesson(
                completion_id, lesson_id, xp_amount=xp, occurred_at=at, timezone="UTC"
            )
            application.save(learner)
    finally:
        nstore.close()
        store.close()
        application.close()
    return ids


def test_repeat_xp_over_the_weekly_share_invalidates_the_run_and_still_reproduces(tmp_path):
    db = str(tmp_path / "gaming.db")
    _build_gaming_log(db)

    result = _verify(db)

    for run in (result.run_first, result.run_second):
        assert run.valid is False
        assert "gaming_share_breach" in run.invalid_reasons
        assert list(run.invalid_reasons) == ["gaming_share_breach"]  # nothing else is wrong with this log
        assert run.gaming.breached is True
        week = {r.cohort_id: r for r in run.gaming.weeks["2026-W40"]}
        assert (week["variant_a"].total_xp, week["variant_a"].repeat_xp) == (260, 60)
        assert week["variant_a"].breached is True
        assert (week["variant_b"].total_xp, week["variant_b"].repeat_xp) == (200, 0)
        assert week["variant_b"].breached is False
    assert result.bit_identical is True
    assert result.checksum_first == result.checksum_second
    assert result.run_first.to_dict() == result.run_second.to_dict()
