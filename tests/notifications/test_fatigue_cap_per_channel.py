"""Per-channel daily cap (the default): 5 in-app, 5 push and 5 email per day, each counted on its
own. cap_per_channel=False is the combined option: 5 across all channels together."""

import pytest

from src.notifications.channels.email import SimulatedEmailAdapter
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

CHANNELS = (ChannelType.IN_APP, ChannelType.PUSH, ChannelType.EMAIL)


def _dispatcher(tmp_path, per_channel):
    store = NotificationStore(str(tmp_path / "notif.db"))
    adapters = {
        ChannelType.IN_APP: InAppChannelAdapter(),
        ChannelType.PUSH: SimulatedPushAdapter(),
        ChannelType.EMAIL: SimulatedEmailAdapter(),
    }
    return store, NotificationDispatcher(store, adapters=adapters, fatigue_cap=5, cap_per_channel=per_channel)


def _send(dispatcher, channel, i, minute):
    prop = NotificationProposal(
        proposal_id=f"{channel.value}_{i}",
        learner_id="L1",
        channel=channel,
        category=NotificationCategory.STREAK_RISK,
        template_id="streak_warning_v1",
        payload={"to": "l1@learner.example", "i": i},
    )
    return dispatcher.dispatch(
        prop, prefs=LearnerPreferences(learner_id="L1"), now_utc=f"2026-10-12T10:{minute:02d}:00Z"
    )


def test_each_channel_gets_its_own_five(tmp_path):
    store, dispatcher = _dispatcher(tmp_path, per_channel=True)
    minute = 0
    results = {}
    for channel in CHANNELS:
        for i in range(6):
            minute += 1
            results[(channel, i)] = _send(dispatcher, channel, i, minute)
    for channel in CHANNELS:
        assert [results[(channel, i)].status for i in range(5)] == [NotificationStatus.DISPATCHED] * 5
        assert results[(channel, 5)].status == NotificationStatus.SUPPRESSED
        assert results[(channel, 5)].reason == "daily_cap_exceeded"
    store.close()


def test_per_channel_cap_resets_at_local_midnight(tmp_path):
    store, dispatcher = _dispatcher(tmp_path, per_channel=True)
    for i in range(5):
        _send(dispatcher, ChannelType.PUSH, i, i)
    prop = NotificationProposal(
        proposal_id="next_day", learner_id="L1", channel=ChannelType.PUSH,
        category=NotificationCategory.STREAK_RISK, template_id="streak_warning_v1", payload={},
    )
    res = dispatcher.dispatch(prop, prefs=LearnerPreferences(learner_id="L1"), now_utc="2026-10-13T10:00:00Z")
    assert res.status == NotificationStatus.DISPATCHED
    store.close()


@pytest.mark.parametrize("per_channel, delivered", [(False, 5), (True, 9)])
def test_default_cap_still_counts_all_channels(tmp_path, per_channel, delivered):
    store, dispatcher = _dispatcher(tmp_path, per_channel=per_channel)
    statuses = []
    for i in range(9):
        channel = CHANNELS[i % 3]
        statuses.append(_send(dispatcher, channel, i, i).status)
    assert statuses.count(NotificationStatus.DISPATCHED) == delivered
    store.close()
