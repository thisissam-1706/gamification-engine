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

        self._watermark = max(self._watermark, self.store.get_committed_offset("global"))

    def apply(
        self,
        domain_event: Any,
        watermark_offset: int | None = None,
        raise_on_missing_time: bool = False,
    ) -> str:
        """Applies a domain event to the read model.

        Returns status string ('applied', 'duplicate', 'pending_time', or 'ignored'),
        or raises MissingTimestampError if raise_on_missing_time is True and no timestamp can be found.
        """
        if watermark_offset is not None:
            self._watermark = max(self._watermark, watermark_offset)
        elif hasattr(domain_event, "originator_version"):
            self._watermark = max(self._watermark, domain_event.originator_version)

        if isinstance(domain_event, Learner.LessonCompleted):
            occ = getattr(domain_event, "occurred_at", None)
            subject_id = str(domain_event.originator_id)
            completion_id = str(domain_event.completion_id)
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
                            watermark_offset=award.get("watermark_offset", self._watermark),
                            event_id=award["event_id"],
                        )
                        self.store.delete_pending_award(award["event_id"], subject_id)
            return "applied"

        elif isinstance(domain_event, Learner.CohortAssigned):
            subject_id = str(domain_event.originator_id)
            cohort_id = domain_event.cohort_id
            epoch = str(domain_event.membership_epoch)
            assigned_at = normalize_to_utc_iso(domain_event.assigned_at)
            self.assign_cohort(subject_id, cohort_id, epoch, assigned_at, self._watermark)
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
                event_id = f"{subject_id}:{domain_event.originator_version}"

            # Bug 2 fix: if no timestamp can be found, park as pending_time
            if not raw_occ:
                self.store.record_pending_award(
                    event_id=event_id,
                    subject_id=subject_id,
                    completion_id=completion_id or "",
                    xp_amount=xp_amount,
                    status="pending_time",
                    watermark_offset=self._watermark,
                )
                self._pending_awards.append(
                    {
                        "event_id": event_id,
                        "subject_id": subject_id,
                        "completion_id": completion_id or "",
                        "xp_amount": xp_amount,
                        "status": "pending_time",
                        "watermark_offset": self._watermark,
                    }
                )
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

            applied = self.record_xp(
                subject_id=subject_id,
                xp_amount=xp_amount,
                occurred_at=occurred_at,
                epoch=epoch,
                watermark_offset=self._watermark,
                event_id=event_id,
            )
            return "applied" if applied else "duplicate"

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

        # Lazily load past events from store if not in memory (M3-1)
        if key not in self._subject_events:
            for ev in self.store.get_applied_events(subject_id=subject_id, epoch=epoch):
                self._subject_events[key].append((ev["applied_at"], ev["xp_amount"], ev["event_id"]))

        # Lazily load score and first_reached from store if not in memory
        if key not in self._epoch_scores:
            existing_entry = self.store.get_entry("global", "global", epoch, subject_id)
            if existing_entry is not None:
                self._epoch_scores[key] = existing_entry.score
                self._first_reached[key] = existing_entry.first_reached_at

        # Derive event ID if not explicitly provided
        event_id = event_id or f"xp_event:{subject_id}:{epoch}:{occurred_at}:{xp_amount}"

        # Fast in-memory deduplication check (optimisation; store is ultimate authority)
        if self.store.is_event_applied(event_id, subject_id=subject_id):
            return False

        old_score = self._epoch_scores.get(key, 0)
        candidate_score = old_score + xp_amount

        # Chronological first_reached_at computation on local candidate structures (M3-2)
        candidate_events = list(self._subject_events.get(key, [])) + [(occurred_at, xp_amount, event_id)]
        sorted_events = sorted(candidate_events, key=lambda item: item[0])

        running_sum = 0
        candidate_first_reached = occurred_at
        for ev_ts, ev_xp, _ in sorted_events:
            running_sum += ev_xp
            if running_sum >= candidate_score:
                candidate_first_reached = ev_ts
                break

        # Keyed cohort membership lookup (M3-5)
        cohort_id = self._cohort_memberships.get(key)
        if not cohort_id:
            cohort_id = self.store.get_member_cohort(epoch, subject_id)
            if cohort_id:
                self._cohort_memberships[key] = cohort_id

        # Atomic commit boundary across global and cohort scopes (M3-2, M3-3)
        applied = self.store.apply_award_atomic(
            event_id=event_id,
            subject_id=subject_id,
            epoch=epoch,
            xp_amount=xp_amount,
            occurred_at=occurred_at,
            global_score=candidate_score,
            global_first_reached_at=candidate_first_reached,
            cohort_id=cohort_id,
            cohort_score=candidate_score if cohort_id else None,
            cohort_first_reached_at=candidate_first_reached if cohort_id else None,
            watermark_offset=watermark_offset,
        )

        if not applied:
            # Atomic commit rejected as duplicate -> leave memory untouched!
            return False

        # Mutate in-memory structures only after successful persistence!
        self._subject_events[key] = candidate_events
        self._epoch_scores[key] = candidate_score
        self._first_reached[key] = candidate_first_reached
        self._watermark = max(self._watermark, watermark_offset)
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
        """
        assigned_at = normalize_to_utc_iso(assigned_at)
        key = (subject_id, epoch)
        old_cohort = self._cohort_memberships.get(key)

        # If reassigned to a different cohort, clean up obsolete row
        if old_cohort and old_cohort != cohort_id:
            self.store.remove_member(
                scope_type="cohort",
                scope_id=old_cohort,
                epoch=epoch,
                subject_id=subject_id,
            )

        self._cohort_memberships[key] = cohort_id
        self._watermark = max(self._watermark, watermark_offset)

        # M3-6: Load global score from store if not in memory to avoid projecting 0
        if key not in self._epoch_scores:
            existing_entry = self.store.get_entry("global", "global", epoch, subject_id)
            if existing_entry is not None:
                self._epoch_scores[key] = existing_entry.score
                self._first_reached[key] = existing_entry.first_reached_at

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
        self._subject_events.clear()
        self._cohort_memberships.clear()
        self._completion_occurred_at.clear()
        self._pending_awards.clear()
        self._watermark = 0
        self.store.clear()

    def rebuild(self, domain_events: Iterable[Any]) -> None:
        """Deterministically clears and replays stored domain events."""
        self.clear()
        for idx, event in enumerate(domain_events):
            self.apply(event, watermark_offset=idx + 1)
