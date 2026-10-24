"""Channel adapters package."""

from src.notifications.channels.base import (
    BaseChannelAdapter,
    PermanentProviderError,
    ProviderError,
    TransientProviderError,
)
from src.notifications.channels.in_app import InAppChannelAdapter
from src.notifications.channels.push import SimulatedPushAdapter

__all__ = [
    "BaseChannelAdapter",
    "InAppChannelAdapter",
    "PermanentProviderError",
    "ProviderError",
    "SimulatedPushAdapter",
    "TransientProviderError",
]
