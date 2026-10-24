"""In-app delivery channel adapter."""

from typing import Any

from src.notifications.channels.base import BaseChannelAdapter
from src.notifications.models import NotificationProposal


class InAppChannelAdapter(BaseChannelAdapter):
    """In-app notification delivery adapter writing directly to recipient inbox."""

    def __init__(self) -> None:
        self.delivered_messages: list[dict[str, Any]] = []

    def send(self, proposal: NotificationProposal) -> bool:
        self.delivered_messages.append(
            {
                "proposal_id": proposal.proposal_id,
                "learner_id": proposal.learner_id,
                "category": proposal.category.value,
                "payload": dict(proposal.payload),
                "created_at": proposal.created_at,
            }
        )
        return True
