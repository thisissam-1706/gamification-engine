"""Integration tests for release_rescheduled and replay_dlq enhancements (M5-N2).

Covers:
- A short-TTL message is suppressed on release.
- A release during changed-timezone quiet hours is re-queued with the new release time.
- Replaying a resolved DLQ entry twice sends once.
- A stale DLQ entry is suppressed on replay.
- Template and TTL survive release and replay (assert delivered message uses stored template & TTL).
- A crash simulated mid-release does not lose the message.
- Non-midnight-spanning quiet window computes the correct next day (same-day end hour).
"""

from datetime import datetime, timezone
import pytest

from src.notifications.channels.base import BaseChannelAdapter
from src.notifications.channels.in_app import InAppChannelAdapter
from src.notifications.channels.push import SimulatedPushAdapter
from src.notifications.dispatcher import NotificationDispatcher
from src.notifications.guardrails import evaluate_quiet_hours
from src.notifications.models import (
    ChannelType,
    LearnerPreferences,
    NotificationCategory,
    NotificationProposal,
    NotificationStatus,
)
from src.notifications.queue import RetryScheduler
from src.notifications.store import NotificationStore


class DyingAdapter(BaseChannelAdapter):
    """Adapter that simulates a crash/unhandled fatal error on first call, succeeds on restart."""

    def __init__(self, should_crash: bool = True) -> None:
        self.should_crash = should_crash
        self.delivered_proposals: list[NotificationProposal] = []

    def send(self, proposal: NotificationProposal) -> bool:
        if self.should_crash:
            raise KeyboardInterrupt("Simulated sudden process death mid-send")
        self.delivered_proposals.append(proposal)
        return True


class CapturingAdapter(BaseChannelAdapter):
    """Adapter that stores the exact NotificationProposal received by send()."""

    def __init__(self) -> None:
        self.proposals: list[NotificationProposal] = []

    def send(self, proposal: NotificationProposal) -> bool:
        self.proposals.append(proposal)
        return True


def test_short_ttl_message_suppressed_on_release(tmp_path):
    """Proves: A short-TTL message is suppressed upon morning release."""
    db_path = str(tmp_path / "notif_ttl_release.db")
    store = NotificationStore(db_path)
    push = SimulatedPushAdapter()
    dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.PUSH: push},
        quiet_start_hour=22,
        quiet_end_hour=7,
    )

    prefs = LearnerPreferences(learner_id="L_ttl_rel", timezone="Asia/Kolkata")
    evening_ts = "2026-10-12T17:30:00Z"  # 23:00 IST -> quiet hours

    # TTL: 8.5 hours (30600s). Target 07:00 IST is 8h later, so not yet expired at dispatch.
    prop = NotificationProposal(
        proposal_id="p_short_ttl_01",
        learner_id="L_ttl_rel",
        channel=ChannelType.PUSH,
        category=NotificationCategory.STREAK_RISK,
        template_id="streak_warning_short",
        created_at=evening_ts,
        ttl_seconds=8 * 3600 + 1800,
    )

    res_resched = dispatcher.dispatch(prop, prefs=prefs, now_utc=evening_ts)
    assert res_resched.status == NotificationStatus.QUEUED
    assert res_resched.rescheduled_for == "2026-10-13T01:30:00Z"  # 07:00 IST

    # Morning release job runs at 08:00 IST (2026-10-13T02:30:00Z), 9 hours after creation (> 8.5h TTL)
    morning_facts = dispatcher.release_rescheduled(
        now_utc="2026-10-13T02:30:00Z",
        prefs_provider={"L_ttl_rel": prefs},
    )

    assert len(morning_facts) == 1
    assert morning_facts[0].status == NotificationStatus.SUPPRESSED
    assert morning_facts[0].reason == "ttl_expired"
    assert len(push.delivered_messages) == 0

    # Proposal in store is SUPPRESSED and rescheduled row is marked released
    assert store.get_proposal_status("p_short_ttl_01") == NotificationStatus.SUPPRESSED
    resched_entry = store.get_rescheduled_entry("p_short_ttl_01")
    assert resched_entry is not None
    assert resched_entry["released"] is True

    store.close()


def test_release_during_changed_timezone_quiet_hours_requeued(tmp_path):
    """Proves: A release during changed-timezone quiet hours is re-queued with the new release time."""
    db_path = str(tmp_path / "notif_tz_change.db")
    store = NotificationStore(db_path)
    push = SimulatedPushAdapter()
    dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.PUSH: push},
        quiet_start_hour=22,
        quiet_end_hour=7,
    )

    # Initial timezone: UTC. Proposal dispatched at 22:30 UTC -> quiet hours
    initial_prefs = LearnerPreferences(learner_id="L_traveler", timezone="UTC")
    evening_ts = "2026-10-12T22:30:00Z"

    prop = NotificationProposal(
        proposal_id="p_tz_travel_01",
        learner_id="L_traveler",
        channel=ChannelType.PUSH,
        category=NotificationCategory.STREAK_RISK,
        created_at=evening_ts,
        ttl_seconds=86400,
    )

    res_queue = dispatcher.dispatch(prop, prefs=initial_prefs, now_utc=evening_ts)
    assert res_queue.status == NotificationStatus.QUEUED
    assert res_queue.rescheduled_for == "2026-10-13T07:00:00Z"

    # Learner travels to New York (America/New_York, UTC-4)
    # At 2026-10-13T07:00:00Z, New York local time is 03:00 (inside 22:00-07:00 quiet hours!)
    ny_prefs = LearnerPreferences(learner_id="L_traveler", timezone="America/New_York")

    release_facts = dispatcher.release_rescheduled(
        now_utc="2026-10-13T07:00:00Z",
        prefs_provider={"L_traveler": ny_prefs},
    )

    assert len(release_facts) == 1
    re_fact = release_facts[0]
    assert re_fact.status == NotificationStatus.QUEUED
    assert re_fact.reason == "quiet_hours_rescheduled"
    # New release time: 07:00 NY time on Oct 13 = 11:00 UTC
    assert re_fact.rescheduled_for == "2026-10-13T11:00:00Z"
    assert len(push.delivered_messages) == 0

    # Rescheduled row must NOT be marked released yet
    resched_entry = store.get_rescheduled_entry("p_tz_travel_01")
    assert resched_entry is not None
    assert resched_entry["released"] is False
    assert resched_entry["rescheduled_for"] == "2026-10-13T11:00:00Z"

    store.close()


def test_replaying_resolved_dlq_entry_twice_sends_once(tmp_path):
    """Proves: Replaying a resolved DLQ entry twice sends once and refuses the second replay."""
    db_path = str(tmp_path / "notif_dlq_twice.db")
    store = NotificationStore(db_path)
    failing_push = SimulatedPushAdapter(fail_attempts=5)
    retry_scheduler = RetryScheduler(max_retries=2, backoff_delay_sec=0.0)

    dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.PUSH: failing_push},
        retry_scheduler=retry_scheduler,
    )
    prefs = LearnerPreferences(learner_id="L_replay_twice")

    prop = NotificationProposal(
        proposal_id="p_dlq_twice_01",
        learner_id="L_replay_twice",
        channel=ChannelType.PUSH,
        category=NotificationCategory.REMEDIATION,
        payload={"topic": "algebra"},
    )

    # Initial dispatch exhausts retries and routes to DLQ
    res_fail = dispatcher.dispatch(prop, prefs=prefs, now_utc="2026-10-12T12:00:00Z")
    assert res_fail.status == NotificationStatus.FAILED
    dlq_entries = store.get_dlq_entries()
    assert len(dlq_entries) == 1
    dlq_id = dlq_entries[0]["dlq_id"]

    # Channel recovered with working adapter
    recovered_push = SimulatedPushAdapter(fail_attempts=0)
    recovery_dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.PUSH: recovered_push},
    )

    # First replay: succeeds and marks DLQ entry resolved atomically
    res_replay_1 = recovery_dispatcher.replay_dlq(dlq_id, prefs=prefs, now_utc="2026-10-12T13:00:00Z")
    assert res_replay_1.status == NotificationStatus.DISPATCHED
    assert len(recovered_push.delivered_messages) == 1

    entry_after = store.get_dlq_entry(dlq_id)
    assert entry_after is not None
    assert entry_after["resolved"] is True

    # Second replay: refused because it is already resolved
    with pytest.raises(ValueError, match="already been resolved"):
        recovery_dispatcher.replay_dlq(dlq_id, prefs=prefs, now_utc="2026-10-12T13:05:00Z")

    # Delivered count remains 1 across both replays (sends exactly once)
    assert len(recovered_push.delivered_messages) == 1

    store.close()


def test_stale_dlq_entry_suppressed_on_replay(tmp_path):
    """Proves: A stale DLQ entry is suppressed on replay against the original created_at."""
    db_path = str(tmp_path / "notif_dlq_stale.db")
    store = NotificationStore(db_path)
    failing_push = SimulatedPushAdapter(fail_attempts=5)
    retry_scheduler = RetryScheduler(max_retries=2, backoff_delay_sec=0.0)

    dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.PUSH: failing_push},
        retry_scheduler=retry_scheduler,
    )
    prefs = LearnerPreferences(learner_id="L_stale_dlq")

    # 1 hour TTL
    created_at = "2026-10-12T10:00:00Z"
    prop = NotificationProposal(
        proposal_id="p_stale_dlq_01",
        learner_id="L_stale_dlq",
        channel=ChannelType.PUSH,
        category=NotificationCategory.REMEDIATION,
        created_at=created_at,
        ttl_seconds=3600,
    )

    res_fail = dispatcher.dispatch(prop, prefs=prefs, now_utc="2026-10-12T10:05:00Z")
    assert res_fail.status == NotificationStatus.FAILED
    dlq_id = store.get_dlq_entries()[0]["dlq_id"]

    # Replay 3 hours later (2026-10-12T13:00:00Z > 10:00:00Z + 3600s TTL)
    recovered_push = SimulatedPushAdapter(fail_attempts=0)
    recovery_dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.PUSH: recovered_push},
    )

    res_replay = recovery_dispatcher.replay_dlq(dlq_id, prefs=prefs, now_utc="2026-10-12T13:00:00Z")
    assert res_replay.status == NotificationStatus.SUPPRESSED
    assert res_replay.reason == "ttl_expired"
    assert len(recovered_push.delivered_messages) == 0

    store.close()


def test_template_and_ttl_survive_release_and_replay(tmp_path):
    """Proves: Template ID and TTL survive both release_rescheduled and replay_dlq."""
    db_path = str(tmp_path / "notif_template_ttl.db")
    store = NotificationStore(db_path)

    # 1. Release path preserves template and TTL
    capturing_push = CapturingAdapter()
    dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.PUSH: capturing_push},
        quiet_start_hour=22,
        quiet_end_hour=7,
    )
    prefs = LearnerPreferences(learner_id="L_meta", timezone="UTC")

    prop_resched = NotificationProposal(
        proposal_id="p_meta_resched",
        learner_id="L_meta",
        channel=ChannelType.PUSH,
        category=NotificationCategory.STREAK_RISK,
        template_id="custom_streak_gold_v2",
        ttl_seconds=72000,
        payload={"tier": "gold"},
    )

    # Rescheduled at 23:00 UTC
    dispatcher.dispatch(prop_resched, prefs=prefs, now_utc="2026-10-12T23:00:00Z")

    # Release at 07:00 UTC next morning
    dispatcher.release_rescheduled(
        now_utc="2026-10-13T07:00:00Z",
        prefs_provider={"L_meta": prefs},
    )

    assert len(capturing_push.proposals) == 1
    rel_delivered = capturing_push.proposals[0]
    assert rel_delivered.template_id == "custom_streak_gold_v2"
    assert rel_delivered.ttl_seconds == 72000
    assert rel_delivered.payload == {"tier": "gold"}

    # 2. Replay path preserves template and TTL
    failing_in_app = SimulatedPushAdapter(fail_attempts=5)
    retry_scheduler = RetryScheduler(max_retries=2, backoff_delay_sec=0.0)
    fail_dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.PUSH: failing_in_app},
        retry_scheduler=retry_scheduler,
    )

    prop_dlq = NotificationProposal(
        proposal_id="p_meta_dlq",
        learner_id="L_meta",
        channel=ChannelType.PUSH,
        category=NotificationCategory.QUEST_UPDATE,
        template_id="quest_complete_celebration_v5",
        ttl_seconds=172800,
        payload={"quest": "dragon_slayer"},
    )

    fail_dispatcher.dispatch(prop_dlq, prefs=prefs, now_utc="2026-10-13T12:00:00Z")
    dlq_id = [e["dlq_id"] for e in store.get_dlq_entries() if e["proposal_id"] == "p_meta_dlq"][0]

    capturing_replay = CapturingAdapter()
    replay_dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.PUSH: capturing_replay},
    )

    replay_dispatcher.replay_dlq(dlq_id, prefs=prefs, now_utc="2026-10-13T13:00:00Z")

    assert len(capturing_replay.proposals) == 1
    replay_delivered = capturing_replay.proposals[0]
    assert replay_delivered.template_id == "quest_complete_celebration_v5"
    assert replay_delivered.ttl_seconds == 172800
    assert replay_delivered.payload == {"quest": "dragon_slayer"}

    store.close()


def test_simulated_crash_mid_release_does_not_lose_message(tmp_path):
    """Proves: A simulated crash mid-release does not lose the message (released flag remains 0)."""
    db_path = str(tmp_path / "notif_crash_release.db")
    store = NotificationStore(db_path)
    crashing_adapter = DyingAdapter(should_crash=True)
    dispatcher_crashing = NotificationDispatcher(
        store=store,
        adapters={ChannelType.PUSH: crashing_adapter},
        quiet_start_hour=22,
        quiet_end_hour=7,
    )
    prefs = LearnerPreferences(learner_id="L_crash_rel", timezone="UTC")

    prop = NotificationProposal(
        proposal_id="p_crash_rel_01",
        learner_id="L_crash_rel",
        channel=ChannelType.PUSH,
        category=NotificationCategory.STREAK_RISK,
        template_id="streak_warning_v1",
        ttl_seconds=86400,
    )

    # 1. Proposal is queued during quiet hours
    res_queue = dispatcher_crashing.dispatch(prop, prefs=prefs, now_utc="2026-10-12T23:00:00Z")
    assert res_queue.status == NotificationStatus.QUEUED

    # 2. Morning arrives; release attempt crashes during adapter.send
    with pytest.raises(KeyboardInterrupt, match="Simulated sudden process death"):
        dispatcher_crashing.release_rescheduled(
            now_utc="2026-10-13T07:00:00Z",
            prefs_provider={"L_crash_rel": prefs},
        )

    # Crucial assertion: release flag in notification_rescheduled is STILL 0 (not marked released prematurely!)
    entry = store.get_rescheduled_entry("p_crash_rel_01")
    assert entry is not None
    assert entry["released"] is False

    # 3. Process restarts with healthy adapter
    recovered_push = SimulatedPushAdapter(fail_attempts=0)
    dispatcher_recovered = NotificationDispatcher(
        store=store,
        adapters={ChannelType.PUSH: recovered_push},
        quiet_start_hour=22,
        quiet_end_hour=7,
    )

    facts = dispatcher_recovered.release_rescheduled(
        now_utc="2026-10-13T07:01:00Z",
        prefs_provider={"L_crash_rel": prefs},
    )

    # Message was NOT lost: delivered on recovery and now marked released
    assert len(facts) == 1
    assert facts[0].status == NotificationStatus.DISPATCHED
    assert len(recovered_push.delivered_messages) == 1

    entry_final = store.get_rescheduled_entry("p_crash_rel_01")
    assert entry_final is not None
    assert entry_final["released"] is True

    store.close()


def test_non_midnight_spanning_quiet_window_computes_correct_next_day():
    """Proves: Non-midnight-spanning quiet window (e.g. 13:00 to 15:00) computes same-day end hour."""
    now_utc = datetime(2026, 10, 12, 13, 30, tzinfo=timezone.utc)

    # 1. Inside quiet window: 13:00 to 15:00
    in_quiet, next_allowed = evaluate_quiet_hours(
        now_utc=now_utc,
        timezone_str="UTC",
        quiet_start_hour=13,
        quiet_end_hour=15,
    )
    assert in_quiet is True
    assert next_allowed is not None
    # End hour must be on the SAME DAY at 15:00 UTC (not tomorrow!)
    assert next_allowed.date() == now_utc.date()
    assert next_allowed.hour == 15
    assert next_allowed.minute == 0

    # 2. Before quiet window (12:30 UTC): not in quiet hours
    before_dt = datetime(2026, 10, 12, 12, 30, tzinfo=timezone.utc)
    in_quiet_before, next_before = evaluate_quiet_hours(
        now_utc=before_dt,
        timezone_str="UTC",
        quiet_start_hour=13,
        quiet_end_hour=15,
    )
    assert in_quiet_before is False
    assert next_before is None

    # 3. After quiet window (15:30 UTC): not in quiet hours
    after_dt = datetime(2026, 10, 12, 15, 30, tzinfo=timezone.utc)
    in_quiet_after, next_after = evaluate_quiet_hours(
        now_utc=after_dt,
        timezone_str="UTC",
        quiet_start_hour=13,
        quiet_end_hour=15,
    )
    assert in_quiet_after is False
    assert next_after is None
