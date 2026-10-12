"""Reconciliation service auditing leaderboard projections and applying differential corrections (AT-7)."""

from datetime import datetime, timezone
import time
from typing import Mapping

from src.leaderboard.models import ReconciliationAudit
from src.leaderboard.store import LeaderboardStore


class LeaderboardReconciler:
    """Audits read models against authoritative aggregate/event history and applies differential fixes."""

    def __init__(self, store: LeaderboardStore) -> None:
        self.store = store

    def reconcile_scope(
        self,
        canonical_state: Mapping[str, tuple[int, str]],
        scope_type: str,
        scope_id: str,
        epoch: str,
        watermark_offset: int = 0,
    ) -> ReconciliationAudit:
        """Audits a leaderboard scope and applies differential updates without flushing state.

        Args:
            canonical_state: Mapping of subject_id -> (true_score, true_first_reached_at).
            scope_type: 'cohort' or 'global'.
            scope_id: cohort_id or 'global'.
            epoch: Calendar week epoch string.
            watermark_offset: Authoritative event watermark.

        Returns:
            ReconciliationAudit with detailed counts and corrected subject IDs.
        """
        start_time = time.perf_counter()
        now_ts = datetime.now(timezone.utc).isoformat()

        # 1. Fetch current read-model entries
        projected_entries = {
            entry.subject_id: entry
            for entry in self.store.get_all_entries(scope_type, scope_id, epoch)
        }

        canonical_subjects = set(canonical_state.keys())
        projected_subjects = set(projected_entries.keys())

        inserted_count = 0
        updated_count = 0
        deleted_count = 0
        corrected_subjects: list[str] = []

        # 2. Check for missing and mismatched entries
        for subject_id, (true_score, true_first_reached) in canonical_state.items():
            proj = projected_entries.get(subject_id)
            if proj is None:
                # Missing from read model -> differential insert
                self.store.upsert_score(
                    scope_type=scope_type,
                    scope_id=scope_id,
                    epoch=epoch,
                    subject_id=subject_id,
                    score=true_score,
                    first_reached_at=true_first_reached,
                    updated_at=now_ts,
                    watermark_offset=watermark_offset,
                )
                inserted_count += 1
                corrected_subjects.append(subject_id)
            elif proj.score != true_score or proj.first_reached_at != true_first_reached:
                # Score or tie timestamp diverged -> differential update
                self.store.upsert_score(
                    scope_type=scope_type,
                    scope_id=scope_id,
                    epoch=epoch,
                    subject_id=subject_id,
                    score=true_score,
                    first_reached_at=true_first_reached,
                    updated_at=now_ts,
                    watermark_offset=watermark_offset,
                )
                updated_count += 1
                corrected_subjects.append(subject_id)

        # 3. Check for orphan entries in projection that do not exist in canonical truth
        orphans = projected_subjects - canonical_subjects
        for orphan_id in orphans:
            self.store.remove_member(scope_type, scope_id, epoch, orphan_id)
            deleted_count += 1
            corrected_subjects.append(orphan_id)

        duration_ms = (time.perf_counter() - start_time) * 1000.0
        divergence_count = inserted_count + updated_count + deleted_count

        return ReconciliationAudit(
            scope_type=scope_type,  # type: ignore
            scope_id=scope_id,
            epoch=epoch,
            audited_at=now_ts,
            canonical_count=len(canonical_state),
            projection_count=len(projected_entries),
            divergence_count=divergence_count,
            inserted_count=inserted_count,
            updated_count=updated_count,
            deleted_count=deleted_count,
            corrected_subject_ids=tuple(sorted(corrected_subjects)),
            duration_ms=round(duration_ms, 3),
        )
