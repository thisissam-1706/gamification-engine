"""Tests for notification crash safety, transaction atomicity, and clock injection (M5-N1).

Covers:
- Adapter raising unhandled RuntimeError ends as failed + DLQ row, not stuck.
- Simulated crash after registration followed by re-dispatch delivers exactly once.
- Suppressed proposal replayed after consent restored is still a duplicate and writes no new suppression row.
- Queued proposal re-dispatched is a duplicate and does not reset its release flag.
- Clock requirement: raises ValueError when omitted, succeeds with injected clock across simulated times.
"""

import pytest
from unittest.mock import MagicMock

from src.notifications.channels.base import BaseChannelAdapter
from src.notifications.channels.in_app import InAppChannelAdapter
from src.notifications.channels.push import SimulatedPushAdapter
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


class CrashingAdapter(BaseChannelAdapter):
    """Adapter that raises unexpected non-ProviderError exceptions (e.g. RuntimeError)."""

    def __init__(self, exc_to_raise: Exception) -> None:
        self.exc_to_raise = exc_to_raise
        self.attempts = 0

    def send(self, proposal: NotificationProposal) -> bool:
        self.attempts += 1
        raise self.exc_to_raise


def test_adapter_runtime_error_routes_to_dlq_not_stuck(tmp_path):
    """Proves: An adapter raising RuntimeError ends as failed plus a DLQ row, not stuck in_progress."""
    db_path = str(tmp_path / "notif_crash.db")
    store = NotificationStore(db_path)
    crashing_adapter = CrashingAdapter(RuntimeError("Network socket connection reset by peer"))
    retry_scheduler = RetryScheduler(max_retries=3, backoff_delay_sec=0.0)

    dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.PUSH: crashing_adapter},
        retry_scheduler=retry_scheduler,
    )

    proposal = NotificationProposal(
        proposal_id="p_runtime_err_01",
        learner_id="L_crash_01",
        channel=ChannelType.PUSH,
        category=NotificationCategory.STREAK_RISK,
        payload={"streak": 7},
    )
    prefs = LearnerPreferences(learner_id="L_crash_01")

    res = dispatcher.dispatch(proposal, prefs=prefs, now_utc="2026-10-12T12:00:00Z")

    # Proposal must not raise unhandled, must reach terminal failure fact
    assert res.status == NotificationStatus.FAILED
    assert res.event_type == "notification_failed"
    assert "RuntimeError" in (res.reason or "")
    assert "Network socket connection reset by peer" in (res.reason or "")
    assert res.attempt_count == 3
    assert crashing_adapter.attempts == 3

    # Proposal status in store is FAILED, not stuck in IN_PROGRESS
    assert store.get_proposal_status("p_runtime_err_01") == NotificationStatus.FAILED

    # DLQ row exists with error details
    dlq_entries = store.get_dlq_entries()
    assert len(dlq_entries) == 1
    assert dlq_entries[0]["proposal_id"] == "p_runtime_err_01"
    assert "RuntimeError" in dlq_entries[0]["reason"]
    assert dlq_entries[0]["resolved"] is False

    store.close()


def test_simulated_crash_after_registration_redispatch_delivers_once(tmp_path):
    """Proves: A simulated crash after registration, followed by re-dispatch, delivers exactly once."""
    db_path = str(tmp_path / "notif_crash_recovery.db")
    store = NotificationStore(db_path)
    in_app = InAppChannelAdapter()
    dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.IN_APP: in_app},
    )

    proposal = NotificationProposal(
        proposal_id="p_crash_recovery_01",
        learner_id="L_crash_02",
        channel=ChannelType.IN_APP,
        category=NotificationCategory.QUEST_UPDATE,
        payload={"badge": "level_10"},
    )
    prefs = LearnerPreferences(learner_id="L_crash_02")

    # Simulate crash: proposal is registered with status IN_PROGRESS, but process died before deliver/facts
    registered = store.register_proposal(proposal, status=NotificationStatus.IN_PROGRESS)
    assert registered is True
    assert store.get_proposal_status("p_crash_recovery_01") == NotificationStatus.IN_PROGRESS
    assert len(in_app.delivered_messages) == 0

    # On restart / re-dispatch: in_progress proposal is allowed to retry (not blocked as duplicate)
    res_retry = dispatcher.dispatch(proposal, prefs=prefs, now_utc="2026-10-12T12:00:00Z")
    assert res_retry.status == NotificationStatus.DISPATCHED
    assert res_retry.event_type == "notification_dispatched"
    assert len(in_app.delivered_messages) == 1
    assert store.get_proposal_status("p_crash_recovery_01") == NotificationStatus.DISPATCHED

    # Third attempt: now that it reached DISPATCHED terminal fact, duplicate detection blocks it
    res_dup = dispatcher.dispatch(proposal, prefs=prefs, now_utc="2026-10-12T12:05:00Z")
    assert res_dup.status == NotificationStatus.SUPPRESSED
    assert res_dup.reason == "duplicate_proposal"
    assert len(in_app.delivered_messages) == 1  # Delivered exactly once!

    store.close()


def test_suppressed_proposal_replayed_after_consent_restored_is_duplicate(tmp_path):
    """Proves: A suppressed proposal replayed after consent is restored is still a duplicate and writes no new suppression row."""
    db_path = str(tmp_path / "notif_consent_replay.db")
    store = NotificationStore(db_path)
    push = SimulatedPushAdapter()
    dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.PUSH: push},
    )

    proposal = NotificationProposal(
        proposal_id="p_supp_consent_01",
        learner_id="L_supp_01",
        channel=ChannelType.PUSH,
        category=NotificationCategory.STREAK_RISK,
        payload={"streak": 3},
    )

    # 1. Learner has opted out of PUSH
    opted_out_prefs = LearnerPreferences(
        learner_id="L_supp_01",
        opted_out_channels={ChannelType.PUSH},
    )

    res_supp = dispatcher.dispatch(proposal, prefs=opted_out_prefs, now_utc="2026-10-12T12:00:00Z")
    assert res_supp.status == NotificationStatus.SUPPRESSED
    assert res_supp.reason == "opt_out"
    assert len(push.delivered_messages) == 0

    # Verify 1 suppression row in DB and proposal status is SUPPRESSED
    supp_entries = store.get_suppression_entries("p_supp_consent_01")
    assert len(supp_entries) == 1
    assert supp_entries[0]["reason"] == "opt_out"
    assert store.get_proposal_status("p_supp_consent_01") == NotificationStatus.SUPPRESSED

    # 2. Consent is later restored!
    restored_prefs = LearnerPreferences(
        learner_id="L_supp_01",
        opted_out_channels=set(),
    )

    # 3. Caller replays the original proposal ID
    res_replay = dispatcher.dispatch(proposal, prefs=restored_prefs, now_utc="2026-10-12T14:00:00Z")
    assert res_replay.status == NotificationStatus.SUPPRESSED
    assert res_replay.reason == "duplicate_proposal"
    assert len(push.delivered_messages) == 0

    # Crucial assertion: no new suppression row written to DB! Count remains 1.
    supp_entries_after = store.get_suppression_entries("p_supp_consent_01")
    assert len(supp_entries_after) == 1
    assert store.get_proposal_status("p_supp_consent_01") == NotificationStatus.SUPPRESSED

    store.close()


def test_queued_proposal_redispatched_is_duplicate_and_preserves_release_flag(tmp_path):
    """Proves: A queued proposal re-dispatched is a duplicate and does not reset its release flag."""
    db_path = str(tmp_path / "notif_queued_dup.db")
    store = NotificationStore(db_path)
    push = SimulatedPushAdapter()
    dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.PUSH: push},
        quiet_start_hour=22,
        quiet_end_hour=7,
    )

    prefs = LearnerPreferences(learner_id="L_quiet", timezone="Asia/Kolkata")
    evening_ts = "2026-10-12T17:30:00Z"  # 23:00 IST -> quiet hours

    proposal = NotificationProposal(
        proposal_id="p_quiet_queue_01",
        learner_id="L_quiet",
        channel=ChannelType.PUSH,
        category=NotificationCategory.STREAK_RISK,
        created_at=evening_ts,
        ttl_seconds=86400,
    )

    # 1. Evening dispatch reschedules to queue
    res_queue = dispatcher.dispatch(proposal, prefs=prefs, now_utc=evening_ts)
    assert res_queue.status == NotificationStatus.QUEUED
    assert store.get_proposal_status("p_quiet_queue_01") == NotificationStatus.QUEUED

    resched_entry = store.get_rescheduled_entry("p_quiet_queue_01")
    assert resched_entry is not None
    assert resched_entry["released"] is False

    # 2. Re-dispatch while queued is duplicate and does NOT alter queued state
    res_queue_dup = dispatcher.dispatch(proposal, prefs=prefs, now_utc="2026-10-12T18:00:00Z")
    assert res_queue_dup.status == NotificationStatus.SUPPRESSED
    assert res_queue_dup.reason == "duplicate_proposal"
    assert store.get_proposal_status("p_quiet_queue_01") == NotificationStatus.QUEUED

    # 3. Caller releases the rescheduled proposal at morning time
    morning_facts = dispatcher.release_rescheduled(
        now_utc=res_queue.rescheduled_for,
        prefs_provider={"L_quiet": prefs},
    )
    assert len(morning_facts) == 1
    assert morning_facts[0].status == NotificationStatus.DISPATCHED
    assert len(push.delivered_messages) == 1

    # Verify release flag is True
    resched_released = store.get_rescheduled_entry("p_quiet_queue_01")
    assert resched_released is not None
    assert resched_released["released"] is True

    # 4. Re-dispatch after release: must NOT reset released flag!
    res_post_release_dup = dispatcher.dispatch(proposal, prefs=prefs, now_utc="2026-10-13T02:00:00Z")
    assert res_post_release_dup.status == NotificationStatus.SUPPRESSED
    assert res_post_release_dup.reason == "duplicate_proposal"

    # Crucial assertion: release flag in notification_rescheduled is STILL True!
    resched_final = store.get_rescheduled_entry("p_quiet_queue_01")
    assert resched_final is not None
    assert resched_final["released"] is True

    # Even direct record_rescheduled update does NOT reset released flag
    store.record_rescheduled(
        proposal_id="p_quiet_queue_01",
        learner_id="L_quiet",
        channel=ChannelType.PUSH,
        category=NotificationCategory.STREAK_RISK,
        rescheduled_for="2026-10-13T03:00:00Z",
        created_at=evening_ts,
        payload={},
    )
    resched_after_direct = store.get_rescheduled_entry("p_quiet_queue_01")
    assert resched_after_direct is not None
    assert resched_after_direct["released"] is True  # released flag was never reset to 0!

    store.close()


def test_clock_requirement_and_injected_clock(tmp_path):
    """Proves: dispatch() requires now_utc or injected clock (raises ValueError if omitted), works with simulated clock."""
    db_path = str(tmp_path / "notif_clock.db")
    store = NotificationStore(db_path)
    in_app = InAppChannelAdapter()

    # Dispatcher without clock
    dispatcher_no_clock = NotificationDispatcher(
        store=store,
        adapters={ChannelType.IN_APP: in_app},
    )

    prop = NotificationProposal(
        proposal_id="p_clock_test_01",
        learner_id="L_clock",
        channel=ChannelType.IN_APP,
        category=NotificationCategory.GENERAL,
    )
    prefs = LearnerPreferences(learner_id="L_clock")

    # Omitting now_utc without injected clock raises ValueError (deterministic behavior)
    with pytest.raises(ValueError, match="now_utc or an injected clock is required"):
        dispatcher_no_clock.dispatch(prop, prefs=prefs)

    # Injected clock provider
    simulated_time = "2026-10-12T15:00:00Z"
    dispatcher_with_clock = NotificationDispatcher(
        store=store,
        adapters={ChannelType.IN_APP: in_app},
        clock=lambda: simulated_time,
    )

    # Dispatch without now_utc uses the injected clock
    res_clock = dispatcher_with_clock.dispatch(prop, prefs=prefs)
    assert res_clock.status == NotificationStatus.DISPATCHED
    assert res_clock.timestamp == "2026-10-12T15:00:00Z"

    store.close()
