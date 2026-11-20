"""The daily notification cap resets at the learner's local midnight (not a rolling 24 h)."""

from datetime import datetime, timezone

from src.experiments.fatigue import max_in_local_day
from src.notifications.dispatcher import NotificationDispatcher
from src.notifications.guardrails import daily_cap_usage, local_day_start
from src.notifications.models import (
    ChannelType,
    LearnerPreferences,
    NotificationCategory,
    NotificationProposal,
    NotificationStatus,
)
from src.notifications.store import NotificationStore


def _proposal(pid: str, learner: str, at: str) -> NotificationProposal:
    return NotificationProposal(
        proposal_id=pid, learner_id=learner, channel=ChannelType.IN_APP,
        category=NotificationCategory.GENERAL, payload={"title": "t", "body": "b"}, created_at=at,
    )


def _send(dispatcher, pid, learner, at, tz="UTC"):
    return dispatcher.dispatch(
        _proposal(pid, learner, at), prefs=LearnerPreferences(learner, timezone=tz), now_utc=at
    )


def test_cap_blocks_the_sixth_message_of_the_day_and_resets_next_morning(tmp_path):
    store = NotificationStore(str(tmp_path / "n.db"))
    dispatcher = NotificationDispatcher(store=store)
    for i in range(5):
        assert _send(dispatcher, f"d1_{i}", "L", "2026-10-16T18:00:00Z").status == NotificationStatus.DISPATCHED
    blocked = _send(dispatcher, "d1_6", "L", "2026-10-16T18:00:00Z")
    assert (blocked.status, blocked.reason) == (NotificationStatus.SUPPRESSED, "daily_cap_exceeded")
    # 15 hours later is a new local day: the cap starts again (a rolling 24 h would still block).
    assert _send(dispatcher, "d2_1", "L", "2026-10-17T09:00:00Z").status == NotificationStatus.DISPATCHED
    store.close()


def test_cap_day_follows_the_learner_timezone(tmp_path):
    """Asia/Kolkata midnight is 18:30 UTC the day before."""
    store = NotificationStore(str(tmp_path / "n.db"))
    dispatcher = NotificationDispatcher(store=store, quiet_start_hour=3, quiet_end_hour=4)
    tz = "Asia/Kolkata"
    for i in range(5):  # 23:00 IST on Oct 16
        assert _send(dispatcher, f"a{i}", "L", "2026-10-16T17:30:00Z", tz).status == NotificationStatus.DISPATCHED
    assert _send(dispatcher, "a6", "L", "2026-10-16T18:29:00Z", tz).reason == "daily_cap_exceeded"  # 23:59 IST
    assert _send(dispatcher, "b1", "L", "2026-10-16T18:31:00Z", tz).status == NotificationStatus.DISPATCHED  # 00:01 IST
    store.close()


def test_dispatches_after_now_do_not_count(tmp_path):
    """A demo clock moved backwards must not be blocked by messages stamped in its future."""
    store = NotificationStore(str(tmp_path / "n.db"))
    dispatcher = NotificationDispatcher(store=store)
    for i in range(5):
        _send(dispatcher, f"late_{i}", "L", "2026-10-16T18:00:00Z")
    assert _send(dispatcher, "early", "L", "2026-10-16T09:00:00Z").status == NotificationStatus.DISPATCHED
    store.close()


def test_daily_cap_usage_reports_count_and_next_reset():
    now = datetime(2026, 10, 16, 18, 0, tzinfo=timezone.utc)
    used, start, reset = daily_cap_usage(
        ["2026-10-15T23:00:00Z", "2026-10-16T09:00:00Z", "2026-10-16T17:59:59Z", "2026-10-16T19:00:00Z"],
        now, "UTC",
    )
    assert used == 2
    assert start == datetime(2026, 10, 16, tzinfo=timezone.utc)
    assert reset == datetime(2026, 10, 17, tzinfo=timezone.utc)
    assert local_day_start(now, "not/a_zone") == datetime(2026, 10, 16, tzinfo=timezone.utc)


def test_runner_breach_metric_counts_per_local_day():
    evening = [datetime(2026, 10, 16, 20, m, tzinfo=timezone.utc) for m in range(5)]
    morning = [datetime(2026, 10, 17, 8, m, tzinfo=timezone.utc) for m in range(5)]
    assert max_in_local_day(evening + morning, "UTC") == 5  # within the cap on both days
    # In IST both bursts fall on Oct 17 (01:30 and 13:30 local), so they share one day.
    assert max_in_local_day(evening + morning, "Asia/Kolkata") == 10
    assert max_in_local_day([], "UTC") == 0
