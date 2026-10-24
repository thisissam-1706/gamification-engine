"""AT-14: Notification Fatigue Guardrail (M5)

Given L1 has already received 5 notifications in the current 24h window, when a 6th candidate is generated, then it is blocked and the triggering experiment variant is flagged for guardrail violation.
"""

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
from src.notifications.store import NotificationStore


def test_notification_fatigue(tmp_path):
    store = NotificationStore(str(tmp_path / "notif.db"))
    push = SimulatedPushAdapter()
    dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.PUSH: push},
        fatigue_cap=5,
    )
    prefs = LearnerPreferences(learner_id="L1")

    # Generate and dispatch 5 notifications within the 24h window
    for i in range(1, 6):
        prop = NotificationProposal(
            proposal_id=f"prop_{i}",
            learner_id="L1",
            channel=ChannelType.PUSH,
            category=NotificationCategory.STREAK_RISK,
            template_id="streak_warning_v1",
            payload={"msg_index": i},
        )
        res = dispatcher.dispatch(prop, prefs=prefs, now_utc=f"2026-10-12T10:0{i}:00Z")
        assert res.status == NotificationStatus.DISPATCHED

    assert len(push.delivered_messages) == 5

    # 6th candidate is blocked by fatigue cap
    prop_6 = NotificationProposal(
        proposal_id="prop_6",
        learner_id="L1",
        channel=ChannelType.PUSH,
        category=NotificationCategory.STREAK_RISK,
        template_id="streak_warning_v1",
        payload={"msg_index": 6},
    )
    res_6 = dispatcher.dispatch(prop_6, prefs=prefs, now_utc="2026-10-12T10:15:00Z")
    assert res_6.status == NotificationStatus.SUPPRESSED
    assert res_6.reason == "daily_cap_exceeded"

    # Delivery count remains 5
    assert len(push.delivered_messages) == 5

    store.close()


def test_notification_fatigue_cross_channel_combined_cap(tmp_path):
    """Proves F2: Fatigue cap applies across all channels combined (e.g. 3 push + 2 in-app = 5)."""
    store = NotificationStore(str(tmp_path / "notif_cross.db"))
    push = SimulatedPushAdapter()
    in_app = InAppChannelAdapter()
    dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.PUSH: push, ChannelType.IN_APP: in_app},
        fatigue_cap=5,
    )
    prefs = LearnerPreferences(learner_id="L2")

    # 3 Push notifications
    for i in range(1, 4):
        p = NotificationProposal(
            proposal_id=f"push_{i}",
            learner_id="L2",
            channel=ChannelType.PUSH,
            category=NotificationCategory.STREAK_RISK,
            payload={"i": i},
        )
        res = dispatcher.dispatch(p, prefs=prefs, now_utc=f"2026-10-12T12:0{i}:00Z")
        assert res.status == NotificationStatus.DISPATCHED

    # 2 In-App notifications
    for i in range(1, 3):
        p = NotificationProposal(
            proposal_id=f"inapp_{i}",
            learner_id="L2",
            channel=ChannelType.IN_APP,
            category=NotificationCategory.SOCIAL_NUDGE,
            payload={"i": i},
        )
        res = dispatcher.dispatch(p, prefs=prefs, now_utc=f"2026-10-12T12:1{i}:00Z")
        assert res.status == NotificationStatus.DISPATCHED

    assert len(push.delivered_messages) == 3
    assert len(in_app.delivered_messages) == 2

    # 6th candidate on In-App channel is blocked by combined cap
    p_6 = NotificationProposal(
        proposal_id="inapp_3",
        learner_id="L2",
        channel=ChannelType.IN_APP,
        category=NotificationCategory.SOCIAL_NUDGE,
        payload={"i": 6},
    )
    res_6 = dispatcher.dispatch(p_6, prefs=prefs, now_utc="2026-10-12T12:20:00Z")
    assert res_6.status == NotificationStatus.SUPPRESSED
    assert res_6.reason == "daily_cap_exceeded"

    # Delivery counts remain unchanged
    assert len(push.delivered_messages) == 3
    assert len(in_app.delivered_messages) == 2
    store.close()


def test_notification_fatigue_window_expiration(tmp_path):
    """Proves messages older than 24h roll out of the fatigue window."""
    store = NotificationStore(str(tmp_path / "notif_win.db"))
    in_app = InAppChannelAdapter()
    dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.IN_APP: in_app},
        fatigue_cap=5,
    )
    prefs = LearnerPreferences(learner_id="L3")

    # 5 notifications sent yesterday at 08:00
    for i in range(1, 6):
        p = NotificationProposal(
            proposal_id=f"old_{i}",
            learner_id="L3",
            channel=ChannelType.IN_APP,
            category=NotificationCategory.SOCIAL_NUDGE,
            payload={"i": i},
        )
        res = dispatcher.dispatch(p, prefs=prefs, now_utc=f"2026-10-11T08:0{i}:00Z")
        assert res.status == NotificationStatus.DISPATCHED

    assert len(in_app.delivered_messages) == 5

    # 25 hours later (2026-10-12T09:00:00Z), new notification is allowed
    new_p = NotificationProposal(
        proposal_id="new_1",
        learner_id="L3",
        channel=ChannelType.IN_APP,
        category=NotificationCategory.SOCIAL_NUDGE,
        payload={"i": 10},
    )
    res_new = dispatcher.dispatch(new_p, prefs=prefs, now_utc="2026-10-12T09:00:00Z")
    assert res_new.status == NotificationStatus.DISPATCHED
    assert len(in_app.delivered_messages) == 6
    store.close()
