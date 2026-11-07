"""Quiet hours 22:00-08:00 (learner local time), using the DEFAULT arguments.

Inside quiet hours means local hour >= 22 or < 8: 22:00:00 is inside, 08:00:00 is outside.
Nothing here passes quiet_start_hour / quiet_end_hour explicitly.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

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
from src.notifications.store import NotificationStore


def _utc(tz_name, year, month, day, hour, minute=0, second=0):
    """The UTC instant at which the given local wall-clock time occurs in tz_name."""
    local = datetime(year, month, day, hour, minute, second, tzinfo=ZoneInfo(tz_name))
    return local.astimezone(timezone.utc)


@pytest.mark.parametrize("tz_name", ["Asia/Kolkata", "America/New_York"])
@pytest.mark.parametrize(
    "hour, minute, second, expected_quiet",
    [
        (7, 30, 0, True),    # before 08:00
        (7, 59, 59, True),
        (8, 0, 0, False),    # exactly 08:00 is outside
        (21, 59, 0, False),
        (22, 0, 0, True),    # exactly 22:00 is inside
        (23, 10, 0, True),
        (3, 0, 0, True),
    ],
)
def test_default_quiet_hours_window_is_22_to_08(tz_name, hour, minute, second, expected_quiet):
    now = _utc(tz_name, 2026, 10, 14, hour, minute, second)
    is_quiet, _ = evaluate_quiet_hours(now, tz_name)  # defaults only
    assert is_quiet is expected_quiet


@pytest.mark.parametrize("tz_name", ["Asia/Kolkata", "America/New_York"])
def test_default_quiet_hours_next_dispatch_is_08_local(tz_name):
    now = _utc(tz_name, 2026, 10, 14, 23, 10)
    is_quiet, next_dispatch = evaluate_quiet_hours(now, tz_name)
    assert is_quiet is True
    assert next_dispatch == _utc(tz_name, 2026, 10, 15, 8, 0)

    early = _utc(tz_name, 2026, 10, 14, 7, 30)  # same-day morning, before 08:00
    is_quiet, next_dispatch = evaluate_quiet_hours(early, tz_name)
    assert is_quiet is True
    assert next_dispatch == _utc(tz_name, 2026, 10, 14, 8, 0)


def test_dispatcher_with_default_arguments_reschedules_to_08_local(tmp_path):
    store = NotificationStore(str(tmp_path / "defaults.db"))
    push = SimulatedPushAdapter()
    dispatcher = NotificationDispatcher(store=store, adapters={ChannelType.PUSH: push})  # defaults
    prefs = LearnerPreferences(learner_id="L1", timezone="Asia/Kolkata")

    # 23:10 IST on 2026-10-12 is 17:40 UTC
    now = "2026-10-12T17:40:00Z"
    prop = NotificationProposal(
        proposal_id="p_default_quiet",
        learner_id="L1",
        channel=ChannelType.PUSH,
        category=NotificationCategory.STREAK_RISK,
        created_at=now,
        ttl_seconds=86400,
    )
    res = dispatcher.dispatch(prop, prefs=prefs, now_utc=now)

    assert res.status == NotificationStatus.QUEUED
    assert res.reason == "quiet_hours_rescheduled"
    # 08:00 IST on 2026-10-13 is 02:30 UTC (07:00 IST would be 01:30 UTC)
    assert res.rescheduled_for == "2026-10-13T02:30:00Z"
    assert len(push.delivered_messages) == 0
    store.close()


def test_dispatcher_with_default_arguments_holds_a_07_30_local_message(tmp_path):
    store = NotificationStore(str(tmp_path / "defaults_morning.db"))
    push = SimulatedPushAdapter()
    dispatcher = NotificationDispatcher(store=store, adapters={ChannelType.PUSH: push})
    prefs = LearnerPreferences(learner_id="L2", timezone="Asia/Kolkata")

    # 07:30 IST on 2026-10-13 is 02:00 UTC: still quiet, so not delivered now
    now = "2026-10-13T02:00:00Z"
    prop = NotificationProposal(
        proposal_id="p_default_morning",
        learner_id="L2",
        channel=ChannelType.PUSH,
        category=NotificationCategory.STREAK_RISK,
        created_at=now,
    )
    res = dispatcher.dispatch(prop, prefs=prefs, now_utc=now)

    assert res.status == NotificationStatus.QUEUED
    assert res.rescheduled_for == "2026-10-13T02:30:00Z"  # 08:00 IST
    assert len(push.delivered_messages) == 0
    store.close()
