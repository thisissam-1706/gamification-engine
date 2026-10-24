"""High-level LeaderboardService providing query APIs with watermark consistency verification."""

from typing import Literal

from src.leaderboard.models import RankedLearner
from src.leaderboard.store import LeaderboardStore


class LeaderboardService:
    """Service facade querying leaderboard projections and verifying consistency watermarks."""

    def __init__(self, store: LeaderboardStore) -> None:
        self.store = store

    def get_top_k(
        self,
        scope_type: Literal["cohort", "global"],
        scope_id: str,
        epoch: str,
        k: int = 10,
    ) -> list[RankedLearner]:
        """Fetches top-K learners in deterministic tie-broken order."""
        return self.store.get_top_k(scope_type, scope_id, epoch, k=k)

    def get_surrounding(
        self,
        scope_type: Literal["cohort", "global"],
        scope_id: str,
        epoch: str,
        subject_id: str,
        window: int = 5,
    ) -> list[RankedLearner]:
        """Fetches learners within rank +/- window of the specified subject."""
        return self.store.get_surrounding(
            scope_type, scope_id, epoch, subject_id, window=window
        )

    def get_learner_rank(
        self,
        scope_type: Literal["cohort", "global"],
        scope_id: str,
        epoch: str,
        subject_id: str,
    ) -> RankedLearner | None:
        """Fetches the exact rank, score, and tie metadata for a learner."""
        return self.store.get_rank(scope_type, scope_id, epoch, subject_id)

    def get_watermark(
        self, scope_type: Literal["cohort", "global"], scope_id: str, epoch: str
    ) -> int:
        """Returns the latest processed event watermark offset for the scope."""
        return self.store.get_watermark(scope_type, scope_id, epoch)

    def is_consistent_with_watermark(
        self,
        scope_type: Literal["cohort", "global"],
        scope_id: str,
        epoch: str,
        expected_watermark: int,
    ) -> bool:
        """Verifies read-your-writes guarantee: read model is at least at expected watermark."""
        return self.get_watermark(scope_type, scope_id, epoch) >= expected_watermark

    def check_watermark_lag(
        self,
        scope_type: Literal["cohort", "global"],
        scope_id: str,
        epoch: str,
        required_watermark: int,
    ) -> dict[str, int | bool]:
        """Returns watermark consistency verification and explicit lag count."""
        current = self.get_watermark(scope_type, scope_id, epoch)
        lag = max(0, required_watermark - current)
        return {
            "current_watermark": current,
            "required_watermark": required_watermark,
            "is_consistent": current >= required_watermark,
            "lag": lag,
        }

