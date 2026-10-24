"""AT-10: Retry & DLQ (M5)

Given a notification delivery attempt fails due to a provider error, when retries are exhausted, then the message routes to the DLQ and is recoverable after the channel failure is resolved.
"""

from unittest.mock import MagicMock
from src.notifications.channels.base import BaseChannelAdapter, PermanentProviderError
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


def test_retry_dlq(tmp_path):
    store = NotificationStore(str(tmp_path / "notif.db"))
    # Simulated provider fails 5 consecutive times with transient network timeout
    failing_push = SimulatedPushAdapter(fail_attempts=5)
    retry_scheduler = RetryScheduler(max_retries=3, backoff_delay_sec=0.0)

    dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.PUSH: failing_push},
        retry_scheduler=retry_scheduler,
    )
    prefs = LearnerPreferences(learner_id="L1")

    prop = NotificationProposal(
        proposal_id="p_retry_01",
        learner_id="L1",
        channel=ChannelType.PUSH,
        category=NotificationCategory.REMEDIATION,
        template_id="checkpoint_remediation_v1",
        payload={"module": "math_101"},
    )

    res = dispatcher.dispatch(prop, prefs=prefs)

    # All 3 retries were exhausted -> routed to DLQ with terminal failure fact
    assert res.status == NotificationStatus.FAILED
    assert res.event_type == "notification_failed"
    assert "provider_exhausted" in (res.reason or "")
    assert res.attempt_count == 3
    assert failing_push.attempts_seen == 3

    # Verify message is preserved in DLQ
    dlq_entries = store.get_dlq_entries()
    assert len(dlq_entries) == 1
    assert dlq_entries[0]["proposal_id"] == "p_retry_01"
    assert dlq_entries[0]["attempts"] == 3
    assert dlq_entries[0]["payload"] == {"module": "math_101"}
    assert dlq_entries[0]["resolved"] is False

    dlq_id = dlq_entries[0]["dlq_id"]

    # F3: Recovery scenario using replay_dlq preserving proposal identity and marking resolved
    recovered_push = SimulatedPushAdapter(fail_attempts=0)
    recovery_dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.PUSH: recovered_push},
    )

    replay_res = recovery_dispatcher.replay_dlq(dlq_id, prefs=prefs)
    assert replay_res.status == NotificationStatus.DISPATCHED
    assert replay_res.proposal_id == "p_retry_01"  # Preserved original ID
    assert len(recovered_push.delivered_messages) == 1

    # Verify DLQ row is now marked resolved
    updated_dlq = store.get_dlq_entries()
    assert updated_dlq[0]["resolved"] is True

    store.close()


def test_permanent_failure_bypasses_retries(tmp_path):
    """Proves M5-6: Permanent provider errors fail immediately on attempt 1 without wasting retries."""
    store = NotificationStore(str(tmp_path / "notif_perm.db"))
    perm_push = SimulatedPushAdapter(fail_permanently=True)
    retry_scheduler = RetryScheduler(max_retries=3, backoff_delay_sec=0.0)

    dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.PUSH: perm_push},
        retry_scheduler=retry_scheduler,
    )
    prefs = LearnerPreferences(learner_id="L_perm")

    prop = NotificationProposal(
        proposal_id="p_perm_01",
        learner_id="L_perm",
        channel=ChannelType.PUSH,
        category=NotificationCategory.REMEDIATION,
        payload={"error": "invalid_token"},
    )

    res = dispatcher.dispatch(prop, prefs=prefs)
    assert res.status == NotificationStatus.FAILED
    assert res.attempt_count == 1  # Bypassed remaining 2 retries
    assert "permanent_failure" in (res.reason or "")
    assert perm_push.attempts_seen == 1

    store.close()


def test_adapter_returning_false_is_handled(tmp_path):
    """Proves M5-6: An adapter that returns False without raising is retried and reported cleanly."""
    store = NotificationStore(str(tmp_path / "notif_false.db"))
    mock_adapter = MagicMock(spec=BaseChannelAdapter)
    mock_adapter.send.return_value = False

    retry_scheduler = RetryScheduler(max_retries=2, backoff_delay_sec=0.0)
    dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.PUSH: mock_adapter},
        retry_scheduler=retry_scheduler,
    )
    prefs = LearnerPreferences(learner_id="L_false")

    prop = NotificationProposal(
        proposal_id="p_false_01",
        learner_id="L_false",
        channel=ChannelType.PUSH,
        category=NotificationCategory.REMEDIATION,
        payload={},
    )

    res = dispatcher.dispatch(prop, prefs=prefs)
    assert res.status == NotificationStatus.FAILED
    assert res.attempt_count == 2
    assert "adapter_returned_false" in (res.reason or "")

    store.close()
