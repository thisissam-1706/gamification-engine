"""Leaderboard projections consuming aggregate domain events."""

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from src.leaderboard.cohorts import get_calendar_week
from src.leaderboard.store import LeaderboardStore
from src.state_machine.learner import Learner


class LeaderboardProjection:
    """Projects XPAwarded and CohortAssigned facts into global and cohort leaderboards."""

    def __init__(self, store: LeaderboardStore) -> None:
        self.store = store
        # Tracks (subject_id, epoch) -> total_score
        self._epoch_scores: dict[tuple[str, str], int] = defaultdict(int)
        # Tracks (subject_id, epoch) -> timestamp when current score was first reached
        self._first_reached: dict[tuple[str, str], str] = {}
        # Tracks (subject_id, epoch) -> cohort_id
        self._cohort_memberships: dict[tuple[str, str], str] = {}
        # Tracks completion_id -> occurred_at timestamp
        self._completion_occurred_at: dict[str, str] = {}
        self._watermark = 0

    def apply(self, domain_event: Any, watermark_offset: int | None = None) -> None:
        """Applies a domain event to the read model."""
        if watermark_offset is not None:
            self._watermark = max(self._watermark, watermark_offset)
        elif hasattr(domain_event, "originator_version"):
            self._watermark = max(self._watermark, domain_event.originator_version)

        if isinstance(domain_event, Learner.LessonCompleted):
            occ = getattr(domain_event, "occurred_at", None)
            if occ:
                ts = occ.isoformat() if isinstance(occ, datetime) else str(occ)
                self._completion_occurred_at[domain_event.completion_id] = ts

        elif isinstance(domain_event, Learner.CohortAssigned):
            subject_id = str(domain_event.originator_id)
            cohort_id = domain_event.cohort_id
            epoch = str(domain_event.membership_epoch)
            assigned_at = domain_event.assigned_at
            self.assign_cohort(subject_id, cohort_id, epoch, assigned_at, self._watermark)

        elif isinstance(domain_event, Learner.XPAwarded):
            subject_id = str(domain_event.originator_id)
            xp_amount = domain_event.xp_amount
            occurred_at = (
                getattr(domain_event, "occurred_at", None)
                or self._completion_occurred_at.get(domain_event.completion_id)
                or datetime.now(timezone.utc).isoformat()
            )
            epoch = get_calendar_week(occurred_at)
            self.record_xp(subject_id, xp_amount, occurred_at, epoch, self._watermark)

    def record_xp(
        self,
        subject_id: str,
        xp_amount: int,
        occurred_at: str,
        epoch: str | None = None,
        watermark_offset: int = 0,
    ) -> None:
        """Records an XP award for a subject, updating first_reached_at if score increases."""
        epoch = epoch or get_calendar_week(occurred_at)
        key = (subject_id, epoch)
        old_score = self._epoch_scores[key]
        new_score = old_score + xp_amount
        self._epoch_scores[key] = new_score

        if new_score > old_score or key not in self._first_reached:
            self._first_reached[key] = occurred_at

        first_reached = self._first_reached[key]
        self._watermark = max(self._watermark, watermark_offset)

        # 1. Update Global Leaderboard
        self.store.upsert_score(
            scope_type="global",
            scope_id="global",
            epoch=epoch,
            subject_id=subject_id,
            score=new_score,
            first_reached_at=first_reached,
            updated_at=occurred_at,
            watermark_offset=self._watermark,
        )

        # 2. Update Cohort Leaderboard if subject is assigned to a cohort in this epoch
        cohort_id = self._cohort_memberships.get(key)
        if cohort_id:
            self.store.upsert_score(
                scope_type="cohort",
                scope_id=cohort_id,
                epoch=epoch,
                subject_id=subject_id,
                score=new_score,
                first_reached_at=first_reached,
                updated_at=occurred_at,
                watermark_offset=self._watermark,
            )

    def assign_cohort(
        self,
        subject_id: str,
        cohort_id: str,
        epoch: str,
        assigned_at: str,
        watermark_offset: int = 0,
    ) -> None:
        """Assigns a subject to a cohort and projects any accumulated epoch XP into the cohort leaderboard."""
        key = (subject_id, epoch)
        self._cohort_memberships[key] = cohort_id
        self._watermark = max(self._watermark, watermark_offset)

        # If learner already has accumulated score in this epoch, project it to the cohort
        current_score = self._epoch_scores.get(key, 0)
        first_reached = self._first_reached.get(key, assigned_at)

        self.store.upsert_score(
            scope_type="cohort",
            scope_id=cohort_id,
            epoch=epoch,
            subject_id=subject_id,
            score=current_score,
            first_reached_at=first_reached,
            updated_at=assigned_at,
            watermark_offset=self._watermark,
        )

    def clear(self) -> None:
        """Clears in-memory projection tracking and storage."""
        self._epoch_scores.clear()
        self._first_reached.clear()
        self._cohort_memberships.clear()
        self._watermark = 0
        self.store.clear()

    def rebuild(self, domain_events: Iterable[Any]) -> None:
        """Deterministically clears and replays stored domain events."""
        self.clear()
        for idx, event in enumerate(domain_events):
            self.apply(event, watermark_offset=idx + 1)
