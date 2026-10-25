"""Data models and value objects for multi-channel notifications (Milestone M5)."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping


class ChannelType(str, Enum):
    IN_APP = "in_app"
    PUSH = "push"
    EMAIL = "email"


class NotificationCategory(str, Enum):
    STREAK_RISK = "streak_risk"
    REMEDIATION = "remediation"
    QUEST_UPDATE = "quest_update"
    SOCIAL_NUDGE = "social_nudge"
    GENERAL = "general"


class NotificationStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    SUPPRESSED = "suppressed"
    FAILED = "failed"


@dataclass(frozen=True)
class NotificationProposal:
    """A generated notification candidate proposal awaiting guardrail evaluation and dispatch."""

    proposal_id: str
    learner_id: str
    channel: ChannelType
    category: NotificationCategory
    template_id: str = "default_template"
    payload: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = ""  # ISO UTC
    ttl_seconds: int = 86400  # Default 24h TTL


@dataclass(frozen=True)
class LearnerPreferences:
    """Learner timezone and communication preferences."""

    learner_id: str
    timezone: str = "UTC"
    opted_out_channels: set[ChannelType] = field(default_factory=set)
    opted_out_categories: set[NotificationCategory] = field(default_factory=set)


@dataclass(frozen=True)
class TerminalFact:
    """Immutable terminal outcome fact emitted after notification handling."""

    event_id: str
    event_type: str  # 'notification_dispatched', 'notification_suppressed', 'notification_failed'
    proposal_id: str
    learner_id: str
    channel: str
    category: str
    timestamp: str  # ISO UTC
    status: NotificationStatus
    reason: str | None = None
    rescheduled_for: str | None = None
    attempt_count: int = 1
