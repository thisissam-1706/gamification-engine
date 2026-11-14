"""M7 step 7: mastery inputs built from the event store (exp_007). Written before the implementation.

Assumed new module ``src.experiments.inputs``:

* ``build_mastery_inputs(application, exp_id, *, assessment_id, eligible_ids=None)`` ->
  ``MasteryInputs(assignments, attempts, completions, variants, ignored_other_assessment_attempts)``
  built by reading every stored domain event (as ``AbuseService._domain_events`` does), with no new
  event type and no clock reads:
  - ``assignments``: ``{str(learner_id): assigned_at ISO string}`` from ExperimentAssigned for exp_id;
  - ``variants``: ``{str(learner_id): variant}`` from the same events;
  - ``attempts``: one dict per CheckpointAttempted for ``assessment_id`` with learner_id, attempt_id,
    assessment_id, assessment_version, occurred_at (ISO STRING, even if stored as a datetime);
  - ``completions``: one dict per such attempt: attempt_id, learner_id, assessment_id,
    assessment_version, passed (unchanged bool), occurred_at (the attempt's occurred_at);
  - ``ignored_other_assessment_attempts``: count of CheckpointAttempted events for other assessments;
  - ``eligible_ids`` (None = everyone) filters learners out of assignments, variants, attempts and
    completions.

Assumptions: learners without an ExperimentAssigned event for exp_id are not in the outputs (untested);
list order is not specified, so tests compare by attempt_id.
"""

from datetime import datetime, timedelta, timezone

from src.experiments.inputs import build_mastery_inputs
from src.experiments.metrics import mastery_report
from src.experiments.service import assign_learner_to_experiment
from src.state_machine.learner import Learner
from tests.experiments.support import ASSIGNED_AT, EXP_ID, open_persisted, uuid_for_variant

ASSESSMENT = "asmt-1"
AS_OF = ASSIGNED_AT + timedelta(days=30)
T1 = ASSIGNED_AT + timedelta(days=1)


def _learner(application, variant: str, nth: int = 0, assigned_at=ASSIGNED_AT) -> str:
    learner = Learner._create(Learner.Created, id=uuid_for_variant(variant, EXP_ID, nth=nth))
    application.save(learner)
    assert assign_learner_to_experiment(application, str(learner.id), EXP_ID, assigned_at)
    return str(learner.id)


def _attempt(
    application,
    learner_id: str,
    attempt_id: str,
    *,
    passed: bool = True,
    occurred_at=T1,
    assessment_id: str = ASSESSMENT,
    version: int = 1,
) -> None:
    from uuid import UUID

    learner = application.repository.get(UUID(learner_id))
    assert learner.record_checkpoint_attempt(
        attempt_id,
        assessment_id,
        version,
        passed,
        timezone="Asia/Kolkata",
        occurred_at=occurred_at,
    )
    application.save(learner)


def _build(application, **kwargs):
    return build_mastery_inputs(application, EXP_ID, assessment_id=ASSESSMENT, **kwargs)


def _by_attempt(rows) -> dict[str, dict]:
    return {row["attempt_id"]: row for row in rows}


# 1 ---------------------------------------------------------------------------------------------


def test_assignments_and_variants_come_from_assigned_events(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        a = _learner(application, "variant_a")
        b = _learner(application, "variant_b", assigned_at=ASSIGNED_AT + timedelta(hours=5))
        inputs = _build(application)
        assert set(inputs.assignments) == {a, b}
        assert all(isinstance(key, str) for key in inputs.assignments)
        assert inputs.assignments[a] == ASSIGNED_AT.isoformat()
        assert inputs.assignments[b] == (ASSIGNED_AT + timedelta(hours=5)).isoformat()
        assert inputs.variants == {a: "variant_a", b: "variant_b"}


def test_other_experiments_assignments_are_not_included(tmp_path):
    from uuid import UUID

    with open_persisted(tmp_path) as (application, _):
        a = _learner(application, "variant_a")
        other = Learner._create(Learner.Created, id=uuid_for_variant("variant_a", EXP_ID, nth=1))
        assert other.assign_experiment("exp_other", assigned_at=ASSIGNED_AT)
        application.save(other)
        inputs = _build(application)
        assert set(inputs.assignments) == {a}
        assert str(other.id) not in inputs.variants


# 2 ---------------------------------------------------------------------------------------------


def test_attempts_carry_required_keys_with_iso_string_times(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        a = _learner(application, "variant_a")
        as_datetime = T1
        as_string = (T1 + timedelta(hours=1)).isoformat()
        _attempt(application, a, "att-dt", occurred_at=as_datetime, version=2)
        _attempt(application, a, "att-str", occurred_at=as_string, version=3)

        rows = _by_attempt(_build(application).attempts)
        assert set(rows) == {"att-dt", "att-str"}
        for attempt_id, version in (("att-dt", 2), ("att-str", 3)):
            row = rows[attempt_id]
            assert row["learner_id"] == a
            assert row["assessment_id"] == ASSESSMENT
            assert row["assessment_version"] == version
            assert isinstance(row["occurred_at"], str)
        assert datetime.fromisoformat(rows["att-dt"]["occurred_at"]) == as_datetime
        assert datetime.fromisoformat(rows["att-str"]["occurred_at"]) == datetime.fromisoformat(
            as_string
        )


# 3 ---------------------------------------------------------------------------------------------


def test_one_completion_per_attempt_copies_fields_unchanged(tmp_path):
    with open_persisted(tmp_path) as (application, events):
        a = _learner(application, "variant_a")
        b = _learner(application, "variant_b")
        _attempt(application, a, "att-pass", passed=True, version=4)
        _attempt(application, b, "att-fail", passed=False, version=5, occurred_at=T1.isoformat())

        inputs = _build(application)
        attempts = _by_attempt(inputs.attempts)
        completions = _by_attempt(inputs.completions)
        assert len(inputs.completions) == len(inputs.attempts) == 2
        assert set(completions) == set(attempts) == {"att-pass", "att-fail"}
        for attempt_id, learner_id, passed, version in (
            ("att-pass", a, True, 4),
            ("att-fail", b, False, 5),
        ):
            completion = completions[attempt_id]
            assert completion["learner_id"] == learner_id
            assert completion["assessment_id"] == ASSESSMENT
            assert completion["assessment_version"] == version
            assert completion["passed"] is passed
            assert type(completion["passed"]) is bool
            assert completion["occurred_at"] == attempts[attempt_id]["occurred_at"]


def test_building_inputs_writes_no_new_events(tmp_path):
    from uuid import UUID

    with open_persisted(tmp_path) as (application, _):
        a = _learner(application, "variant_a")
        _attempt(application, a, "att-1")
        before = [type(e).__name__ for e in application.events.get(UUID(a))]
        _build(application)
        _build(application)
        assert [type(e).__name__ for e in application.events.get(UUID(a))] == before


# 4 ---------------------------------------------------------------------------------------------


def test_output_feeds_mastery_report_mastered_and_failed(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        passing = _learner(application, "variant_a")
        failing = _learner(application, "variant_b")
        _attempt(application, passing, "att-pass", passed=True)
        _attempt(application, failing, "att-fail", passed=False, occurred_at=T1.isoformat())

        inputs = _build(application)
        report = mastery_report(
            inputs.assignments, inputs.attempts, inputs.completions, as_of=AS_OF
        )
        assert report.assigned == 2
        assert report.mastered == 1
        assert report.failed == 1
        assert report.missing_outcome == 0
        assert report.conflicting == 0
        assert report.invalid_ordering == 0
        assert report.rate == 0.5


# 5 ---------------------------------------------------------------------------------------------


def test_other_assessment_attempts_are_excluded_and_counted(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        a = _learner(application, "variant_a")
        b = _learner(application, "variant_b")
        _attempt(application, a, "att-mine")
        _attempt(application, a, "att-other-1", assessment_id="asmt-2")
        _attempt(application, b, "att-other-2", assessment_id="asmt-3")

        inputs = _build(application)
        assert set(_by_attempt(inputs.attempts)) == {"att-mine"}
        assert set(_by_attempt(inputs.completions)) == {"att-mine"}
        assert inputs.ignored_other_assessment_attempts == 2
        # A learner whose only attempt was for another assessment stays assigned.
        assert set(inputs.assignments) == {a, b}


def test_nothing_ignored_when_every_attempt_is_for_the_assessment(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        a = _learner(application, "variant_a")
        _attempt(application, a, "att-1")
        assert _build(application).ignored_other_assessment_attempts == 0


# 6 ---------------------------------------------------------------------------------------------


def test_eligible_ids_filters_all_four_outputs(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        keep_a = _learner(application, "variant_a")
        keep_b = _learner(application, "variant_b")
        drop = _learner(application, "variant_a", nth=1)
        for learner_id, attempt_id in ((keep_a, "att-a"), (keep_b, "att-b"), (drop, "att-drop")):
            _attempt(application, learner_id, attempt_id)

        inputs = _build(application, eligible_ids={keep_a, keep_b})
        assert set(inputs.assignments) == {keep_a, keep_b}
        assert set(inputs.variants) == {keep_a, keep_b}
        assert set(_by_attempt(inputs.attempts)) == {"att-a", "att-b"}
        assert set(_by_attempt(inputs.completions)) == {"att-a", "att-b"}


def test_empty_eligible_ids_gives_empty_outputs(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        a = _learner(application, "variant_a")
        _attempt(application, a, "att-1")
        inputs = _build(application, eligible_ids=set())
        assert inputs.assignments == {} and inputs.variants == {}
        assert list(inputs.attempts) == [] and list(inputs.completions) == []


# Edge cases -----------------------------------------------------------------------------------


def test_duplicate_delivery_of_one_attempt_id_is_one_attempt_and_one_completion(tmp_path):
    from uuid import UUID

    with open_persisted(tmp_path) as (application, _):
        a = _learner(application, "variant_a")
        _attempt(application, a, "att-1")
        learner = application.repository.get(UUID(a))
        # The aggregate is idempotent on attempt_id: a repeat, even with other content, is refused.
        assert (
            learner.record_checkpoint_attempt(
                "att-1", ASSESSMENT, 9, False, timezone="UTC", occurred_at=T1 + timedelta(hours=2)
            )
            is False
        )
        application.save(learner)
        names = [type(e).__name__ for e in application.events.get(UUID(a))]
        assert names.count("CheckpointAttempted") == 1

        inputs = _build(application)
        assert [r["attempt_id"] for r in inputs.attempts] == ["att-1"]
        assert [r["attempt_id"] for r in inputs.completions] == ["att-1"]
        assert inputs.attempts[0]["assessment_version"] == 1
        assert inputs.completions[0]["passed"] is True


def test_learner_without_assignment_event_is_absent(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        assigned = _learner(application, "variant_a")
        unassigned = Learner._create(
            Learner.Created, id=uuid_for_variant("variant_b", EXP_ID, nth=1)
        )
        application.save(unassigned)
        _attempt(application, assigned, "att-assigned")
        _attempt(application, str(unassigned.id), "att-unassigned")
        # Assigned only to another experiment: still absent for exp_007.
        elsewhere = Learner._create(
            Learner.Created, id=uuid_for_variant("variant_a", EXP_ID, nth=2)
        )
        elsewhere.assign_experiment("exp_other", assigned_at=ASSIGNED_AT)
        application.save(elsewhere)
        _attempt(application, str(elsewhere.id), "att-elsewhere")

        inputs = _build(application)
        assert set(inputs.assignments) == {assigned}
        assert set(inputs.variants) == {assigned}
        assert set(_by_attempt(inputs.attempts)) == {"att-assigned"}
        assert set(_by_attempt(inputs.completions)) == {"att-assigned"}


def test_attempt_id_reused_across_learners_reaches_mastery_report_as_conflicting(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        a = _learner(application, "variant_a")
        b = _learner(application, "variant_b")
        _attempt(application, a, "shared", passed=True)
        _attempt(application, b, "shared", passed=False, occurred_at=T1 + timedelta(hours=1))

        inputs = _build(application)
        assert len(inputs.attempts) == len(inputs.completions) == 2  # not deduped across learners
        report = mastery_report(
            inputs.assignments, inputs.attempts, inputs.completions, as_of=AS_OF
        )
        assert report.conflicting == 2
        assert report.mastered == report.failed == report.missing_outcome == 0


def test_output_order_does_not_change_the_mastery_report(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        ids = [_learner(application, v, nth=i) for i, v in enumerate(["variant_a", "variant_b"] * 2)]
        outcomes = [True, False, True, False]
        for i, (learner_id, passed) in enumerate(zip(ids, outcomes)):
            _attempt(application, learner_id, f"att-{i}", passed=passed)
            _attempt(
                application, learner_id, f"att-late-{i}", passed=not passed,
                occurred_at=T1 + timedelta(days=2),
            )

        inputs = _build(application)
        forward = mastery_report(
            inputs.assignments, inputs.attempts, inputs.completions, as_of=AS_OF
        )
        backward = mastery_report(
            dict(reversed(list(inputs.assignments.items()))),
            list(reversed(inputs.attempts)),
            list(reversed(inputs.completions)),
            as_of=AS_OF,
        )
        assert forward == backward
        assert forward.mastered == 2 and forward.failed == 2


def test_results_identical_after_closing_and_reopening_the_application(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        a = _learner(application, "variant_a")
        b = _learner(application, "variant_b")
        _attempt(application, a, "att-a", passed=True)
        _attempt(application, b, "att-b", passed=False, occurred_at=T1.isoformat())
        _attempt(application, b, "att-other", assessment_id="asmt-2")
        first = _build(application)
    with open_persisted(tmp_path) as (application, _):
        second = _build(application)
    assert first == second
    assert len(second.attempts) == 2 and second.ignored_other_assessment_attempts == 1
