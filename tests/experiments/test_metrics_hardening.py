"""Week 4 fix 4: exp_007 primary-metric hardening (Week 3 review, row 27).

One small test per defect. Fixtures are tiny and hand-computed; the expected
value is in a comment next to each assertion. ``mastery_report`` and the
``as_of`` argument are the planned API, so they are imported/called inside
the tests that need them.
"""

from datetime import datetime, timezone
from itertools import permutations
from pathlib import Path

import pytest

from src.experiments.metrics import first_eligible_checkpoint_mastery

ROOT = Path(__file__).resolve().parents[2]


def _attempt(learner_id, attempt_id, occurred_at, assessment_id="checkpoint", version=1):
    return {
        "learner_id": learner_id,
        "attempt_id": attempt_id,
        "assessment_id": assessment_id,
        "assessment_version": version,
        "occurred_at": occurred_at,
    }


def _completion(attempt_id, occurred_at, passed, assessment_id="checkpoint", version=1):
    return {
        "attempt_id": attempt_id,
        "assessment_id": assessment_id,
        "assessment_version": version,
        "passed": passed,
        "occurred_at": occurred_at,
    }


# (1) A passing completion dated before its attempt is not mastery.
def test_completion_before_its_attempt_is_not_mastery():
    assignments = {"L1": "2026-01-01T00:00:00+00:00"}
    attempts = [_attempt("L1", "A1", "2026-01-05T00:00:00+00:00")]
    completions = [_completion("A1", "2026-01-03T00:00:00+00:00", passed=True)]

    # Completion (Jan 3) precedes its attempt (Jan 5): invalid ordering,
    # so 0 mastered / 1 assigned = 0.0.
    assert first_eligible_checkpoint_mastery(assignments, attempts, completions, as_of="2100-01-01T00:00:00Z") == 0.0


# (2) Conflicting duplicate completions for one attempt_id.
def test_conflicting_duplicate_completions_are_order_independent_and_not_mastery():
    assignments = {"L1": "2026-01-01T00:00:00+00:00"}
    attempts = [_attempt("L1", "A1", "2026-01-02T00:00:00+00:00")]
    passed = _completion("A1", "2026-01-02T01:00:00+00:00", passed=True)
    failed = _completion("A1", "2026-01-02T01:00:00+00:00", passed=False)

    forward = first_eligible_checkpoint_mastery(assignments, attempts, [passed, failed], as_of="2100-01-01T00:00:00Z")
    reverse = first_eligible_checkpoint_mastery(assignments, attempts, [failed, passed], as_of="2100-01-01T00:00:00Z")

    # A1 has both passed=True and passed=False: conflicting, not mastered.
    # 0 / 1 = 0.0 in both input orders.
    assert (forward, reverse) == (0.0, 0.0)


def test_identical_duplicate_completions_collapse_to_one():
    from src.experiments.metrics import mastery_report

    assignments = {"L1": "2026-01-01T00:00:00+00:00"}
    attempts = [_attempt("L1", "A1", "2026-01-02T00:00:00+00:00")]
    completion = _completion("A1", "2026-01-02T01:00:00+00:00", passed=True)
    completions = [completion, dict(completion)]

    # Two identical rows are one completion, not a conflict: 1 / 1 = 1.0.
    assert first_eligible_checkpoint_mastery(assignments, attempts, completions, as_of="2100-01-01T00:00:00Z") == 1.0
    report = mastery_report(assignments, attempts, completions, as_of="2100-01-01T00:00:00Z")
    assert (report.mastered, report.conflicting) == (1, 0)


# (3) Equal-time first attempts need a stable tie key.
def test_tied_first_attempts_use_smallest_attempt_id():
    assignments = {"L1": "2026-01-01T00:00:00+00:00"}
    a2 = _attempt("L1", "A2", "2026-01-02T00:00:00+00:00")
    a1 = _attempt("L1", "A1", "2026-01-02T00:00:00+00:00")
    completions = [
        _completion("A1", "2026-01-02T01:00:00+00:00", passed=True),
        _completion("A2", "2026-01-02T01:00:00+00:00", passed=False),
    ]

    forward = first_eligible_checkpoint_mastery(assignments, [a2, a1], completions, as_of="2100-01-01T00:00:00Z")
    reverse = first_eligible_checkpoint_mastery(assignments, [a1, a2], completions, as_of="2100-01-01T00:00:00Z")

    # Same instant: the tie goes to the smallest attempt_id, "A1", which passed.
    # 1 / 1 = 1.0 in both input orders.
    assert (forward, reverse) == (1.0, 1.0)


# (5) Naive timestamps are UTC; "Z" and "+05:30" are normalized to UTC.
def test_naive_and_offset_timestamps_are_normalized_to_utc():
    assignments = {
        "L1": "2026-01-01T00:00:00",  # naive -> 2026-01-01T00:00Z
        "L2": "2026-01-01T00:00:00Z",
    }
    attempts = [
        # 05:30+05:30 == 00:00Z: exactly at assignment, eligible.
        _attempt("L1", "A1", "2026-01-01T05:30:00+05:30"),
        # 05:00+05:30 == Dec 31 23:30Z: before assignment, not eligible.
        _attempt("L2", "A2", "2026-01-01T05:00:00+05:30"),
    ]
    completions = [
        # Exactly assignment + 14 days, still inside the window.
        _completion("A1", "2026-01-15T00:00:00Z", passed=True),
        _completion("A2", "2026-01-01T06:00:00+05:30", passed=True),
    ]

    # L1 mastered, L2 has no eligible attempt: 1 / 2 = 0.5.
    assert first_eligible_checkpoint_mastery(assignments, attempts, completions, as_of="2100-01-01T00:00:00Z") == 0.5


# (4) Learners whose 14-day window has not closed are not failures.
def _maturity_fixture():
    assignments = {
        "L1": "2025-12-20T00:00:00Z",  # window closed 2026-01-03: mature
        "L2": "2026-01-01T00:00:00Z",  # window closes 2026-01-15: immature on Jan 10
    }
    attempts = [_attempt("L1", "A1", "2025-12-21T00:00:00Z")]
    completions = [_completion("A1", "2025-12-21T01:00:00Z", passed=True)]
    return assignments, attempts, completions


def test_immature_learner_is_not_counted_as_failure():
    assignments, attempts, completions = _maturity_fixture()

    # As of Jan 10, L2 is immature and leaves the denominator: 1 / 1 = 1.0
    # (today L2 counts as a failure: 1 / 2 = 0.5).
    assert first_eligible_checkpoint_mastery(
        assignments, attempts, completions, as_of="2026-01-10T00:00:00Z"
    ) == 1.0


def test_mastery_report_counts_immature_learner_separately():
    from src.experiments.metrics import mastery_report

    assignments, attempts, completions = _maturity_fixture()
    report = mastery_report(
        assignments, attempts, completions, as_of="2026-01-10T00:00:00Z"
    )

    # assigned 2 = L1 mastered + L2 immature; denominator = mature learners = 1.
    assert report.rate == 1.0
    assert (report.assigned, report.denominator, report.immature) == (2, 1, 1)
    assert (report.mastered, report.failed, report.missing_outcome) == (1, 0, 0)
    assert (report.invalid_ordering, report.conflicting) == (0, 0)


# (6) exp_007.yaml must describe the hash assign() actually uses.
def test_exp_007_assignment_function_matches_implementation():
    text = (ROOT / "experiments" / "exp_007.yaml").read_text(encoding="utf-8")
    function_line = next(
        line for line in text.splitlines() if line.strip().startswith("function:")
    )

    # assign(): sha256(json.dumps([subject_id, exp_id], separators=(",", ":")))
    # first 8 bytes, big-endian, mod 100; bucket < 50 -> variant_a.
    for fragment in ("sha256", "json.dumps([subject_id, exp_id]", "[:8]", "big", "mod 100"):
        assert fragment in function_line, (fragment, function_line)
    assert "||" not in function_line
    assert "bucket < 50" in text and "variant_a" in text


# Every assigned learner lands in exactly one bucket.
def test_report_buckets_partition_assigned_learners():
    from src.experiments.metrics import mastery_report

    assignments = {f"L{n}": "2026-01-01T00:00:00Z" for n in range(1, 8)}
    attempts = [
        _attempt("L1", "A1", "2026-01-02T00:00:00Z"),
        _attempt("L2", "A2", "2026-01-02T00:00:00Z"),
        # L3: no attempt.
        _attempt("L4", "A4", "2026-01-02T00:00:00Z"),
        _attempt("L5", "A5", "2026-01-05T00:00:00Z"),
        _attempt("L6", "A6", "2026-01-02T00:00:00Z"),
        _attempt("L7", "A7", "2026-01-02T00:00:00Z"),
    ]
    completions = [
        _completion("A1", "2026-01-02T01:00:00Z", passed=True),   # mastered
        _completion("A2", "2026-01-02T01:00:00Z", passed=False),  # failed
        # A4: no completion -> missing_outcome
        _completion("A5", "2026-01-03T00:00:00Z", passed=True),   # before attempt
        _completion("A6", "2026-01-02T01:00:00Z", passed=True),   # conflicting
        _completion("A6", "2026-01-02T01:00:00Z", passed=False),
        _completion("A7", "2026-01-20T00:00:00Z", passed=True),   # after +14 d
    ]

    report = mastery_report(assignments, attempts, completions, as_of="2100-01-01T00:00:00Z")
    reverse = mastery_report(assignments, attempts, list(reversed(completions)), as_of="2100-01-01T00:00:00Z")

    # mastered L1; failed L2; missing L3, L4, L7; invalid L5; conflicting L6.
    assert report == reverse
    assert (report.mastered, report.failed, report.missing_outcome) == (1, 1, 3)
    assert (report.invalid_ordering, report.conflicting, report.immature) == (1, 1, 0)
    assert (report.assigned, report.denominator) == (7, 7)
    assert report.rate == 1 / 7
    assert (
        report.mastered + report.failed + report.missing_outcome
        + report.invalid_ordering + report.conflicting + report.immature
    ) == report.assigned


def test_true_and_one_are_conflicting_in_either_order():
    from src.experiments.metrics import mastery_report

    assignments = {"L1": "2026-01-01T00:00:00Z"}
    attempts = [_attempt("L1", "A1", "2026-01-02T00:00:00Z")]
    as_bool = _completion("A1", "2026-01-02T01:00:00Z", passed=True)
    as_int = _completion("A1", "2026-01-02T01:00:00Z", passed=1)

    forward = mastery_report(assignments, attempts, [as_bool, as_int], as_of="2100-01-01T00:00:00Z")
    reverse = mastery_report(assignments, attempts, [as_int, as_bool], as_of="2100-01-01T00:00:00Z")

    # True == 1 in Python, but the rows are not identical: conflicting, 0 / 1.
    assert forward == reverse
    assert (forward.conflicting, forward.mastered, forward.rate) == (1, 0, 0.0)


# ---------------------------------------------------------------------------
# Defects 3 and 4: edge cases
# ---------------------------------------------------------------------------
def _passed_on_jan_2(learner_id="L1", attempt_id="A1"):
    """One learner assigned Jan 1, attempt Jan 2, passing completion an hour later."""
    return (
        [_attempt(learner_id, attempt_id, "2026-01-02T00:00:00Z")],
        [_completion(attempt_id, "2026-01-02T01:00:00Z", passed=True)],
    )


def _assert_partition(report):
    assert (
        report.mastered + report.failed + report.missing_outcome
        + report.invalid_ordering + report.conflicting + report.immature
    ) == report.assigned
    assert report.denominator == report.assigned - report.immature


def test_as_of_exactly_at_window_close_is_mature():
    from src.experiments.metrics import mastery_report

    attempts, completions = _passed_on_jan_2()
    report = mastery_report(
        {"L1": "2026-01-01T00:00:00Z"}, attempts, completions,
        as_of=datetime(2026, 1, 15, tzinfo=timezone.utc),
    )

    # Jan 1 + 14 d == Jan 15 == as_of: mature, mastered, 1 / 1 = 1.0.
    assert (report.immature, report.mastered, report.denominator) == (0, 1, 1)
    assert report.rate == 1.0
    _assert_partition(report)


def test_as_of_one_second_before_window_close_is_immature():
    from src.experiments.metrics import mastery_report

    attempts, completions = _passed_on_jan_2()
    report = mastery_report(
        {"L1": "2026-01-01T00:00:00Z"}, attempts, completions,
        as_of="2026-01-14T23:59:59Z",
    )

    # Window closes one second after as_of: immature even though L1 passed.
    assert (report.immature, report.mastered, report.denominator) == (1, 0, 0)
    assert report.rate is None
    _assert_partition(report)


def test_attempt_after_as_of_is_ignored():
    from src.experiments.metrics import mastery_report

    assignments = {"L1": "2025-12-20T00:00:00Z"}  # window closed Jan 3
    late_attempt = _attempt("L1", "A-late", "2026-01-12T00:00:00Z")
    late_completion = _completion("A-late", "2026-01-12T01:00:00Z", passed=True)
    as_of = datetime(2026, 1, 10)  # naive -> 2026-01-10T00:00Z

    with_late = mastery_report(
        assignments, [late_attempt], [late_completion], as_of=as_of
    )
    without = mastery_report(assignments, [], [], as_of=as_of)

    # Jan 12 is after as_of (Jan 10): not observable, so L1 has no attempt.
    # mature, missing_outcome, 0 / 1 = 0.0.
    assert with_late == without
    assert (with_late.missing_outcome, with_late.immature, with_late.rate) == (1, 0, 0.0)



def test_completion_after_as_of_is_ignored():
    from src.experiments.metrics import mastery_report

    assignments = {"L1": "2025-12-20T00:00:00Z"}  # window closed Jan 3
    attempts = [_attempt("L1", "A1", "2025-12-21T00:00:00Z")]
    completions = [
        _completion("A1", "2025-12-21T01:00:00Z", passed=True),
        _completion("A1", "2026-01-12T00:00:00Z", passed=False),  # after as_of
    ]

    # As of Jan 10 only the Dec 21 pass is observable: mastered, 1 / 1 = 1.0.
    # Without a cutoff both rows are visible and disagree: conflicting, 0.0.
    observed = mastery_report(assignments, attempts, completions, as_of="2026-01-10T00:00:00Z")
    everything = mastery_report(assignments, attempts, completions, as_of="2100-01-01T00:00:00Z")
    assert (observed.mastered, observed.conflicting, observed.rate) == (1, 0, 1.0)
    assert (everything.mastered, everything.conflicting, everything.rate) == (0, 1, 0.0)

def test_zero_mature_learners_gives_rate_none():
    from src.experiments.metrics import mastery_report

    assignments = {"L1": "2026-01-01T00:00:00Z", "L2": "2026-01-03T00:00:00Z"}
    attempts, completions = _passed_on_jan_2()
    as_of = "2026-01-10T00:00:00Z"

    report = mastery_report(assignments, attempts, completions, as_of=as_of)

    # Both windows close after Jan 10: immature 2, denominator 0, rate None;
    # the float wrapper reports the ambiguous 0.0.
    assert (report.assigned, report.immature, report.denominator) == (2, 2, 0)
    assert report.rate is None
    assert first_eligible_checkpoint_mastery(
        assignments, attempts, completions, as_of=as_of
    ) == 0.0
    assert mastery_report({}, [], [], as_of="2100-01-01T00:00:00Z").rate is None
    _assert_partition(report)


def _two_arms():
    arm_a = {"L1": "2026-01-01T00:00:00Z", "L2": "2026-01-01T00:00:00Z"}
    arm_b = {"L3": "2026-01-01T00:00:00Z", "L4": "2026-01-05T00:00:00Z"}
    attempts = [
        _attempt("L1", "A1", "2026-01-02T00:00:00Z"),
        _attempt("L2", "A2", "2026-01-02T00:00:00Z"),
        _attempt("L4", "A4", "2026-01-06T00:00:00Z"),
    ]
    completions = [
        _completion("A1", "2026-01-02T01:00:00Z", passed=True),
        _completion("A2", "2026-01-02T01:00:00Z", passed=False),
        _completion("A4", "2026-01-06T01:00:00Z", passed=True),
    ]
    return arm_a, arm_b, attempts, completions


def test_two_arms_evaluated_separately_are_independent():
    from src.experiments.metrics import mastery_report

    arm_a, arm_b, attempts, completions = _two_arms()
    as_of = "2026-01-16T00:00:00Z"

    report_a = mastery_report(arm_a, attempts, completions, as_of=as_of)
    report_b = mastery_report(arm_b, attempts, completions, as_of=as_of)
    both = mastery_report({**arm_a, **arm_b}, attempts, completions, as_of=as_of)

    # Arm A: L1 mastered, L2 failed -> 1 / 2 = 0.5.
    assert (report_a.mastered, report_a.failed, report_a.rate) == (1, 1, 0.5)
    # Arm B: L3 no attempt -> missing; L4 window closes Jan 19 > Jan 16 -> immature.
    # 0 / 1 = 0.0 (L4 already passed but is not counted).
    assert (report_b.missing_outcome, report_b.immature, report_b.rate) == (1, 1, 0.0)
    # The other arm's rows change nothing.
    a_ids = {"A1", "A2"}
    assert report_a == mastery_report(
        arm_a,
        [a for a in attempts if a["attempt_id"] in a_ids],
        [c for c in completions if c["attempt_id"] in a_ids],
        as_of=as_of,
    )
    for field in ("mastered", "failed", "missing_outcome", "invalid_ordering",
                  "conflicting", "immature", "assigned", "denominator"):
        assert getattr(both, field) == getattr(report_a, field) + getattr(report_b, field)
    _assert_partition(report_a)
    _assert_partition(report_b)


def _permutation_fixture():
    assignments = {f"L{n}": "2026-01-01T00:00:00Z" for n in (1, 2, 3)}
    attempts = [
        _attempt("L1", "A2", "2026-01-02T00:00:00Z"),  # tie with A1
        _attempt("L1", "A1", "2026-01-02T00:00:00Z"),
        _attempt("L2", "B1", "2026-01-03T00:00:00Z"),
        _attempt("L3", "C1", "2026-01-04T00:00:00Z"),
    ]
    completions = [
        _completion("A1", "2026-01-02T01:00:00Z", passed=True),
        _completion("A2", "2026-01-02T01:00:00Z", passed=False),
        _completion("B1", "2026-01-03T01:00:00Z", passed=True),
        _completion("C1", "2026-01-04T01:00:00Z", passed=True),  # conflicting
        _completion("C1", "2026-01-04T01:00:00Z", passed=False),
    ]
    return assignments, attempts, completions


def test_as_of_is_required_and_none_is_rejected():
    from src.experiments.metrics import mastery_report

    with pytest.raises(TypeError):
        mastery_report({}, [], [])
    with pytest.raises(ValueError, match="as_of is required"):
        mastery_report({}, [], [], as_of=None)
    with pytest.raises(TypeError):
        first_eligible_checkpoint_mastery({}, [], [])
    with pytest.raises(ValueError, match="as_of is required"):
        first_eligible_checkpoint_mastery({}, [], [], as_of=None)


def test_every_input_permutation_gives_identical_report():
    from src.experiments.metrics import mastery_report

    assignments, attempts, completions = _permutation_fixture()
    reference = mastery_report(assignments, attempts, completions, as_of="2100-01-01T00:00:00Z")

    # L1 -> A1 (smallest id at the tie) passed; L2 passed; L3 conflicting.
    # 2 / 3.
    assert (reference.mastered, reference.conflicting, reference.failed) == (2, 1, 0)
    assert reference.rate == 2 / 3

    runs = 0
    for learner_order in permutations(assignments):
        ordered = {learner: assignments[learner] for learner in learner_order}
        for attempt_order in permutations(attempts):
            for completion_order in permutations(completions):
                assert mastery_report(
                    ordered, list(attempt_order), list(completion_order),
                    as_of="2100-01-01T00:00:00Z",
                ) == reference
                runs += 1
    assert runs == 6 * 24 * 120


# Every fixture in this file, as (assignments, attempts, completions, as_of).
FIXTURES = {
    "before_attempt": lambda: (
        {"L1": "2026-01-01T00:00:00+00:00"},
        [_attempt("L1", "A1", "2026-01-05T00:00:00+00:00")],
        [_completion("A1", "2026-01-03T00:00:00+00:00", passed=True)],
        "2100-01-01T00:00:00Z",
    ),
    "conflicting": lambda: (
        {"L1": "2026-01-01T00:00:00+00:00"},
        [_attempt("L1", "A1", "2026-01-02T00:00:00+00:00")],
        [_completion("A1", "2026-01-02T01:00:00+00:00", passed=True),
         _completion("A1", "2026-01-02T01:00:00+00:00", passed=False)],
        "2100-01-01T00:00:00Z",
    ),
    "identical": lambda: (
        {"L1": "2026-01-01T00:00:00+00:00"},
        [_attempt("L1", "A1", "2026-01-02T00:00:00+00:00")],
        [_completion("A1", "2026-01-02T01:00:00+00:00", passed=True)] * 2,
        "2100-01-01T00:00:00Z",
    ),
    "timestamps": lambda: (
        {"L1": "2026-01-01T00:00:00", "L2": "2026-01-01T00:00:00Z"},
        [_attempt("L1", "A1", "2026-01-01T05:30:00+05:30"),
         _attempt("L2", "A2", "2026-01-01T05:00:00+05:30")],
        [_completion("A1", "2026-01-15T00:00:00Z", passed=True),
         _completion("A2", "2026-01-01T06:00:00+05:30", passed=True)],
        "2100-01-01T00:00:00Z",
    ),
    "maturity": lambda: (*_maturity_fixture(), "2026-01-10T00:00:00Z"),
    "boundary_mature": lambda: (
        {"L1": "2026-01-01T00:00:00Z"}, *_passed_on_jan_2(), "2026-01-15T00:00:00Z"
    ),
    "boundary_immature": lambda: (
        {"L1": "2026-01-01T00:00:00Z"}, *_passed_on_jan_2(), "2026-01-14T23:59:59Z"
    ),
    "two_arms": lambda: (
        {**_two_arms()[0], **_two_arms()[1]}, *_two_arms()[2:], "2026-01-16T00:00:00Z"
    ),
    "permutation": lambda: (*_permutation_fixture(), "2100-01-01T00:00:00Z"),
    "tie": lambda: (
        {"L1": "2026-01-01T00:00:00+00:00"},
        [_attempt("L1", "A2", "2026-01-02T00:00:00+00:00"),
         _attempt("L1", "A1", "2026-01-02T00:00:00+00:00")],
        [_completion("A1", "2026-01-02T01:00:00+00:00", passed=True),
         _completion("A2", "2026-01-02T01:00:00+00:00", passed=False)],
        "2100-01-01T00:00:00Z",
    ),
    "partition": lambda: (
        {f"L{n}": "2026-01-01T00:00:00Z" for n in range(1, 8)},
        [_attempt(f"L{n}", f"A{n}", "2026-01-05T00:00:00Z" if n == 5 else "2026-01-02T00:00:00Z")
         for n in (1, 2, 4, 5, 6, 7)],
        [_completion("A1", "2026-01-02T01:00:00Z", passed=True),
         _completion("A2", "2026-01-02T01:00:00Z", passed=False),
         _completion("A5", "2026-01-03T00:00:00Z", passed=True),
         _completion("A6", "2026-01-02T01:00:00Z", passed=True),
         _completion("A6", "2026-01-02T01:00:00Z", passed=False),
         _completion("A7", "2026-01-20T00:00:00Z", passed=True)],
        "2100-01-01T00:00:00Z",
    ),
    "true_and_one": lambda: (
        {"L1": "2026-01-01T00:00:00Z"},
        [_attempt("L1", "A1", "2026-01-02T00:00:00Z")],
        [_completion("A1", "2026-01-02T01:00:00Z", passed=True),
         _completion("A1", "2026-01-02T01:00:00Z", passed=1)],
        "2100-01-01T00:00:00Z",
    ),
    "zero_mature": lambda: (
        {"L1": "2026-01-01T00:00:00Z", "L2": "2026-01-03T00:00:00Z"},
        *_passed_on_jan_2(), "2026-01-10T00:00:00Z",
    ),
    "empty": lambda: ({}, [], [], "2100-01-01T00:00:00Z"),
}


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_bucket_sum_invariant_holds_on_every_fixture(name):
    from src.experiments.metrics import mastery_report

    assignments, attempts, completions, as_of = FIXTURES[name]()
    report = mastery_report(assignments, attempts, completions, as_of=as_of)

    _assert_partition(report)
    expected_rate = report.mastered / report.denominator if report.denominator else None
    assert report.rate == expected_rate
