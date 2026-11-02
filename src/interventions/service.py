"""Runs the remediation policy against a stored learner and records the decision."""

from datetime import datetime
from typing import Callable
from uuid import UUID

from src.interventions.config import RemediationConfig
from src.interventions.policy import RemediationDecision, decide_remediation
from src.state_machine.learner import LearnerApplication


def _decision_from_record(record: dict[str, object]) -> RemediationDecision:
    deadline = record["deadline_at"]
    return RemediationDecision(
        decision_id=str(record["decision_id"]),
        attempt_id=str(record["attempt_id"]),
        assessment_id=str(record["assessment_id"]),
        action=str(record["action"]),
        reason=None if record["reason"] is None else str(record["reason"]),
        decided_at=datetime.fromisoformat(str(record["decided_at"])),
        deadline_at=None if deadline is None else datetime.fromisoformat(str(deadline)),
        quest_id=None if record["quest_id"] is None else str(record["quest_id"]),
        policy_version=str(record["policy_version"]),
    )


def run_remediation(
    application: LearnerApplication,
    learner_id: str,
    attempt_id: str,
    *,
    clock: Callable[[], datetime],
    config: RemediationConfig = RemediationConfig(),
) -> RemediationDecision:
    """Returns the stored decision for the attempt, or decides, records and saves one.

    An ``assign`` decision also assigns its quest, in the same save.
    """
    learner = application.repository.get(UUID(learner_id))
    stored = learner.remediation_decisions.get(f"remediation:{attempt_id}")
    if stored is not None:
        return _decision_from_record(stored)
    prior = [_decision_from_record(r) for r in learner.remediation_decisions.values()]
    decision = decide_remediation(
        learner.checkpoint_attempts,
        learner.first_checkpoint_attempt_ids,
        prior,
        attempt_id,
        clock(),
        config,
    )
    learner.record_remediation_decision(decision)
    if decision.action == "assign":
        # Raises before the save for an unknown quest, so no decision is stored without its quest.
        learner.assign_quest(
            config.quest_id,
            assignment_id=f"remediation_quest:{attempt_id}",
            assigned_at=decision.decided_at,
            deadline_at=decision.deadline_at,
            definition_version=config.quest_definition_version,
        )
    application.save(learner)
    return decision


def run_quest_expiry(
    application: LearnerApplication,
    learner_id: str,
    *,
    clock: Callable[[], datetime],
) -> list[str]:
    """Expires the learner's overdue quests; saves only if something expired."""
    learner = application.repository.get(UUID(learner_id))
    expired = learner.expire_due_quests(clock())
    if expired:
        application.save(learner)
    return expired
