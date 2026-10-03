"""Contract acceptance tests for learning.lesson_completed, v1.

These tests exercise the validation boundary independently of aggregate logic.
"""
import pytest

from jsonschema import ValidationError

from src.ingestion.validation import validate_lesson_completed_payload


VALID_PAYLOAD = {
    "completion_id": "cmp_492_003",
    "lesson_id": "l_492",
    "timezone": "Asia/Kolkata",
}


@pytest.mark.parametrize("missing_field", ("completion_id", "lesson_id", "timezone"))
def test_lesson_completed_requires_a_stable_completion_identity(missing_field):
    """Reject a payload missing any required business-action identity field."""
    payload = {key: value for key, value in VALID_PAYLOAD.items() if key != missing_field}

    with pytest.raises(ValidationError):
        validate_lesson_completed_payload(payload)


def test_lesson_completed_rejects_reward_outcomes_in_the_source_payload():
    """Reject fields such as xp_amount: source events must not embed decisions."""
    payload = {**VALID_PAYLOAD, "xp_amount": 10}

    with pytest.raises(ValidationError):
        validate_lesson_completed_payload(payload)


def test_lesson_completed_accepts_a_valid_source_payload():
    """Accept the minimum valid v1 source payload."""
    validate_lesson_completed_payload(VALID_PAYLOAD)
