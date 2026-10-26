"""Notification outage and DLQ recovery drain harness (M5, M8)."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import time
from typing import Any

from src.notifications.channels.base import (
    BaseChannelAdapter,
    ProviderError,
    TransientProviderError,
)
from src.notifications.dispatcher import NotificationDispatcher
from src.notifications.models import (
    ChannelType,
    LearnerPreferences,
    NotificationCategory,
    NotificationProposal,
    NotificationStatus,
)
from src.notifications.queue import RetryScheduler
from src.notifications.store import NotificationStore


class ControllableOutageAdapter(BaseChannelAdapter):
    """Mock external channel adapter with controllable failure state."""

    def __init__(self, channel: ChannelType = ChannelType.PUSH) -> None:
        self.channel = channel
        self.outage_active = True
        self.delivered_proposals: list[str] = []

    def send(self, proposal: NotificationProposal) -> bool:
        """Sends proposal or raises TransientProviderError if outage is simulated."""
        if self.outage_active:
            raise TransientProviderError("Simulated channel outage (503 Service Unavailable)")
        self.delivered_proposals.append(proposal.proposal_id)
        return True


@dataclass
class OutageRecoveryResult:
    """Quantitative results of simulated notification outage and backlog recovery."""

    proposals_dispatched: int
    dlq_routed_count: int
    dlq_capture_rate: float
    recovery_drained_count: int
    recovery_drain_duration_sec: float
    drain_throughput_msgs_per_sec: float
    duplicate_deliveries_count: int
    passed: bool
    violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposals_dispatched": self.proposals_dispatched,
            "dlq_routed_count": self.dlq_routed_count,
            "dlq_capture_rate": self.dlq_capture_rate,
            "recovery_drained_count": self.recovery_drained_count,
            "recovery_drain_duration_sec": self.recovery_drain_duration_sec,
            "drain_throughput_msgs_per_sec": self.drain_throughput_msgs_per_sec,
            "duplicate_deliveries_count": self.duplicate_deliveries_count,
            "passed": self.passed,
            "violations": self.violations,
        }


class NotificationOutageHarness:
    """Simulates channel outage, verifies DLQ routing, restores channel, and validates drain."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.store = NotificationStore(db_path)
        self.adapter = ControllableOutageAdapter(ChannelType.PUSH)
        # Fast retry config for benchmarking
        self.retry_scheduler = RetryScheduler(max_retries=2, backoff_delay_sec=0.0)
        self.dispatcher = NotificationDispatcher(
            store=self.store,
            adapters={ChannelType.PUSH: self.adapter},
            retry_scheduler=self.retry_scheduler,
            quiet_start_hour=22,
            quiet_end_hour=7,
        )

    def close(self) -> None:
        self.store.close()

    def run_outage_and_recovery(
        self,
        num_proposals: int = 50,
        base_time_iso: str = "2026-10-12T14:00:00Z",
    ) -> OutageRecoveryResult:
        """Executes full outage lifecycle and validates recovery bounds."""
        violations: list[str] = []

        # 1. Phase 1: Outage Active -> All messages fail and route to DLQ
        self.adapter.outage_active = True
        self.adapter.delivered_proposals.clear()

        proposals: list[NotificationProposal] = []
        for i in range(num_proposals):
            prop = NotificationProposal(
                proposal_id=f"prop_outage_{i:04d}",
                learner_id=f"learner_{i % 10:04d}",
                channel=ChannelType.PUSH,
                category=NotificationCategory.STREAK_RISK,
                template_id="streak_warning_v1",
                payload={"streak_count": 5, "expires_in_hours": 3},
                created_at=base_time_iso,
                ttl_seconds=86400,
            )
            proposals.append(prop)

        # Dispatch all during outage
        prefs = LearnerPreferences(
            learner_id="common",
            timezone="UTC",
        )

        for prop in proposals:
            self.dispatcher.dispatch(prop, prefs=prefs, now_utc=base_time_iso)

        # Check DLQ status
        dlq_entries = self.store.get_dlq_entries(resolved=False)
        dlq_count = len(dlq_entries)
        capture_rate = dlq_count / max(num_proposals, 1)

        if dlq_count != num_proposals:
            violations.append(f"Expected {num_proposals} in DLQ, found {dlq_count}")
        if len(self.adapter.delivered_proposals) != 0:
            violations.append(f"Expected 0 deliveries during outage, got {len(self.adapter.delivered_proposals)}")

        # 2. Phase 2: Channel Recovers -> Replay DLQ and measure recovery drain
        self.adapter.outage_active = False
        recovery_time_iso = "2026-10-12T14:30:00Z"

        t0 = time.perf_counter()
        replayed_facts = []
        for entry in dlq_entries:
            dlq_id = entry["dlq_id"]
            fact = self.dispatcher.replay_dlq(dlq_id, prefs=prefs, now_utc=recovery_time_iso)
            replayed_facts.append(fact)
        t1 = time.perf_counter()

        recovery_duration = max(t1 - t0, 1e-6)
        drained_count = len(replayed_facts)
        drain_throughput = drained_count / recovery_duration

        # Verify DLQ backlog is completely cleared
        remaining_dlq = self.store.get_dlq_entries(resolved=False)
        if len(remaining_dlq) != 0:
            violations.append(f"Expected 0 unresolved DLQ entries post-recovery, found {len(remaining_dlq)}")

        # Check for duplicate deliveries
        distinct_deliveries = set(self.adapter.delivered_proposals)
        duplicates = len(self.adapter.delivered_proposals) - len(distinct_deliveries)
        if duplicates > 0:
            violations.append(f"Detected {duplicates} duplicate deliveries during recovery drain")

        passed = len(violations) == 0

        return OutageRecoveryResult(
            proposals_dispatched=num_proposals,
            dlq_routed_count=dlq_count,
            dlq_capture_rate=capture_rate,
            recovery_drained_count=drained_count,
            recovery_drain_duration_sec=recovery_duration,
            drain_throughput_msgs_per_sec=drain_throughput,
            duplicate_deliveries_count=duplicates,
            passed=passed,
            violations=violations,
        )
