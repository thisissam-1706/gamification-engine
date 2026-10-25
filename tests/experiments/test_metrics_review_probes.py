"""Regression tests mirroring the Week 3 review's probes of the exp_007 metric."""

from src.experiments.metrics import first_eligible_checkpoint_mastery, mastery_report

AS_OF = "2100-01-01T00:00:00Z"
ASSIGNED = {"L1": "2026-01-01T00:00:00Z"}


def _attempt(attempt_id, occurred_at, learner_id="L1"):
    return {
        "learner_id": learner_id, "attempt_id": attempt_id,
        "assessment_id": "checkpoint", "assessment_version": 1,
        "occurred_at": occurred_at,
    }


def _completion(attempt_id, occurred_at, passed=True, **extra):
    return {
        "attempt_id": attempt_id, "assessment_id": "checkpoint",
        "assessment_version": 1, "passed": passed, "occurred_at": occurred_at, **extra,
    }


def _rate(attempts, completions, assignments=ASSIGNED):
    return first_eligible_checkpoint_mastery(assignments, attempts, completions, as_of=AS_OF)


def _report(attempts, completions, assignments=ASSIGNED):
    return mastery_report(assignments, attempts, completions, as_of=AS_OF)


def test_review_probe_completion_before_attempt():
    attempts = [_attempt("A1", "2026-01-02T00:00:00Z")]
    completions = [_completion("A1", "2026-01-01T12:00:00Z")]

    assert _rate(attempts, completions) == 0.0
    assert _report(attempts, completions).invalid_ordering == 1


def test_review_probe_conflicting_duplicate_completions_order_independent():
    attempts = [_attempt("A1", "2026-01-02T00:00:00Z")]
    completions = [
        _completion("A1", "2026-01-02T01:00:00Z", passed=True),
        _completion("A1", "2026-01-02T02:00:00Z", passed=False),
    ]

    assert _rate(attempts, completions) == _rate(attempts, list(reversed(completions))) == 0.0
    assert _report(attempts, completions).conflicting == 1
    assert _report(attempts, list(reversed(completions))).conflicting == 1


def test_review_probe_identical_duplicate_completions_collapse():
    attempts = [_attempt("A1", "2026-01-02T00:00:00Z")]
    completions = [_completion("A1", "2026-01-02T01:00:00Z")] * 2

    assert _report(attempts, completions).mastered == 1
    assert _rate(attempts, completions) == 1.0


def test_review_probe_equal_time_attempts_use_attempt_id():
    a1 = _attempt("a1", "2026-01-02T00:00:00Z")
    a2 = _attempt("a2", "2026-01-02T00:00:00Z")
    completions = [_completion("a1", "2026-01-02T01:00:00Z")]  # only a1 passed

    assert _rate([a2, a1], completions) == 1.0
    assert _rate([a1, a2], completions) == 1.0


def test_duplicate_attempt_ids_with_different_content_are_conflicting():
    early = _attempt("A1", "2026-01-02T00:00:00Z")
    late = _attempt("A1", "2026-01-03T00:00:00Z")
    completions = [_completion("A1", "2026-01-03T01:00:00Z")]

    for attempts in ([early, late], [late, early]):
        report = _report(attempts, completions)
        assert (report.conflicting, report.mastered) == (1, 0)


def test_completion_for_other_learner_is_conflicting():
    attempts = [_attempt("A1", "2026-01-02T00:00:00Z")]
    foreign = [_completion("A1", "2026-01-02T01:00:00Z", learner_id="L2")]
    report = _report(attempts, foreign)
    assert (report.conflicting, report.mastered) == (1, 0)

    # A completion without a learner_id, or with the matching one, still works.
    assert _report(attempts, [_completion("A1", "2026-01-02T01:00:00Z")]).mastered == 1
    matching = [_completion("A1", "2026-01-02T01:00:00Z", learner_id="L1")]
    assert _report(attempts, matching).mastered == 1
