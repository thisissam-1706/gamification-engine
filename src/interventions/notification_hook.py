"""Runs the remediation policy and tells the learner about a new remediation quest."""

from datetime import datetime, timezone
from typing import Callable
from uuid import UUID

from src.interventions.config import RemediationConfig
from src.interventions.policy import RemediationDecision
from src.interventions.service import run_remediation
from src.notifications.dispatcher import NotificationDispatcher
from src.notifications.models import (
    ChannelType,
    LearnerPreferences,
    NotificationCategory,
    NotificationProposal,
    TerminalFact,
)
from src.state_machine.learner import LearnerApplication

REMEDIATION_TEMPLATE_ID = "checkpoint_remediation_v1"


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_remediation_with_notification(
    application: LearnerApplication,
    learner_id: str,
    attempt_id: str,
    *,
    clock: Callable[[], datetime],
    dispatcher: NotificationDispatcher,
    prefs: LearnerPreferences | None,
    config: RemediationConfig = RemediationConfig(),
    channel: ChannelType = ChannelType.PUSH,
) -> tuple[RemediationDecision, TerminalFact | None]:
    """Runs ``run_remediation``; an ``assign`` decision is then handed to the dispatcher.

    Other decisions send nothing and return ``(decision, None)``. The proposal id is
    ``remediation_notice:<attempt_id>``, so running the hook again for the same attempt is
    deduplicated by the dispatcher. The notification outcome (delivered, suppressed,
    rescheduled or failed) never changes the stored decision or quest, and errors from
    ``run_remediation`` are not caught.
    """
    decision = run_remediation(application, learner_id, attempt_id, clock=clock, config=config)
    if decision.action != "assign":
        return decision, None

    assert decision.deadline_at is not None  # an assign decision always has a deadline
    # The notice belongs to the same chain as the attempt delivery that triggered it.
    learner = application.repository.get(UUID(learner_id))
    correlation_id, causation_id = learner.checkpoint_chain_ids(attempt_id)
    proposal = NotificationProposal(
        proposal_id=f"remediation_notice:{attempt_id}",
        learner_id=learner_id,
        channel=channel,
        category=NotificationCategory.REMEDIATION,
        template_id=REMEDIATION_TEMPLATE_ID,
        payload={
            "quest_id": decision.quest_id,
            "assignment_id": f"remediation_quest:{attempt_id}",
            "deadline_at": _iso_utc(decision.deadline_at),
            "assessment_id": decision.assessment_id,
            "attempt_id": attempt_id,
        },
        created_at=_iso_utc(decision.decided_at),
        correlation_id=correlation_id,
        causation_id=causation_id,
    )
    return decision, dispatcher.dispatch(proposal, prefs=prefs, now_utc=clock())
