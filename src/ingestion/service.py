"""Validated lesson-completion ingestion with durable duplicate evidence."""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID

from jsonschema import Draft7Validator, ValidationError
from eventsourcing.application import AggregateNotFoundError

from src.ingestion.evidence import EvidenceStore
from src.ingestion.validation import (
    validate_lesson_completed_payload,
    validate_producer,
)
from src.state_machine.learner import Learner, LearnerApplication


@dataclass(frozen=True)
class IngestionResult:
    status: str
    reason: str | None = None


def _envelope_validator() -> Draft7Validator:
    path = Path(__file__).resolve().parents[2] / "schemas" / "envelope.schema.json"
    return Draft7Validator(json.loads(path.read_text(encoding="utf-8")))


def ingest_lesson_completed(
    application: LearnerApplication,
    evidence: EvidenceStore,
    envelope: Mapping[str, Any],
) -> IngestionResult:
    """Validates and accepts one lesson completion or records its outcome."""
    learner_id = str(envelope.get("subject_id", ""))
    action_id = (
        str(envelope.get("payload", {}).get("completion_id"))
        if isinstance(envelope.get("payload"), Mapping)
        and envelope["payload"].get("completion_id") is not None
        else None
    )
    event_id = str(envelope.get("event_id")) if envelope.get("event_id") else None

    try:
        _envelope_validator().validate(dict(envelope))
        UUID(str(envelope["event_id"]))
        if envelope["event_type"] != "learning.lesson_completed":
            raise ValidationError("schema_invalid")
        validate_producer(
            str(envelope["producer"]),
            str(envelope["event_type"]),
            envelope.get("provenance"),
        )
        payload = envelope["payload"]
        if any(key in payload for key in ("xp_amount", "reward_id", "new_streak")):
            raise ValidationError("reward_field_in_source_event")
        validate_lesson_completed_payload(payload)
        learner_uuid = UUID(learner_id)
    except ValidationError as exc:
        reason = str(exc.message) if str(exc.message) in {
            "untrusted_producer",
            "reward_field_in_source_event",
            "schema_invalid",
        } else "schema_invalid"
        evidence.record(
            "rejected",
            learner_id,
            reason,
            action_id=action_id,
            event_id=event_id,
        )
        return IngestionResult("rejected", reason)
    except (KeyError, TypeError, ValueError):
        reason = "schema_invalid"
        evidence.record(
            "rejected",
            learner_id,
            reason,
            action_id=action_id,
            event_id=event_id,
        )
        return IngestionResult("rejected", reason)

    original = evidence.original_event_id(learner_id, action_id)
    if original is not None:
        evidence.record(
            "duplicate",
            learner_id,
            "duplicate_completion_id",
            action_id=action_id,
            event_id=event_id,
            original_ref=original,
        )
        return IngestionResult("duplicate", "duplicate_completion_id")

    try:
        learner = application.repository.get(learner_uuid)
    except AggregateNotFoundError as exc:
        raise RuntimeError("learner aggregate must exist before ingestion") from exc
    accepted = learner.complete_lesson(
        action_id,
        str(envelope["payload"]["lesson_id"]),
        occurred_at=str(envelope["occurred_at"]),
        timezone=str(envelope["payload"]["timezone"]),
    )
    if not accepted:
        original = evidence.original_event_id(learner_id, action_id)
        evidence.record(
            "duplicate",
            learner_id,
            "duplicate_completion_id",
            action_id=action_id,
            event_id=event_id,
            original_ref=original,
        )
        return IngestionResult("duplicate", "duplicate_completion_id")
    application.save(learner)
    evidence.remember_delivery(learner_id, action_id, event_id)
    return IngestionResult("accepted")
