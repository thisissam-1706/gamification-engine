"""Reconciliation service auditing leaderboard projections and applying differential corrections (AT-7)."""

from collections.abc import Mapping
from datetime import datetime, timezone
import itertools
import time
from typing import Any
import uuid

from src.leaderboard.models import ReconciliationAudit
from src.leaderboard.store import LeaderboardStore

# Atomic in-process sequence counter for collision-safe audit IDs
_audit_counter = itertools.count(1)


class LeaderboardReconciler:
    """Audits read models against authoritative aggregate/event history and applies differential fixes."""

    def __init__(self, store: LeaderboardStore) -> None:
        self.store = store

    def build_canonical_state(
        self,
        scope_type: str,
        scope_id: str,
        epoch: str,
        pinned_offset: int,
    ) -> dict[str, tuple[int, str]]:
        """Rebuilds the authoritative canonical state (subject_id -> (score, first_reached_at))
        from the event stream (applied events) up to pinned_offset.
        """
        applied_events = self.store.get_applied_events(epoch=epoch)
        events_up_to_pin = [
            e for e in applied_events
            if pinned_offset == 0 or e.get("stream_offset", 0) <= pinned_offset
        ]

        # Filter events for the requested scope
        events_by_subject: dict[str, list[dict[str, Any]]] = {}
        for ev in events_up_to_pin:
            sub = ev["subject_id"]
            if scope_type == "cohort":
                assigned = self.store.get_member_cohort(epoch, sub)
                if assigned != scope_id:
                    continue
            events_by_subject.setdefault(sub, []).append(ev)

        canonical: dict[str, tuple[int, str]] = {}
        for sub, ev_list in events_by_subject.items():
            ev_list.sort(key=lambda e: (e["applied_at"], e.get("stream_offset", 0), e.get("event_id", "")))
            total_score = sum(e["xp_amount"] for e in ev_list)
            if total_score > 0:
                running = 0
                first_reached = ev_list[0]["applied_at"]
                for e in ev_list:
                    running += e["xp_amount"]
                    if running >= total_score:
                        first_reached = e["applied_at"]
                        break
                canonical[sub] = (total_score, first_reached)

        return canonical

    def reconcile_scope_from_mapping_for_tests(
        self,
        canonical_state: Mapping[str, tuple[int, str]],
        scope_type: str,
        scope_id: str,
        epoch: str,
        pinned_offset: int | None = None,
        watermark_offset: int | None = None,
        audited_at: str | None = None,
    ) -> ReconciliationAudit:
        """Test helper allowing callers to supply an explicit canonical mapping."""
        effective_pin = pinned_offset if pinned_offset is not None else (watermark_offset or 0)
        return self._execute_reconciliation(
            canonical_state=canonical_state,
            scope_type=scope_type,
            scope_id=scope_id,
            epoch=epoch,
            pinned_offset=effective_pin,
            audited_at=audited_at,
        )

    def reconcile_scope(
        self,
        scope_type: str = "global",
        scope_id: str = "global",
        epoch: str = "",
        pinned_offset: int | None = None,
        audited_at: str | None = None,
        *,
        canonical_state: Mapping[str, tuple[int, str]] | None = None,
        watermark_offset: int | None = None,
    ) -> ReconciliationAudit:
        """Audits a leaderboard scope against canonical event stream up to pinned_offset."""
        if canonical_state is not None:
            # Caller-supplied mapping path (kept as test helper compatibility)
            effective_pin = pinned_offset if pinned_offset is not None else (watermark_offset or 0)
            return self._execute_reconciliation(
                canonical_state=canonical_state,
                scope_type=scope_type,
                scope_id=scope_id,
                epoch=epoch,
                pinned_offset=effective_pin,
                audited_at=audited_at,
            )

        # 1. Determine pinned offset if not explicitly provided
        if pinned_offset is None:
            applied_events = self.store.get_applied_events(epoch=epoch)
            max_stream = max((e.get("stream_offset", 0) for e in applied_events), default=0)
            committed = max(
                self.store.get_committed_offset(scope_id),
                self.store.get_committed_offset("global"),
            )
            pinned_offset = max(max_stream, committed)

        # 2. Rebuild canonical state from event stream up to pinned_offset
        canonical = self.build_canonical_state(
            scope_type=scope_type,
            scope_id=scope_id,
            epoch=epoch,
            pinned_offset=pinned_offset,
        )

        # 3. Execute differential reconciliation
        return self._execute_reconciliation(
            canonical_state=canonical,
            scope_type=scope_type,
            scope_id=scope_id,
            epoch=epoch,
            pinned_offset=pinned_offset,
            audited_at=audited_at,
        )

    def _execute_reconciliation(
        self,
        canonical_state: Mapping[str, tuple[int, str]],
        scope_type: str,
        scope_id: str,
        epoch: str,
        pinned_offset: int,
        audited_at: str | None = None,
    ) -> ReconciliationAudit:
        start_time = time.perf_counter()
        now_ts = audited_at or datetime.now(timezone.utc).isoformat()

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
                # Missing from read model -> differential insert respecting the pin
                self.store.upsert_score(
                    scope_type=scope_type,
                    scope_id=scope_id,
                    epoch=epoch,
                    subject_id=subject_id,
                    score=true_score,
                    first_reached_at=true_first_reached,
                    updated_at=now_ts,
                    watermark_offset=pinned_offset,
                )
                inserted_count += 1
                corrected_subjects.append(subject_id)
            elif proj.score != true_score or proj.first_reached_at != true_first_reached:
                # Score or tie timestamp diverged
                # Leave rows newer than pinned offset alone!
                if pinned_offset == 0 or proj.watermark_offset <= pinned_offset:
                    self.store.upsert_score(
                        scope_type=scope_type,
                        scope_id=scope_id,
                        epoch=epoch,
                        subject_id=subject_id,
                        score=true_score,
                        first_reached_at=true_first_reached,
                        updated_at=now_ts,
                        watermark_offset=pinned_offset,
                    )
                    updated_count += 1
                    corrected_subjects.append(subject_id)

        # 3. Check for orphan entries in projection that do not exist in canonical truth
        orphans = projected_subjects - canonical_subjects
        for orphan_id in orphans:
            proj = projected_entries[orphan_id]
            # Leave rows newer than pinned offset alone!
            if pinned_offset == 0 or proj.watermark_offset <= pinned_offset:
                self.store.remove_member(scope_type, scope_id, epoch, orphan_id)
                deleted_count += 1
                corrected_subjects.append(orphan_id)

        duration_ms = (time.perf_counter() - start_time) * 1000.0
        divergence_count = inserted_count + updated_count + deleted_count

        audit = ReconciliationAudit(
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
            audit_id=f"audit_{time.time_ns()}_{next(_audit_counter)}_{uuid.uuid4().hex[:8]}_{scope_type}_{scope_id}",
            pinned_offset=pinned_offset,
        )
        self.store.record_reconciliation_audit(audit)
        return audit
