"""Validation at the ingestion boundary for versioned event payloads."""

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jsonschema import Draft7Validator, ValidationError

SUPPORTED_SCHEMA_VERSIONS = {
    "learning.lesson_completed": {1},
}

ALLOWED_EVENT_TYPES = {
    "learning-svc": {"learning.lesson_completed"},
    "gamification-engine": {
        "gamification.xp_awarded",
        "gamification.streak_updated",
        "gamification.quest_assigned",
        "gamification.quest_progressed",
        "gamification.quest_completed",
        "gamification.quest_expired",
        "gamification.badge_awarded",
    },
}


def validate_producer(
    producer: str, event_type: str, provenance: Mapping[str, Any] | None = None
) -> None:
    """Rejects untrusted producers and client claims of server authority."""
    if event_type not in ALLOWED_EVENT_TYPES.get(producer, set()):
        raise ValidationError("untrusted_producer")
    if provenance and provenance.get("source") == "server_authoritative":
        raise ValidationError("untrusted_producer")


@lru_cache(maxsize=1)
def lesson_completed_validator() -> Draft7Validator:
    """Loads the checked-in v1 lesson-completed payload contract."""
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "schemas"
        / "events"
        / "lesson_completed.schema.json"
    )
    with schema_path.open(encoding="utf-8") as schema_file:
        schema: dict[str, Any] = json.load(schema_file)
    return Draft7Validator(schema)


def validate_lesson_completed_payload(payload: Mapping[str, Any]) -> None:
    """Raises ``ValidationError`` when a v1 source payload violates its contract."""
    lesson_completed_validator().validate(dict(payload))


def validate_lesson_completed_envelope(envelope: Mapping[str, Any]) -> None:
    """Validates lesson-completed envelope values not covered by JSON Schema."""
    event_type = envelope["event_type"]
    supported_versions = SUPPORTED_SCHEMA_VERSIONS.get(event_type)
    if supported_versions is not None and envelope["schema_version"] not in supported_versions:
        raise ValidationError("unsupported_schema_version")

    for field in ("occurred_at", "ingested_at"):
        value = envelope[field]
        try:
            timestamp = datetime.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError("schema_invalid") from exc
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValidationError("schema_invalid")

    try:
        ZoneInfo(envelope["payload"]["timezone"])
    except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError) as exc:
        raise ValidationError("schema_invalid") from exc
