"""Simulated push notification delivery channel adapter with fault injection."""

from typing import Any

from src.notifications.channels.base import (
    BaseChannelAdapter,
    PermanentProviderError,
    TransientProviderError,
)
from src.notifications.models import NotificationProposal


class SimulatedPushAdapter(BaseChannelAdapter):
    """Simulated external push notification adapter supporting fault injection."""

    def __init__(
        self,
        fail_attempts: int = 0,
        fail_permanently: bool = False,
    ) -> None:
        self.fail_attempts = fail_attempts
        self.fail_permanently = fail_permanently
        self.attempts_seen = 0
        self.delivered_messages: list[dict[str, Any]] = []

    def send(self, proposal: NotificationProposal) -> bool:
        self.attempts_seen += 1

        if self.fail_permanently:
            raise PermanentProviderError("Simulated unrecoverable provider failure")

        if self.attempts_seen <= self.fail_attempts:
            raise TransientProviderError(
                f"Simulated provider network timeout (attempt {self.attempts_seen}/{self.fail_attempts})"
            )

        self.delivered_messages.append(
            {
                "proposal_id": proposal.proposal_id,
                "learner_id": proposal.learner_id,
                "category": proposal.category.value,
                "payload": dict(proposal.payload),
            }
        )
        return True
