"""AT-8: Quiet-Hour Delay (M5)

Given L1’s timezone is Asia/Kolkata with quiet hours 22:00–07:00, when a streak-risk candidate generates at 23:10 local, then delivery reschedules to 07:00 next day and counts against that day’s 5-message cap.
"""

from src.notifications.channels.push import SimulatedPushAdapter
from src.notifications.dispatcher import NotificationDispatcher
from src.notifications.models import (
    ChannelType,
    LearnerPreferences,
    NotificationCategory,
    NotificationProposal,
    NotificationStatus,
)
from src.notifications.store import NotificationStore


def test_quiet_hour_delay(tmp_path):
    store = NotificationStore(str(tmp_path / "notif.db"))
    push = SimulatedPushAdapter()
    dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.PUSH: push},
        quiet_start_hour=22,
        quiet_end_hour=7,
    )

    prefs = LearnerPreferences(learner_id="L1", timezone="Asia/Kolkata")

    # 23:10 local IST on 2026-10-12 is 17:40 UTC on 2026-10-12
    generated_at_utc = "2026-10-12T17:40:00Z"

    prop = NotificationProposal(
        proposal_id="p_streak_01",
        learner_id="L1",
        channel=ChannelType.PUSH,
        category=NotificationCategory.STREAK_RISK,
        template_id="streak_warning_v1",
        created_at=generated_at_utc,
        ttl_seconds=86400,
    )

    res = dispatcher.dispatch(prop, prefs=prefs, now_utc=generated_at_utc)

    # Must be rescheduled rather than delivered immediately
    assert res.status == NotificationStatus.QUEUED
    assert res.event_type == "notification_rescheduled"
    assert res.reason == "quiet_hours_rescheduled"

    # Rescheduled instant: 07:00 next day IST is 01:30 UTC on 2026-10-13
    assert res.rescheduled_for == "2026-10-13T01:30:00Z"
    # Zero immediate delivery
    assert len(push.delivered_messages) == 0

    # When morning arrives at 07:00 IST (01:30 UTC), morning delivery proceeds
    morning_prop = NotificationProposal(
        proposal_id="p_streak_01_rescheduled",
        learner_id="L1",
        channel=ChannelType.PUSH,
        category=NotificationCategory.STREAK_RISK,
        template_id="streak_warning_v1",
        created_at=res.rescheduled_for,
    )
    morning_res = dispatcher.dispatch(morning_prop, prefs=prefs, now_utc=res.rescheduled_for)
    assert morning_res.status == NotificationStatus.DISPATCHED
    assert len(push.delivered_messages) == 1

    store.close()


def test_quiet_hour_exact_boundaries_and_dst(tmp_path):
    """Proves M5-5: Evaluates 22:00 (inside) and 07:00 (outside) boundaries and DST transitions deterministically."""
    store = NotificationStore(str(tmp_path / "notif_dst.db"))
    push = SimulatedPushAdapter()
    dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.PUSH: push},
        quiet_start_hour=22,
        quiet_end_hour=7,
    )
    prefs_ny = LearnerPreferences(learner_id="L_ny", timezone="America/New_York")

    # 1. At exactly 22:00:00 local time in America/New_York (EDT UTC-4 on 2026-10-12 -> 02:00 UTC next day)
    prop_2200 = NotificationProposal(
        proposal_id="p_2200",
        learner_id="L_ny",
        channel=ChannelType.PUSH,
        category=NotificationCategory.STREAK_RISK,
        created_at="2026-10-13T02:00:00Z",
    )
    res_2200 = dispatcher.dispatch(prop_2200, prefs=prefs_ny, now_utc="2026-10-13T02:00:00Z")
    # Exactly 22:00 is inside quiet hours
    assert res_2200.status == NotificationStatus.QUEUED
    assert res_2200.reason == "quiet_hours_rescheduled"

    # 2. At exactly 07:00:00 local time in America/New_York (11:00 UTC)
    prop_0700 = NotificationProposal(
        proposal_id="p_0700",
        learner_id="L_ny",
        channel=ChannelType.PUSH,
        category=NotificationCategory.STREAK_RISK,
        created_at="2026-10-13T11:00:00Z",
    )
    res_0700 = dispatcher.dispatch(prop_0700, prefs=prefs_ny, now_utc="2026-10-13T11:00:00Z")
    # Exactly 07:00 is outside quiet hours -> immediate dispatch
    assert res_0700.status == NotificationStatus.DISPATCHED
    assert len(push.delivered_messages) == 1

    # 3. Fall-back DST transition date (2026-11-01 in America/New_York)
    prefs_dst = LearnerPreferences(learner_id="L_dst", timezone="America/New_York")
    prop_dst = NotificationProposal(
        proposal_id="p_dst",
        learner_id="L_dst",
        channel=ChannelType.PUSH,
        category=NotificationCategory.STREAK_RISK,
        created_at="2026-11-01T03:00:00Z",  # 23:00 local EDT
    )
    res_dst = dispatcher.dispatch(prop_dst, prefs=prefs_dst, now_utc="2026-11-01T03:00:00Z")
    assert res_dst.status == NotificationStatus.QUEUED
    # Target morning 07:00 EST is 12:00 UTC (standard time UTC-5)
    assert res_dst.rescheduled_for == "2026-11-01T12:00:00Z"

    store.close()
