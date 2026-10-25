"""End-to-End integration tests for Milestone M5 Notification Slice.

Exercises:
- Queued opt-out (consent revoked while queued, released via release_rescheduled).
- Stale-after-quiet-hours (message TTL expires during quiet-hour delay).
- Strict separation of terminal facts: policy suppression vs provider failure.
- In-app and simulated push adapter dispatches.
- Release of scheduled morning notifications.
- Missing preferences and invalid timezone handling.
"""

from datetime import datetime, timezone

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


def test_stale_after_quiet_hours_suppression(tmp_path):
    """Reviewer Deliverable 3: A message that goes stale during quiet hours must be suppressed (ttl_expired)."""
    store = NotificationStore(str(tmp_path / "notif.db"))
    push = SimulatedPushAdapter()
    dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.PUSH: push},
        quiet_start_hour=22,
        quiet_end_hour=7,
    )

    prefs = LearnerPreferences(learner_id="L_stale", timezone="Asia/Kolkata")
    # Generated at 23:00 IST (17:30 UTC), but with only a 3-hour TTL (10800 sec)
    # Next morning 07:00 IST is 8 hours later, so TTL will have expired!
    gen_utc = "2026-10-12T17:30:00Z"

    prop = NotificationProposal(
        proposal_id="p_short_ttl",
        learner_id="L_stale",
        channel=ChannelType.PUSH,
        category=NotificationCategory.STREAK_RISK,
        template_id="short_reminder",
        created_at=gen_utc,
        ttl_seconds=3 * 3600,  # 3 hours TTL
    )

    res = dispatcher.dispatch(prop, prefs=prefs, now_utc=gen_utc)

    # Must be suppressed because it will be stale before quiet hours end!
    assert res.status == NotificationStatus.SUPPRESSED
    assert res.event_type == "notification_suppressed"
    assert res.reason == "ttl_expired"
    assert len(push.delivered_messages) == 0

    store.close()


def test_queued_opt_out_cancellation(tmp_path):
    """Reviewer Deliverable 3 & F5: If consent is revoked while queued, release_rescheduled suppresses with opt_out."""
    store = NotificationStore(str(tmp_path / "notif.db"))
    push = SimulatedPushAdapter()
    dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.PUSH: push},
        quiet_start_hour=22,
        quiet_end_hour=7,
    )

    # Initial state: learner has not opted out
    active_prefs = LearnerPreferences(learner_id="L_revoke", timezone="Asia/Kolkata")
    evening_ts = "2026-10-12T17:30:00Z"  # 23:00 IST

    prop = NotificationProposal(
        proposal_id="p_queued_optout",
        learner_id="L_revoke",
        channel=ChannelType.PUSH,
        category=NotificationCategory.STREAK_RISK,
        template_id="streak_warning_v1",
        created_at=evening_ts,
        ttl_seconds=86400,
    )

    # 1. Evening dispatch reschedules for next morning
    resched_fact = dispatcher.dispatch(prop, prefs=active_prefs, now_utc=evening_ts)
    assert resched_fact.status == NotificationStatus.QUEUED
    assert resched_fact.rescheduled_for == "2026-10-13T01:30:00Z"

    # 2. Before morning dispatch arrives, learner revokes Push consent!
    revoked_prefs = LearnerPreferences(
        learner_id="L_revoke",
        timezone="Asia/Kolkata",
        opted_out_channels={ChannelType.PUSH},
    )

    # 3. Caller invokes release_rescheduled at morning dispatch instant
    morning_facts = dispatcher.release_rescheduled(
        now_utc=resched_fact.rescheduled_for,
        prefs_provider={"L_revoke": revoked_prefs},
    )

    assert len(morning_facts) == 1
    morning_fact = morning_facts[0]
    # Preserves original proposal_id
    assert morning_fact.proposal_id == "p_queued_optout"
    assert morning_fact.status == NotificationStatus.SUPPRESSED
    assert morning_fact.reason == "opt_out"
    assert len(push.delivered_messages) == 0

    store.close()


def test_release_rescheduled_delivers_due_messages(tmp_path):
    """Proves F5: release_rescheduled delivers morning queued messages when consent is active."""
    store = NotificationStore(str(tmp_path / "notif_rel.db"))
    push = SimulatedPushAdapter()
    dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.PUSH: push},
        quiet_start_hour=22,
        quiet_end_hour=7,
    )
    prefs = LearnerPreferences(learner_id="L_morning", timezone="Asia/Kolkata")
    evening_ts = "2026-10-12T17:30:00Z"  # 23:00 IST

    prop = NotificationProposal(
        proposal_id="p_morning_happy",
        learner_id="L_morning",
        channel=ChannelType.PUSH,
        category=NotificationCategory.STREAK_RISK,
        created_at=evening_ts,
        ttl_seconds=86400,
    )

    resched = dispatcher.dispatch(prop, prefs=prefs, now_utc=evening_ts)
    assert resched.status == NotificationStatus.QUEUED

    # Release at 07:00 IST next morning
    morning_facts = dispatcher.release_rescheduled(
        now_utc=resched.rescheduled_for,
        prefs_provider={"L_morning": prefs},
    )
    assert len(morning_facts) == 1
    assert morning_facts[0].status == NotificationStatus.DISPATCHED
    assert morning_facts[0].proposal_id == "p_morning_happy"
    assert len(push.delivered_messages) == 1

    # Second release at same time does not double send (row marked released)
    second_facts = dispatcher.release_rescheduled(
        now_utc=resched.rescheduled_for,
        prefs_provider={"L_morning": prefs},
    )
    assert len(second_facts) == 0
    assert len(push.delivered_messages) == 1

    store.close()


def test_missing_preferences_and_invalid_timezone(tmp_path):
    """Proves M5-2 & M5-3: Missing preferences and invalid timezones fail cleanly with descriptive reasons."""
    store = NotificationStore(str(tmp_path / "notif_err.db"))
    in_app = InAppChannelAdapter()
    dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.IN_APP: in_app},
    )

    prop = NotificationProposal(
        proposal_id="p_err_01",
        learner_id="L_err",
        channel=ChannelType.IN_APP,
        category=NotificationCategory.GENERAL,
    )

    # 1. Missing preferences
    res_noprefs = dispatcher.dispatch(prop, prefs=None, now_utc="2026-10-12T12:00:00Z")
    assert res_noprefs.status == NotificationStatus.SUPPRESSED
    assert res_noprefs.reason == "missing_preferences"

    # 2. Invalid timezone string
    prop_bad_tz = NotificationProposal(
        proposal_id="p_bad_tz",
        learner_id="L_err",
        channel=ChannelType.IN_APP,
        category=NotificationCategory.GENERAL,
    )
    bad_prefs = LearnerPreferences(learner_id="L_err", timezone="Invalid/Nonexistent_Timezone")
    res_bad_tz = dispatcher.dispatch(prop_bad_tz, prefs=bad_prefs, now_utc="2026-10-12T12:00:00Z")
    assert res_bad_tz.status == NotificationStatus.SUPPRESSED
    assert res_bad_tz.reason == "invalid_timezone"

    store.close()


def test_terminal_facts_distinguish_suppression_from_provider_failure(tmp_path):
    """Reviewer Deliverable 3: Suppression and provider failure must produce different terminal events."""
    store = NotificationStore(str(tmp_path / "notif.db"))
    in_app = InAppChannelAdapter()
    failing_push = SimulatedPushAdapter(fail_attempts=5)
    retry_scheduler = RetryScheduler(max_retries=2, backoff_delay_sec=0.0)

    dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.IN_APP: in_app, ChannelType.PUSH: failing_push},
        retry_scheduler=retry_scheduler,
    )

    # Case A: Policy Suppression (e.g. opt-out)
    optout_prefs = LearnerPreferences(learner_id="L_a", opted_out_channels={ChannelType.IN_APP})
    prop_suppressed = NotificationProposal(
        proposal_id="p_supp_test",
        learner_id="L_a",
        channel=ChannelType.IN_APP,
        category=NotificationCategory.GENERAL,
        template_id="t1",
    )
    fact_suppressed = dispatcher.dispatch(prop_suppressed, prefs=optout_prefs, now_utc="2026-10-12T12:00:00Z")

    assert fact_suppressed.status == NotificationStatus.SUPPRESSED
    assert fact_suppressed.event_type == "notification_suppressed"
    assert fact_suppressed.reason == "opt_out"

    # Case B: Provider Delivery Failure (exhausted retries)
    normal_prefs = LearnerPreferences(learner_id="L_b")
    prop_failed = NotificationProposal(
        proposal_id="p_fail_test",
        learner_id="L_b",
        channel=ChannelType.PUSH,
        category=NotificationCategory.GENERAL,
        template_id="t2",
    )
    fact_failed = dispatcher.dispatch(prop_failed, prefs=normal_prefs, now_utc="2026-10-12T12:00:00Z")

    assert fact_failed.status == NotificationStatus.FAILED
    assert fact_failed.event_type == "notification_failed"
    assert "provider_exhausted" in (fact_failed.reason or "")

    # Events are distinctly typed and structured
    assert fact_suppressed.event_type != fact_failed.event_type
    assert fact_suppressed.status != fact_failed.status

    store.close()
