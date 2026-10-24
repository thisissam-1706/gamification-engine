"""Abstract base channel adapter interface and provider errors."""

from abc import ABC, abstractmethod

from src.notifications.models import NotificationProposal


class ProviderError(Exception):
    """Base exception for notification provider failures."""


class TransientProviderError(ProviderError):
    """Temporary provider failure (e.g. network timeout, 503) that qualifies for retry."""


class PermanentProviderError(ProviderError):
    """Permanent provider failure (e.g. invalid device token, unrecoverable reject)."""


class BaseChannelAdapter(ABC):
    """Abstract interface for a notification delivery provider adapter."""

    @abstractmethod
    def send(self, proposal: NotificationProposal) -> bool:
        """Attempts to deliver the notification.

        Raises:
            TransientProviderError: for transient network/service failures.
            PermanentProviderError: for terminal delivery failures.
        """
        raise NotImplementedError
