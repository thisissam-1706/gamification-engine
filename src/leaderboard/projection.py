"""Leaderboard projections consuming aggregate domain events."""

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from src.leaderboard.cohorts import get_calendar_week
from src.leaderboard.store import LeaderboardStore
from src.state_machine.learner import Learner


def normalize_to_utc_iso(ts: str | datetime | None) -> str:
    """Normalizes any ISO timestamp or datetime to standard UTC ISO string (ending in Z)."""
    if ts is None:
        raise ValueError("Timestamp cannot be None; guessing with datetime.now is prohibited")
    elif isinstance(ts, datetime):
        dt = ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)
    elif isinstance(ts, str):
        clean_ts = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    else:
        raise TypeError(f"Invalid timestamp type: {type(ts)}")
    utc_dt = dt.astimezone(timezone.utc)
    # Format with Z for clean deterministic string comparison
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class MissingTimestampError(ValueError):
    """Raised when an XPAwarded event cannot be timed because occurred_at is missing."""

    def __init__(
        self,
        message: str,
        event_id: str | None = None,
        subject_id: str | None = None,
        completion_id: str | None = None,
        status: str = "pending_time",
    ) -> None:
        super().__init__(message)
        self.event_id = event_id
        self.subject_id = subject_id
        self.completion_id = completion_id
        self.status = status


class LeaderboardProjection:
    """Projects XPAwarded and CohortAssigned facts into global and cohort leaderboards.

    Guarantees:
    - Idempotency across duplicate deliveries using unique event tracking.
    - Full crash recovery and state rehydration on restart from persisted SQLite state.
    - Atomic single-transaction commits across global and cohort scopes.
    - True chronological first_reached_at calculation for late and out-of-order events.
    - Strict UTC normalization for deterministic tie-breaking.
    """

    def __init__(self, store: LeaderboardStore) -> None:
        self.store = store
        # Tracks (subject_id, epoch) -> total_score
        self._epoch_scores: dict[tuple[str, str], int] = defaultdict(int)
        # Tracks (subject_id, epoch) -> timestamp when current score was first reached
        self._first_reached: dict[tuple[str, str], str] = {}
        # Tracks (subject_id, epoch) -> list of (occurred_at_utc, xp_amount, event_id)
        self._subject_events: dict[tuple[str, str], list[tuple[str, int, str]]] = defaultdict(list)
        # Tracks (subject_id, epoch) -> cohort_id
        self._cohort_memberships: dict[tuple[str, str], str] = {}
        # Tracks completion_id -> occurred_at timestamp or (completion_id, subject_id) -> timestamp
        self._completion_occurred_at: dict[tuple[str, str] | str, str] = {}
        self._pending_awards: list[dict[str, Any]] = []
        self._watermark = self.store.get_committed_offset("global")

    def rehydrate(self, epoch: str | None = None) -> None:
        """Rehydrates projection state from SQLite store into memory."""
        if epoch is None:
            epochs_to_load = self.store.get_all_epochs()
        else:
            epochs_to_load = [epoch]

        for ep in epochs_to_load:
            # Rehydrate global entries
            for entry in self.store.get_epoch_entries("global", ep):
                key = (entry.subject_id, ep)
                self._epoch_scores[key] = entry.score
                self._first_reached[key] = entry.first_reached_at

            # Rehydrate cohort memberships
            for entry in self.store.get_epoch_entries("cohort", ep):
                key = (entry.subject_id, ep)
                self._cohort_memberships[key] = entry.scope_id

            # Rehydrate per-subject applied event facts (M3-1)
            for ev in self.store.get_applied_events(epoch=ep):
                ev_key = (ev["subject_id"], ev["epoch"])
                self._subject_events[ev_key].append((ev["applied_at"], ev["xp_amount"], ev["event_id"]))

        self._watermark = self.store.get_committed_offset("global")

    def apply(
        self,
        domain_event: Any,
        offset: int | None = None,
        raise_on_missing_time: bool = False,
        *,
        watermark_offset: int | None = None,
    ) -> str:
        """Applies a domain event to the read model.

        Requires each event's own global notification-log position (`offset`).
        Returns status string ('applied', 'duplicate', 'pending_time', or 'ignored'),
        or raises MissingTimestampError if raise_on_missing_time is True and no timestamp can be found.
        """
        real_offset = offset if offset is not None else watermark_offset
        if real_offset is None:
            raise TypeError(
                "apply() missing required argument: 'offset' (global notification-log position)"
            )

        # Duplicate delivery of an already-processed offset is a no-op that does not move checkpoint backwards
        if self.store.is_offset_processed(real_offset):
            return "duplicate"

        if isinstance(domain_event, Learner.LessonCompleted):
            occ = getattr(domain_event, "occurred_at", None)
            subject_id = str(domain_event.originator_id)
            completion_id = str(domain_event.completion_id)
            with self.store.transaction():
                ts = ""
                if occ:
                    ts = normalize_to_utc_iso(occ)
                    self._completion_occurred_at[(completion_id, subject_id)] = ts
                    self._completion_occurred_at[completion_id] = ts
                    self.store.record_completion_time(completion_id, subject_id, ts)

                    # Resolve any pending awards waiting for this completion's timestamp
                    for award in self.store.get_pending_awards(subject_id=subject_id):
                        if award.get("completion_id") == completion_id and award.get("status") == "pending_time":
                            aw_epoch = get_calendar_week(ts)
                            self.record_xp(
                                subject_id=subject_id,
                                xp_amount=award["xp_amount"],
                                occurred_at=ts,
                                epoch=aw_epoch,
                                watermark_offset=award.get("watermark_offset", real_offset),
                                event_id=award["event_id"],
                            )
                            self.store.delete_pending_award(award["event_id"], subject_id)

                self.store.record_processed_offset(real_offset, occurred_at=ts)
                self._watermark = self.store.get_committed_offset("global")
            return "applied"

        elif isinstance(domain_event, Learner.CohortAssigned):
            subject_id = str(domain_event.originator_id)
            cohort_id = domain_event.cohort_id
            epoch = str(domain_event.membership_epoch)
            assigned_at = normalize_to_utc_iso(domain_event.assigned_at)
            with self.store.transaction():
                self.assign_cohort(subject_id, cohort_id, epoch, assigned_at, real_offset)
                self.store.record_processed_offset(
                    real_offset, cohort_id=cohort_id, occurred_at=assigned_at
                )
                self._watermark = self.store.get_committed_offset("global")
            return "applied"

        elif isinstance(domain_event, Learner.XPAwarded):
            subject_id = str(domain_event.originator_id)
            xp_amount = domain_event.xp_amount
            completion_id = getattr(domain_event, "completion_id", None)
            raw_occ = getattr(domain_event, "occurred_at", None)
            if not raw_occ and completion_id:
                raw_occ = (
                    self._completion_occurred_at.get((completion_id, subject_id))
                    or self._completion_occurred_at.get(completion_id)
                    or self.store.get_completion_time(completion_id, learner_id=subject_id)
                )

            # Unique event tracking identifier (M3-4, Bug 1 fix: must include learner_id)
            reward_id = getattr(domain_event, "reward_id", None)
            if reward_id:
                if reward_id.startswith(f"{subject_id}:"):
                    event_id = reward_id
                else:
                    event_id = f"{subject_id}:{reward_id}"
            else:
                event_id = f"{subject_id}:{real_offset}"

            # If no timestamp can be found, park as pending_time
            if not raw_occ:
                with self.store.transaction():
                    self.store.record_pending_award(
                        event_id=event_id,
                        subject_id=subject_id,
                        completion_id=completion_id or "",
                        xp_amount=xp_amount,
                        status="pending_time",
                        watermark_offset=real_offset,
                    )
                    self._pending_awards.append(
                        {
                            "event_id": event_id,
                            "subject_id": subject_id,
                            "completion_id": completion_id or "",
                            "xp_amount": xp_amount,
                            "status": "pending_time",
                            "watermark_offset": real_offset,
                        }
                    )
                    self.store.record_processed_offset(real_offset, occurred_at="")
                    self._watermark = self.store.get_committed_offset("global")

                if raise_on_missing_time:
                    raise MissingTimestampError(
                        f"Missing occurred_at for XPAwarded event '{event_id}' (completion_id='{completion_id}'). "
                        "Award parked as 'pending_time'; guessing with datetime.now is prohibited.",
                        event_id=event_id,
                        subject_id=subject_id,
                        completion_id=completion_id,
                        status="pending_time",
                    )
                return "pending_time"

            occurred_at = normalize_to_utc_iso(raw_occ)
            epoch = get_calendar_week(occurred_at)

            with self.store.transaction():
                applied = self.record_xp(
                    subject_id=subject_id,
                    xp_amount=xp_amount,
                    occurred_at=occurred_at,
                    epoch=epoch,
                    watermark_offset=real_offset,
                    event_id=event_id,
                )
                cohort_id = self._cohort_memberships.get((subject_id, epoch))
                if not cohort_id:
                    cohort_id = self.store.get_member_cohort(epoch, subject_id)

                if not applied:
                    self.store.record_processed_offset(
                        real_offset, cohort_id=cohort_id, occurred_at=occurred_at
                    )
                self._watermark = self.store.get_committed_offset("global")

            return "applied" if applied else "duplicate"

        else:
            with self.store.transaction():
                self.store.record_processed_offset(real_offset, occurred_at="")
                self._watermark = self.store.get_committed_offset("global")
            return "ignored"

    def record_xp(
        self,
        subject_id: str,
        xp_amount: int,
        occurred_at: str,
        epoch: str | None = None,
        watermark_offset: int = 0,
        event_id: str | None = None,
    ) -> bool:
        """Records an XP award atomically across scopes and rejects duplicates."""
        occurred_at = normalize_to_utc_iso(occurred_at)
        epoch = epoch or get_calendar_week(occurred_at)
        key = (subject_id, epoch)

        # Derive event ID if not explicitly provided
        event_id = event_id or f"xp_event:{subject_id}:{epoch}:{occurred_at}:{xp_amount}"

        # Fast in-memory deduplication check (optimisation; store is ultimate authority)
        if self.store.is_event_applied(event_id, subject_id=subject_id):
            return False

        # Keyed cohort membership lookup (M3-5)
        cohort_id = self._cohort_memberships.get(key)
        if not cohort_id:
            cohort_id = self.store.get_member_cohort(epoch, subject_id)
            if cohort_id:
                self._cohort_memberships[key] = cohort_id

        # Atomic commit boundary across global and cohort scopes (M3-2, M3-3)
        # Delegate score increment and first_reached_at derivation directly to the store
        applied = self.store.apply_award_atomic(
            event_id=event_id,
            subject_id=subject_id,
            epoch=epoch,
            xp_amount=xp_amount,
            occurred_at=occurred_at,
            cohort_id=cohort_id,
            watermark_offset=watermark_offset,
        )

        if not applied:
            # Atomic commit rejected as duplicate -> leave memory untouched!
            return False

        # Treat in-memory structures strictly as a read cache refreshed from the store
        global_entry = self.store.get_entry("global", "global", epoch, subject_id)
        if global_entry is not None:
            self._epoch_scores[key] = global_entry.score
            self._first_reached[key] = global_entry.first_reached_at

        events = self.store.get_applied_events(subject_id=subject_id, epoch=epoch)
        self._subject_events[key] = [
            (ev["applied_at"], ev["xp_amount"], ev["event_id"]) for ev in events
        ]
        self._watermark = self.store.get_committed_offset("global")
        return True

    def assign_cohort(
        self,
        subject_id: str,
        cohort_id: str,
        epoch: str,
        assigned_at: str,
        watermark_offset: int = 0,
    ) -> None:
        """Assigns a subject to a cohort and projects any accumulated epoch XP into the cohort leaderboard.

        Cleans up obsolete cohort leaderboard rows if reassigned.
        Runs remove_member and upsert_score in a single atomic transaction.
        """
        assigned_at = normalize_to_utc_iso(assigned_at)
        key = (subject_id, epoch)
        old_cohort = self._cohort_memberships.get(key)
        if not old_cohort:
            old_cohort = self.store.get_member_cohort(epoch, subject_id)

        # Read global score and first_reached from store (not process memory)
        global_entry = self.store.get_entry("global", "global", epoch, subject_id)
        current_score = global_entry.score if global_entry is not None else 0
        first_reached = (
            global_entry.first_reached_at if global_entry is not None else assigned_at
        )

        with self.store.transaction():
            # If reassigned to a different cohort, clean up obsolete row
            if old_cohort and old_cohort != cohort_id:
                self.store.remove_member(
                    scope_type="cohort",
                    scope_id=old_cohort,
                    epoch=epoch,
                    subject_id=subject_id,
                )

            self.store.upsert_score(
                scope_type="cohort",
                scope_id=cohort_id,
                epoch=epoch,
                subject_id=subject_id,
                score=current_score,
                first_reached_at=first_reached,
                updated_at=assigned_at,
                watermark_offset=watermark_offset,
            )

        # Refresh in-memory read cache
        self._cohort_memberships[key] = cohort_id
        self._epoch_scores[key] = current_score
        self._first_reached[key] = first_reached
        self._watermark = self.store.get_committed_offset("global")

    def clear(self) -> None:
        """Clears in-memory projection tracking and storage."""
        self._epoch_scores.clear()
        self._first_reached.clear()
        self._subject_events.clear()
        self._cohort_memberships.clear()
        self._completion_occurred_at.clear()
        self._pending_awards.clear()
        self._watermark = 0
        self.store.clear()

    def rebuild(self, domain_events: Iterable[Any]) -> None:
        """Deterministically clears and replays stored domain events with real positions."""
        self.clear()
        for idx, item in enumerate(domain_events):
            if isinstance(item, (tuple, list)) and len(item) == 2 and isinstance(item[0], int):
                pos, event = item
            elif hasattr(item, "id") and isinstance(item.id, int) and hasattr(item, "topic"):
                pos = item.id
                event = item
            elif hasattr(item, "notification_id") and isinstance(item.notification_id, int):
                pos = item.notification_id
                event = item
            else:
                pos = idx + 1
                event = item
            self.apply(event, offset=pos)
