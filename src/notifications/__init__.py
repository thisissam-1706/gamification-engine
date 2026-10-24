"""Multi-channel notifications engine (Milestone M5)."""

from src.notifications.dispatcher import NotificationDispatcher
from src.notifications.guardrails import (
    check_consent,
    check_fatigue_cap,
    evaluate_quiet_hours,
    is_ttl_expired,
)
from src.notifications.models import (
    ChannelType,
    LearnerPreferences,
    NotificationCategory,
    NotificationProposal,
    NotificationStatus,
    TerminalFact,
)
from src.notifications.queue import RetryScheduler
from src.notifications.store import NotificationStore

__all__ = [
    "ChannelType",
    "LearnerPreferences",
    "NotificationCategory",
    "NotificationDispatcher",
    "NotificationProposal",
    "NotificationStatus",
    "NotificationStore",
    "RetryScheduler",
    "TerminalFact",
    "check_consent",
    "check_fatigue_cap",
    "evaluate_quiet_hours",
    "is_ttl_expired",
]
