"""AT-12: Deterministic Experiment Assignment (M7)

Given learner L1 and experiment exp_007 with a fixed hash algorithm v2, when assignment runs twice, then L1 is bucketed into the same variant both times, with the propensity score logged on the event.
"""
import hashlib
import json

import pytest

from src.experiments.assignment import assign


def test_same_learner_is_assigned_deterministically_with_propensity():
    first = assign("L1", "exp_007")
    second = assign("L1", "exp_007")

    assert first == second
    assert first[0] in {"variant_a", "variant_b"}
    assert first[1] == 0.5


def test_assignment_is_within_two_percent_of_fifty_fifty():
    assignments = [
        assign(f"synthetic-{index}", "exp_007")[0] for index in range(10_000)
    ]
    treatment_share = assignments.count("variant_a") / len(assignments)

    assert abs(treatment_share - 0.5) <= 0.02


def test_assignment_encoding_does_not_collide_for_ambiguous_concatenations():
    old_encoding = "ab" + "c"
    other_old_encoding = "a" + "bc"
    assert old_encoding == other_old_encoding

    encoded = [
        json.dumps(values, separators=(",", ":")).encode("utf-8")
        for values in (("ab", "c"), ("a", "bc"))
    ]
    buckets = [
        int.from_bytes(hashlib.sha256(value).digest()[:8], "big") % 100
        for value in encoded
    ]

    assert buckets[0] != buckets[1]
    assert [assign(*values)[0] for values in (("ab", "c"), ("a", "bc"))] == [
        "variant_b",
        "variant_b",
    ]


@pytest.mark.parametrize(
    ("subject_id", "exp_id", "expected_variant"),
    [
        ("L1", "exp_007", "variant_b"),
        ("learner-001", "exp_007", "variant_b"),
        ("learner-002", "exp_007", "variant_a"),
        ("learner-004", "exp_007", "variant_a"),
        ("research subject", "experiment alpha", "variant_b"),
    ],
)
def test_assignment_matches_golden_hash_buckets(subject_id, exp_id, expected_variant):
    encoded = json.dumps([subject_id, exp_id], separators=(",", ":")).encode("utf-8")
    bucket = int.from_bytes(hashlib.sha256(encoded).digest()[:8], "big") % 100
    expected_from_bucket = "variant_a" if bucket < 50 else "variant_b"

    assert expected_from_bucket == expected_variant
    assert assign(subject_id, exp_id) == (expected_variant, 0.5)


@pytest.mark.skip(reason="Exposure logging is not implemented yet.")
def test_exposure_is_logged_only_after_treatment_surface_view():
    pass
