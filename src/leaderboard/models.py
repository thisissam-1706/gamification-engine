"""Domain models and value objects for cohorts and leaderboards."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal


@dataclass(frozen=True)
class CohortDefinition:
    """Represents a bounded group of learners competing in an epoch."""

    cohort_id: str
    strategy: Literal["activity_volume", "course_module"] = "activity_volume"
    epoch: str = ""
    min_size: int = 500
    max_size: int = 5000
    status: Literal["forming", "active", "sealed", "finalized"] = "forming"


@dataclass(frozen=True)
class CohortMembership:
    """Records a learner's assignment to a cohort for an epoch."""

    subject_id: str
    cohort_id: str
    epoch: str
    assigned_at: str
    status: Literal["active", "inactive"] = "active"
    last_active_at: str | None = None


@dataclass(frozen=True)
class LeaderboardEntry:
    """Raw record of a learner's score and first-reached timestamp in a leaderboard."""

    scope_type: Literal["cohort", "global"]
    scope_id: str
    epoch: str
    subject_id: str
    score: int
    first_reached_at: str
    last_updated_at: str
    watermark_offset: int = 0


@dataclass(frozen=True)
class RankedLearner:
    """Read model output showing a learner's calculated rank and score."""

    rank: int
    subject_id: str
    score: int
    first_reached_at: str
    scope_type: Literal["cohort", "global"] = "cohort"
    scope_id: str = ""
    epoch: str = ""


@dataclass(frozen=True)
class ReconciliationAudit:
    """Durable audit record emitted when reconciliation audits read projections."""

    scope_type: Literal["cohort", "global"]
    scope_id: str
    epoch: str
    audited_at: str
    canonical_count: int
    projection_count: int
    divergence_count: int
    inserted_count: int
    updated_count: int
    deleted_count: int
    corrected_subject_ids: tuple[str, ...] = field(default_factory=tuple)
    duration_ms: float = 0.0
    audit_id: str = ""
    pinned_offset: int = 0

