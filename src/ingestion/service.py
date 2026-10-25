"""Validated lesson-completion ingestion with durable duplicate evidence."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID

from jsonschema import Draft7Validator, FormatChecker, ValidationError
from eventsourcing.application import AggregateNotFoundError
from eventsourcing.persistence import IntegrityError

from src.ingestion.evidence import DeliveryBinding, EvidenceStore
from src.ingestion.validation import (
    DEFAULT_INGEST_RULE_VERSION,
    expected_dedup_key,
    validate_lesson_completed_envelope,
    validate_lesson_completed_payload,
    validate_producer,
)
from src.state_machine.learner import Learner, LearnerApplication


@dataclass(frozen=True)
class IngestionResult:
    status: str
    reason: str | None = None


DIGEST_FIELDS = (
    "event_type",
    "schema_version",
    "subject_id",
    "producer",
    "occurred_at",
    "payload",
)


def canonical_digest(envelope: Mapping[str, Any]) -> str:
    """Hashes the producer-owned content of an envelope, not its transport metadata."""
    content = {field: envelope[field] for field in DIGEST_FIELDS}
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _action_for_event_id(learner: Learner, event_id: str) -> str | None:
    for completion_id, source_event_id in learner.completion_sources.items():
        if source_event_id == event_id:
            return completion_id
    return None


def _resolve_bound_event_id(
    evidence: EvidenceStore,
    binding: DeliveryBinding,
    learner_id: str,
    action_id: str,
    event_id: str,
    digest: str,
) -> IngestionResult:
    """Answers a delivery whose event_id is already bound to an action."""
    if (
        binding.learner_id != learner_id
        or binding.action_id != action_id
        or (binding.digest is not None and binding.digest != digest)
    ):
        evidence.record(
            "rejected",
            learner_id,
            "conflicting_event_id_reuse",
            action_id=action_id,
            event_id=event_id,
            original_ref=binding.action_id,
        )
        return IngestionResult("rejected", "conflicting_event_id_reuse")
    if binding.digest is None:
        evidence.fill_delivery_digest(event_id, digest)
    evidence.record(
        "duplicate",
        learner_id,
        "duplicate_event_id",
        action_id=action_id,
        event_id=event_id,
        original_ref=binding.event_id,
    )
    return IngestionResult("duplicate", "duplicate_event_id")


def _original_delivery(
    evidence: EvidenceStore, learner_id: str, action_id: str
) -> str | None:
    return evidence.original_event_id(learner_id, action_id)


def _quest_already_complete(learner: Learner, assignment_id: str | None) -> bool:
    return (
        assignment_id is not None
        and f"quest_completion:{assignment_id}" in learner.completed_quest_ids
    )


def _envelope_validator() -> Draft7Validator:
    path = Path(__file__).resolve().parents[2] / "schemas" / "envelope.schema.json"
    return Draft7Validator(
        json.loads(path.read_text(encoding="utf-8")),
        format_checker=FormatChecker(),
    )


def ingest_lesson_completed(
    application: LearnerApplication,
    evidence: EvidenceStore,
    envelope: Mapping[str, Any],
    *,
    authenticated_producer: str,
) -> IngestionResult:
    """Validates and accepts one lesson completion or records its outcome."""
    learner_id = str(envelope.get("subject_id", ""))
    action_id = (
        str(envelope.get("payload", {}).get("completion_id"))
        if isinstance(envelope.get("payload"), Mapping)
        and envelope["payload"].get("completion_id") is not None
        else None
    )
    assignment_id = (
        str(envelope.get("payload", {}).get("assignment_id"))
        if isinstance(envelope.get("payload"), Mapping)
        and envelope["payload"].get("assignment_id") is not None
        else None
    )
    event_id = str(envelope.get("event_id")) if envelope.get("event_id") else None

    try:
        validate_lesson_completed_envelope(envelope)
        _envelope_validator().validate(dict(envelope))
        UUID(str(envelope["event_id"]))
        if authenticated_producer != str(envelope["producer"]):
            raise ValidationError("untrusted_producer")
        validate_producer(
            str(envelope["producer"]),
            str(envelope["event_type"]),
            envelope.get("provenance"),
        )
        if envelope["event_type"] != "learning.lesson_completed":
            raise ValidationError("untrusted_producer")
        payload = envelope["payload"]
        if any(key in payload for key in ("xp_amount", "reward_id", "new_streak")):
            raise ValidationError("reward_field_in_source_event")
        validate_lesson_completed_payload(payload)
        learner_uuid = UUID(learner_id)
        if envelope["dedup_key"] != expected_dedup_key(
            str(envelope["event_id"]), DEFAULT_INGEST_RULE_VERSION
        ):
            raise ValidationError("dedup_key_mismatch")
    except ValidationError as exc:
        reason = str(exc.message) if str(exc.message) in {
            "untrusted_producer",
            "reward_field_in_source_event",
            "unsupported_schema_version",
            "schema_invalid",
            "dedup_key_mismatch",
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

    if event_id is None or action_id is None:
        raise RuntimeError("validated event_id or completion_id unexpectedly missing")
    digest = canonical_digest(envelope)
    binding = evidence.delivery_binding(event_id)
    if binding is not None:
        return _resolve_bound_event_id(
            evidence, binding, learner_id, action_id, event_id, digest
        )

    try:
        learner = application.repository.get(learner_uuid)
    except AggregateNotFoundError as exc:
        raise RuntimeError("learner aggregate must exist before ingestion") from exc

    # Crash gap: the aggregate accepted this event_id but its binding was never written.
    bound_action = _action_for_event_id(learner, event_id)
    if bound_action is not None:
        evidence.bind_delivery(
            event_id,
            learner_id,
            bound_action,
            digest if bound_action == action_id else None,
        )
        return _resolve_bound_event_id(
            evidence,
            DeliveryBinding(event_id, learner_id, bound_action, None),
            learner_id,
            action_id,
            event_id,
            digest,
        )

    original = _original_delivery(evidence, learner_id, action_id)
    if original is None and action_id is not None:
        original = learner.completion_sources.get(action_id)
        if original is not None:
            evidence.remember_delivery(learner_id, action_id, original)
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

    quest_already_complete = _quest_already_complete(learner, assignment_id)
    accepted = learner.complete_lesson(
        action_id,
        str(envelope["payload"]["lesson_id"]),
        source_event_id=event_id,
        occurred_at=str(envelope["occurred_at"]),
        timezone=str(envelope["payload"]["timezone"]),
    )
    if not accepted:
        original = learner.completion_sources.get(action_id)
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
        application.save(learner)
    except IntegrityError:
        learner = application.repository.get(learner_uuid)
        bound_action = _action_for_event_id(learner, event_id)
        if bound_action is not None and bound_action != action_id:
            evidence.bind_delivery(event_id, learner_id, bound_action, None)
            evidence.record(
                "rejected",
                learner_id,
                "conflicting_event_id_reuse",
                action_id=action_id,
                event_id=event_id,
                original_ref=bound_action,
            )
            return IngestionResult("rejected", "conflicting_event_id_reuse")
        if action_id in learner.completed_lesson_ids:
            original = learner.completion_sources.get(action_id)
            if original is not None:
                evidence.remember_delivery(learner_id, action_id, original)
            evidence.record(
                "duplicate",
                learner_id,
                "duplicate_completion_id",
                action_id=action_id,
                event_id=event_id,
                original_ref=original,
            )
            return IngestionResult("duplicate", "duplicate_completion_id")

        quest_already_complete = _quest_already_complete(learner, assignment_id)
        accepted = learner.complete_lesson(
            action_id,
            str(envelope["payload"]["lesson_id"]),
            source_event_id=event_id,
            occurred_at=str(envelope["occurred_at"]),
            timezone=str(envelope["payload"]["timezone"]),
        )
        if not accepted:
            original = learner.completion_sources.get(action_id)
            if original is not None:
                evidence.remember_delivery(learner_id, action_id, original)
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
            application.save(learner)
        except IntegrityError:
            evidence.record(
                "rejected",
                learner_id,
                "concurrency_conflict",
                action_id=action_id,
                event_id=event_id,
            )
            return IngestionResult("rejected", "concurrency_conflict")
    evidence.remember_delivery(learner_id, action_id, event_id)
    evidence.bind_delivery(event_id, learner_id, action_id, digest)
    if quest_already_complete:
        quest_completion_id = f"quest_completion:{assignment_id}"
        evidence.record(
            "duplicate",
            learner_id,
            "quest_already_completed",
            action_id=quest_completion_id,
            event_id=event_id,
            original_ref=quest_completion_id,
        )
        return IngestionResult("accepted", "quest_already_completed")
    return IngestionResult("accepted")
