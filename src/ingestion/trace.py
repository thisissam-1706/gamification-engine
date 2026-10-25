"""Read-only trace from one source delivery to the rewards it produced."""

from dataclasses import dataclass
from uuid import UUID

from src.ingestion.evidence import EvidenceRecord, EvidenceStore
from src.state_machine.learner import Learner, LearnerApplication


@dataclass(frozen=True)
class DeliveryProvenance:
    event_id: str
    learner_id: str
    action_id: str
    digest: str | None
    producer: str | None
    event_type: str | None
    schema_version: int | None
    occurred_at: str | None
    ingested_at: str | None
    correlation_id: str | None
    causation_id: str | None
    dedup_key: str | None
    rule_version: str | None
    rule_digest: str | None


@dataclass(frozen=True)
class LessonCompletedFact:
    originator_version: int
    source_event_id: str | None


@dataclass(frozen=True)
class XPAwardedFact:
    reward_id: str
    amount: int
    rule_version: str


@dataclass(frozen=True)
class DeliveryTrace:
    provenance: DeliveryProvenance
    lesson_completed: LessonCompletedFact | None
    xp_awards: tuple[XPAwardedFact, ...]
    evidence: tuple[EvidenceRecord, ...]
    # Exposure facts (which variant/surface showed the reward) arrive with the
    # pilot; until then nothing records them, so this is always empty.
    exposures: tuple[()] = ()


def trace_delivery(
    application: LearnerApplication, evidence: EvidenceStore, event_id: str
) -> DeliveryTrace | None:
    """Follows event_id -> bound action -> stored facts; writes nothing."""
    binding = evidence.delivery_binding(event_id)
    if binding is None:
        return None
    provenance = DeliveryProvenance(
        event_id=binding.event_id,
        learner_id=binding.learner_id,
        action_id=binding.action_id,
        digest=binding.digest,
        producer=binding.producer,
        event_type=binding.event_type,
        schema_version=binding.schema_version,
        occurred_at=binding.occurred_at,
        ingested_at=binding.ingested_at,
        correlation_id=binding.correlation_id,
        causation_id=binding.causation_id,
        dedup_key=binding.dedup_key,
        rule_version=binding.rule_version,
        rule_digest=binding.rule_digest,
    )

    # Reads stored events directly; the aggregate is never loaded or saved.
    lesson_completed: LessonCompletedFact | None = None
    xp_awards: list[XPAwardedFact] = []
    for domain_event in application.events.get(UUID(binding.learner_id)):
        if (
            isinstance(domain_event, Learner.LessonCompleted)
            and domain_event.completion_id == binding.action_id
        ):
            lesson_completed = LessonCompletedFact(
                originator_version=domain_event.originator_version,
                source_event_id=domain_event.source_event_id,
            )
        elif (
            isinstance(domain_event, Learner.XPAwarded)
            and domain_event.completion_id == binding.action_id
        ):
            xp_awards.append(
                XPAwardedFact(
                    reward_id=domain_event.reward_id,
                    amount=domain_event.xp_amount,
                    rule_version=domain_event.rule_version,
                )
            )

    return DeliveryTrace(
        provenance=provenance,
        lesson_completed=lesson_completed,
        xp_awards=tuple(xp_awards),
        evidence=tuple(evidence.list_mentioning(event_id)),
    )
