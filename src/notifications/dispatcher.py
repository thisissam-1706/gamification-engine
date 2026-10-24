"""Notification dispatcher coordinating guardrail checks, channel adapters, and terminal facts."""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
import uuid

from src.notifications.channels.base import BaseChannelAdapter
from src.notifications.channels.in_app import InAppChannelAdapter
from src.notifications.channels.push import SimulatedPushAdapter
from src.notifications.guardrails import (
    check_consent,
    check_fatigue_cap,
    evaluate_quiet_hours,
    is_ttl_expired,
    normalize_iso_to_utc_dt,
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


class NotificationDispatcher:
    """Orchestrates candidate proposals through consent, fatigue cap, quiet hours, delivery, and DLQ."""

    def __init__(
        self,
        store: NotificationStore,
        adapters: dict[ChannelType, BaseChannelAdapter] | None = None,
        retry_scheduler: RetryScheduler | None = None,
        fatigue_cap: int = 5,
        quiet_start_hour: int = 22,
        quiet_end_hour: int = 7,
    ) -> None:
        self.store = store
        self.adapters = adapters or {
            ChannelType.IN_APP: InAppChannelAdapter(),
            ChannelType.PUSH: SimulatedPushAdapter(),
        }
        self.retry_scheduler = retry_scheduler or RetryScheduler(max_retries=3)
        self.fatigue_cap = fatigue_cap
        self.quiet_start_hour = quiet_start_hour
        self.quiet_end_hour = quiet_end_hour

    def dispatch(
        self,
        proposal: NotificationProposal,
        prefs: LearnerPreferences | None = None,
        now_utc: datetime | str | None = None,
    ) -> TerminalFact:
        """Processes a single proposal through all guardrails, attempting dispatch or routing to DLQ."""
        if now_utc is None:
            now_dt = datetime.now(timezone.utc)
        else:
            now_dt = normalize_iso_to_utc_dt(now_utc)
        now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        # 1. Deduplication check (AT-9)
        if self.store.is_proposal_seen(proposal.proposal_id):
            return TerminalFact(
                event_id=f"supp_dup_{proposal.proposal_id}",
                event_type="notification_suppressed",
                proposal_id=proposal.proposal_id,
                learner_id=proposal.learner_id,
                channel=proposal.channel.value,
                category=proposal.category.value,
                timestamp=now_iso,
                status=NotificationStatus.SUPPRESSED,
                reason="duplicate_proposal",
            )

        # 2. Preferences requirement (M5-2)
        if prefs is None:
            suppression_id = f"supp_{uuid.uuid4().hex[:12]}"
            self.store.record_suppression(
                suppression_id,
                proposal.proposal_id,
                proposal.learner_id,
                proposal.channel,
                proposal.category,
                now_iso,
                "missing_preferences",
            )
            return TerminalFact(
                event_id=suppression_id,
                event_type="notification_suppressed",
                proposal_id=proposal.proposal_id,
                learner_id=proposal.learner_id,
                channel=proposal.channel.value,
                category=proposal.category.value,
                timestamp=now_iso,
                status=NotificationStatus.SUPPRESSED,
                reason="missing_preferences",
            )

        # 3. Consent / Opt-Out enforcement (AT-11)
        consent_ok, consent_reason = check_consent(proposal, prefs)
        if not consent_ok:
            suppression_id = f"supp_{uuid.uuid4().hex[:12]}"
            self.store.record_suppression(
                suppression_id,
                proposal.proposal_id,
                proposal.learner_id,
                proposal.channel,
                proposal.category,
                now_iso,
                consent_reason or "opt_out",
            )
            return TerminalFact(
                event_id=suppression_id,
                event_type="notification_suppressed",
                proposal_id=proposal.proposal_id,
                learner_id=proposal.learner_id,
                channel=proposal.channel.value,
                category=proposal.category.value,
                timestamp=now_iso,
                status=NotificationStatus.SUPPRESSED,
                reason=consent_reason or "opt_out",
            )

        # 4. Immediate TTL Expiry check (M5-4)
        created_ts = proposal.created_at or now_iso
        if is_ttl_expired(created_ts, proposal.ttl_seconds, now_dt):
            suppression_id = f"supp_{uuid.uuid4().hex[:12]}"
            self.store.record_suppression(
                suppression_id,
                proposal.proposal_id,
                proposal.learner_id,
                proposal.channel,
                proposal.category,
                now_iso,
                "ttl_expired",
            )
            return TerminalFact(
                event_id=suppression_id,
                event_type="notification_suppressed",
                proposal_id=proposal.proposal_id,
                learner_id=proposal.learner_id,
                channel=proposal.channel.value,
                category=proposal.category.value,
                timestamp=now_iso,
                status=NotificationStatus.SUPPRESSED,
                reason="ttl_expired",
            )

        # 5. Notification Fatigue Guardrail across all channels (F2, AT-14)
        cutoff_24h = (now_dt - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        recent_dispatches = self.store.get_dispatch_history_since(
            proposal.learner_id,
            since_iso=cutoff_24h,
        )
        fatigue_ok, fatigue_reason = check_fatigue_cap(
            recent_dispatches=recent_dispatches,
            now_utc=now_dt,
            max_cap=self.fatigue_cap,
            window_hours=24,
        )
        if not fatigue_ok:
            suppression_id = f"supp_{uuid.uuid4().hex[:12]}"
            self.store.record_suppression(
                suppression_id,
                proposal.proposal_id,
                proposal.learner_id,
                proposal.channel,
                proposal.category,
                now_iso,
                fatigue_reason or "daily_cap_exceeded",
            )
            return TerminalFact(
                event_id=suppression_id,
                event_type="notification_suppressed",
                proposal_id=proposal.proposal_id,
                learner_id=proposal.learner_id,
                channel=proposal.channel.value,
                category=proposal.category.value,
                timestamp=now_iso,
                status=NotificationStatus.SUPPRESSED,
                reason=fatigue_reason or "daily_cap_exceeded",
            )

        # 6. Quiet Hours Delay & TTL check (AT-8, M5-3)
        tz_str = prefs.timezone or "UTC"
        try:
            in_quiet, next_dispatch_dt = evaluate_quiet_hours(
                now_dt,
                tz_str,
                quiet_start_hour=self.quiet_start_hour,
                quiet_end_hour=self.quiet_end_hour,
            )
        except ValueError:
            suppression_id = f"supp_{uuid.uuid4().hex[:12]}"
            self.store.record_suppression(
                suppression_id,
                proposal.proposal_id,
                proposal.learner_id,
                proposal.channel,
                proposal.category,
                now_iso,
                "invalid_timezone",
            )
            return TerminalFact(
                event_id=suppression_id,
                event_type="notification_suppressed",
                proposal_id=proposal.proposal_id,
                learner_id=proposal.learner_id,
                channel=proposal.channel.value,
                category=proposal.category.value,
                timestamp=now_iso,
                status=NotificationStatus.SUPPRESSED,
                reason="invalid_timezone",
            )

        if in_quiet and next_dispatch_dt is not None:
            # Check if TTL would expire before next dispatch time
            if is_ttl_expired(created_ts, proposal.ttl_seconds, next_dispatch_dt):
                suppression_id = f"supp_{uuid.uuid4().hex[:12]}"
                self.store.record_suppression(
                    suppression_id,
                    proposal.proposal_id,
                    proposal.learner_id,
                    proposal.channel,
                    proposal.category,
                    now_iso,
                    "ttl_expired",
                )
                return TerminalFact(
                    event_id=suppression_id,
                    event_type="notification_suppressed",
                    proposal_id=proposal.proposal_id,
                    learner_id=proposal.learner_id,
                    channel=proposal.channel.value,
                    category=proposal.category.value,
                    timestamp=now_iso,
                    status=NotificationStatus.SUPPRESSED,
                    reason="ttl_expired",
                )

            # Reschedule candidate for morning release (M5-1)
            rescheduled_iso = next_dispatch_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            self.store.record_rescheduled(
                proposal.proposal_id,
                proposal.learner_id,
                proposal.channel,
                proposal.category,
                rescheduled_iso,
                created_ts,
                proposal.payload,
            )
            return TerminalFact(
                event_id=f"resched_{uuid.uuid4().hex[:12]}",
                event_type="notification_rescheduled",
                proposal_id=proposal.proposal_id,
                learner_id=proposal.learner_id,
                channel=proposal.channel.value,
                category=proposal.category.value,
                timestamp=now_iso,
                status=NotificationStatus.QUEUED,
                rescheduled_for=rescheduled_iso,
                reason="quiet_hours_rescheduled",
            )

        # 7. Delivery via Channel Adapter & Retry/DLQ (AT-10)
        self.store.register_proposal(proposal)

        adapter = self.adapters.get(proposal.channel)
        if adapter is None:
            suppression_id = f"supp_{uuid.uuid4().hex[:12]}"
            self.store.record_suppression(
                suppression_id,
                proposal.proposal_id,
                proposal.learner_id,
                proposal.channel,
                proposal.category,
                now_iso,
                "unsupported_channel",
            )
            return TerminalFact(
                event_id=suppression_id,
                event_type="notification_suppressed",
                proposal_id=proposal.proposal_id,
                learner_id=proposal.learner_id,
                channel=proposal.channel.value,
                category=proposal.category.value,
                timestamp=now_iso,
                status=NotificationStatus.SUPPRESSED,
                reason="unsupported_channel",
            )

        success, attempts, err = self.retry_scheduler.deliver_with_retry(
            adapter, proposal
        )

        if success:
            dispatch_id = f"disp_{uuid.uuid4().hex[:12]}"
            self.store.record_dispatch(
                dispatch_id,
                proposal.proposal_id,
                proposal.learner_id,
                proposal.channel,
                proposal.category,
                now_iso,
                proposal.payload,
            )
            return TerminalFact(
                event_id=dispatch_id,
                event_type="notification_dispatched",
                proposal_id=proposal.proposal_id,
                learner_id=proposal.learner_id,
                channel=proposal.channel.value,
                category=proposal.category.value,
                timestamp=now_iso,
                status=NotificationStatus.DISPATCHED,
                attempt_count=attempts,
            )
        else:
            dlq_id = f"dlq_{uuid.uuid4().hex[:12]}"
            self.store.record_dlq(
                dlq_id,
                proposal.proposal_id,
                proposal.learner_id,
                proposal.channel,
                proposal.category,
                now_iso,
                err or "provider_exhausted",
                attempts,
                proposal.payload,
            )
            return TerminalFact(
                event_id=dlq_id,
                event_type="notification_failed",
                proposal_id=proposal.proposal_id,
                learner_id=proposal.learner_id,
                channel=proposal.channel.value,
                category=proposal.category.value,
                timestamp=now_iso,
                status=NotificationStatus.FAILED,
                reason=err or "provider_exhausted",
                attempt_count=attempts,
            )

    def release_rescheduled(
        self,
        now_utc: datetime | str | None = None,
        prefs_provider: Callable[[str], LearnerPreferences | None] | dict[str, LearnerPreferences] | None = None,
    ) -> list[TerminalFact]:
        """Explicit caller-invoked release operation for due rescheduled notifications (F5).

        Rechecks consent, TTL, and fatigue cap before delivery.
        """
        if now_utc is None:
            now_dt = datetime.now(timezone.utc)
        else:
            now_dt = normalize_iso_to_utc_dt(now_utc)
        now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        due_items = self.store.get_due_rescheduled(now_iso)
        facts: list[TerminalFact] = []

        for item in due_items:
            proposal_id = item["proposal_id"]
            learner_id = item["learner_id"]
            channel = ChannelType(item["channel"])
            category = NotificationCategory(item["category"])
            created_at = item["created_at"]
            payload = item["payload"]

            # Mark released so it cannot be double-processed
            self.store.mark_rescheduled_released(proposal_id)

            # Reconstruct proposal
            proposal = NotificationProposal(
                proposal_id=proposal_id,
                learner_id=learner_id,
                channel=channel,
                category=category,
                payload=payload,
                created_at=created_at,
            )

            # Retrieve preferences
            prefs: LearnerPreferences | None = None
            if callable(prefs_provider):
                prefs = prefs_provider(learner_id)
            elif isinstance(prefs_provider, dict):
                prefs = prefs_provider.get(learner_id)

            if prefs is None:
                suppression_id = f"supp_{uuid.uuid4().hex[:12]}"
                self.store.record_suppression(
                    suppression_id,
                    proposal_id,
                    learner_id,
                    channel,
                    category,
                    now_iso,
                    "missing_preferences",
                )
                facts.append(
                    TerminalFact(
                        event_id=suppression_id,
                        event_type="notification_suppressed",
                        proposal_id=proposal_id,
                        learner_id=learner_id,
                        channel=channel.value,
                        category=category.value,
                        timestamp=now_iso,
                        status=NotificationStatus.SUPPRESSED,
                        reason="missing_preferences",
                    )
                )
                continue

            # 1. Recheck consent / opt-out
            consent_ok, consent_reason = check_consent(proposal, prefs)
            if not consent_ok:
                suppression_id = f"supp_{uuid.uuid4().hex[:12]}"
                self.store.record_suppression(
                    suppression_id,
                    proposal_id,
                    learner_id,
                    channel,
                    category,
                    now_iso,
                    consent_reason or "opt_out",
                )
                facts.append(
                    TerminalFact(
                        event_id=suppression_id,
                        event_type="notification_suppressed",
                        proposal_id=proposal_id,
                        learner_id=learner_id,
                        channel=channel.value,
                        category=category.value,
                        timestamp=now_iso,
                        status=NotificationStatus.SUPPRESSED,
                        reason=consent_reason or "opt_out",
                    )
                )
                continue

            # 2. Recheck TTL at release instant
            if is_ttl_expired(created_at, proposal.ttl_seconds, now_dt):
                suppression_id = f"supp_{uuid.uuid4().hex[:12]}"
                self.store.record_suppression(
                    suppression_id,
                    proposal_id,
                    learner_id,
                    channel,
                    category,
                    now_iso,
                    "ttl_expired",
                )
                facts.append(
                    TerminalFact(
                        event_id=suppression_id,
                        event_type="notification_suppressed",
                        proposal_id=proposal_id,
                        learner_id=learner_id,
                        channel=channel.value,
                        category=category.value,
                        timestamp=now_iso,
                        status=NotificationStatus.SUPPRESSED,
                        reason="ttl_expired",
                    )
                )
                continue

            # 3. Recheck fatigue cap across all channels
            cutoff_24h = (now_dt - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
            recent_dispatches = self.store.get_dispatch_history_since(
                learner_id,
                since_iso=cutoff_24h,
            )
            fatigue_ok, fatigue_reason = check_fatigue_cap(
                recent_dispatches=recent_dispatches,
                now_utc=now_dt,
                max_cap=self.fatigue_cap,
                window_hours=24,
            )
            if not fatigue_ok:
                suppression_id = f"supp_{uuid.uuid4().hex[:12]}"
                self.store.record_suppression(
                    suppression_id,
                    proposal_id,
                    learner_id,
                    channel,
                    category,
                    now_iso,
                    fatigue_reason or "daily_cap_exceeded",
                )
                facts.append(
                    TerminalFact(
                        event_id=suppression_id,
                        event_type="notification_suppressed",
                        proposal_id=proposal_id,
                        learner_id=learner_id,
                        channel=channel.value,
                        category=category.value,
                        timestamp=now_iso,
                        status=NotificationStatus.SUPPRESSED,
                        reason=fatigue_reason or "daily_cap_exceeded",
                    )
                )
                continue

            # 4. Attempt delivery
            self.store.register_proposal(proposal)
            adapter = self.adapters.get(channel)
            if adapter is None:
                suppression_id = f"supp_{uuid.uuid4().hex[:12]}"
                self.store.record_suppression(
                    suppression_id,
                    proposal_id,
                    learner_id,
                    channel,
                    category,
                    now_iso,
                    "unsupported_channel",
                )
                facts.append(
                    TerminalFact(
                        event_id=suppression_id,
                        event_type="notification_suppressed",
                        proposal_id=proposal_id,
                        learner_id=learner_id,
                        channel=channel.value,
                        category=category.value,
                        timestamp=now_iso,
                        status=NotificationStatus.SUPPRESSED,
                        reason="unsupported_channel",
                    )
                )
                continue

            success, attempts, err = self.retry_scheduler.deliver_with_retry(adapter, proposal)
            if success:
                dispatch_id = f"disp_{uuid.uuid4().hex[:12]}"
                self.store.record_dispatch(
                    dispatch_id,
                    proposal_id,
                    learner_id,
                    channel,
                    category,
                    now_iso,
                    payload,
                )
                facts.append(
                    TerminalFact(
                        event_id=dispatch_id,
                        event_type="notification_dispatched",
                        proposal_id=proposal_id,
                        learner_id=learner_id,
                        channel=channel.value,
                        category=category.value,
                        timestamp=now_iso,
                        status=NotificationStatus.DISPATCHED,
                        attempt_count=attempts,
                    )
                )
            else:
                dlq_id = f"dlq_{uuid.uuid4().hex[:12]}"
                self.store.record_dlq(
                    dlq_id,
                    proposal_id,
                    learner_id,
                    channel,
                    category,
                    now_iso,
                    err or "provider_exhausted",
                    attempts,
                    payload,
                )
                facts.append(
                    TerminalFact(
                        event_id=dlq_id,
                        event_type="notification_failed",
                        proposal_id=proposal_id,
                        learner_id=learner_id,
                        channel=channel.value,
                        category=category.value,
                        timestamp=now_iso,
                        status=NotificationStatus.FAILED,
                        reason=err or "provider_exhausted",
                        attempt_count=attempts,
                    )
                )

        return facts

    def replay_dlq(
        self,
        dlq_id: str,
        prefs: LearnerPreferences | None = None,
        now_utc: datetime | str | None = None,
    ) -> TerminalFact:
        """Replays a failed notification from DLQ preserving the original proposal identity (F3).

        On successful delivery, marks the DLQ entry as resolved.
        """
        if now_utc is None:
            now_dt = datetime.now(timezone.utc)
        else:
            now_dt = normalize_iso_to_utc_dt(now_utc)
        now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        entry = self.store.get_dlq_entry(dlq_id)
        if not entry:
            raise ValueError(f"DLQ entry '{dlq_id}' not found.")

        proposal = NotificationProposal(
            proposal_id=entry["proposal_id"],
            learner_id=entry["learner_id"],
            channel=ChannelType(entry["channel"]),
            category=NotificationCategory(entry["category"]),
            payload=entry["payload"],
            created_at=entry["failed_at"],
        )

        # 1. Preferences requirement
        if prefs is None:
            suppression_id = f"supp_{uuid.uuid4().hex[:12]}"
            self.store.record_suppression(
                suppression_id,
                proposal.proposal_id,
                proposal.learner_id,
                proposal.channel,
                proposal.category,
                now_iso,
                "missing_preferences",
            )
            return TerminalFact(
                event_id=suppression_id,
                event_type="notification_suppressed",
                proposal_id=proposal.proposal_id,
                learner_id=proposal.learner_id,
                channel=proposal.channel.value,
                category=proposal.category.value,
                timestamp=now_iso,
                status=NotificationStatus.SUPPRESSED,
                reason="missing_preferences",
            )

        # 2. Check consent
        consent_ok, consent_reason = check_consent(proposal, prefs)
        if not consent_ok:
            suppression_id = f"supp_{uuid.uuid4().hex[:12]}"
            self.store.record_suppression(
                suppression_id,
                proposal.proposal_id,
                proposal.learner_id,
                proposal.channel,
                proposal.category,
                now_iso,
                consent_reason or "opt_out",
            )
            return TerminalFact(
                event_id=suppression_id,
                event_type="notification_suppressed",
                proposal_id=proposal.proposal_id,
                learner_id=proposal.learner_id,
                channel=proposal.channel.value,
                category=proposal.category.value,
                timestamp=now_iso,
                status=NotificationStatus.SUPPRESSED,
                reason=consent_reason or "opt_out",
            )

        # 3. Check fatigue cap
        cutoff_24h = (now_dt - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        recent_dispatches = self.store.get_dispatch_history_since(
            proposal.learner_id,
            since_iso=cutoff_24h,
        )
        fatigue_ok, fatigue_reason = check_fatigue_cap(
            recent_dispatches=recent_dispatches,
            now_utc=now_dt,
            max_cap=self.fatigue_cap,
            window_hours=24,
        )
        if not fatigue_ok:
            suppression_id = f"supp_{uuid.uuid4().hex[:12]}"
            self.store.record_suppression(
                suppression_id,
                proposal.proposal_id,
                proposal.learner_id,
                proposal.channel,
                proposal.category,
                now_iso,
                fatigue_reason or "daily_cap_exceeded",
            )
            return TerminalFact(
                event_id=suppression_id,
                event_type="notification_suppressed",
                proposal_id=proposal.proposal_id,
                learner_id=proposal.learner_id,
                channel=proposal.channel.value,
                category=proposal.category.value,
                timestamp=now_iso,
                status=NotificationStatus.SUPPRESSED,
                reason=fatigue_reason or "daily_cap_exceeded",
            )

        # 4. Attempt delivery through adapter
        adapter = self.adapters.get(proposal.channel)
        if adapter is None:
            return TerminalFact(
                event_id=f"supp_{uuid.uuid4().hex[:12]}",
                event_type="notification_suppressed",
                proposal_id=proposal.proposal_id,
                learner_id=proposal.learner_id,
                channel=proposal.channel.value,
                category=proposal.category.value,
                timestamp=now_iso,
                status=NotificationStatus.SUPPRESSED,
                reason="unsupported_channel",
            )

        success, attempts, err = self.retry_scheduler.deliver_with_retry(adapter, proposal)
        if success:
            dispatch_id = f"disp_{uuid.uuid4().hex[:12]}"
            self.store.record_dispatch(
                dispatch_id,
                proposal.proposal_id,
                proposal.learner_id,
                proposal.channel,
                proposal.category,
                now_iso,
                proposal.payload,
            )
            self.store.resolve_dlq(dlq_id)
            return TerminalFact(
                event_id=dispatch_id,
                event_type="notification_dispatched",
                proposal_id=proposal.proposal_id,
                learner_id=proposal.learner_id,
                channel=proposal.channel.value,
                category=proposal.category.value,
                timestamp=now_iso,
                status=NotificationStatus.DISPATCHED,
                attempt_count=attempts,
            )
        else:
            return TerminalFact(
                event_id=dlq_id,
                event_type="notification_failed",
                proposal_id=proposal.proposal_id,
                learner_id=proposal.learner_id,
                channel=proposal.channel.value,
                category=proposal.category.value,
                timestamp=now_iso,
                status=NotificationStatus.FAILED,
                reason=err or "provider_exhausted",
                attempt_count=attempts,
            )
