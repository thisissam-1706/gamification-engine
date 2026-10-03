"""Validation at the ingestion boundary for versioned event payloads."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft7Validator, ValidationError


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
