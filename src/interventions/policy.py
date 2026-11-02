"""Pure remediation policy: decides whether a checkpoint attempt earns a remediation quest."""

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping

from src.interventions.config import RemediationConfig


@dataclass(frozen=True)
class RemediationDecision:
    decision_id: str
    attempt_id: str
    assessment_id: str
    action: str  # "assign" or "no_action"
    reason: str | None
    decided_at: datetime
    deadline_at: datetime | None
    quest_id: str | None
    policy_version: str


def decide_remediation(
    attempts: Mapping[str, Mapping[str, object]],
    first_attempt_ids: Mapping[str, str],
    prior_decisions: Iterable[RemediationDecision],
    attempt_id: str,
    now: datetime,
    config: RemediationConfig,
) -> RemediationDecision:
    """Decides remediation for one attempt; rules are checked in a fixed order.

    Raises KeyError for an unknown attempt and ValueError for a naive ``now``.
    """
    attempt = attempts[attempt_id]
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    assessment_id = str(attempt["assessment_id"])

    def no_action(reason: str) -> RemediationDecision:
        return RemediationDecision(
            decision_id=f"remediation:{attempt_id}",
            attempt_id=attempt_id,
            assessment_id=assessment_id,
            action="no_action",
            reason=reason,
            decided_at=now,
            deadline_at=None,
            quest_id=None,
            policy_version=config.policy_version,
        )

    if first_attempt_ids.get(assessment_id) != attempt_id:
        return no_action("not_first_attempt")
    if attempt["passed"]:
        return no_action("passed")
    window_start = now - config.frequency_window
    if any(d.action == "assign" and d.decided_at > window_start for d in prior_decisions):
        return no_action("remediated_within_window")
    return RemediationDecision(
        decision_id=f"remediation:{attempt_id}",
        attempt_id=attempt_id,
        assessment_id=assessment_id,
        action="assign",
        reason=None,
        decided_at=now,
        deadline_at=now + config.quest_deadline,
        quest_id=config.quest_id,
        policy_version=config.policy_version,
    )
