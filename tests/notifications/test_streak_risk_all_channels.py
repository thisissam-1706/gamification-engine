"""Each scheduled streak-risk reminder goes out on in-app, push and email at the same time, with a
separate daily cap of 5 for each channel; the experiment report counts the cap per channel too."""

import json

import pytest

from src.experiments.prefs import load_preferences
from src.notifications.channels.email import SimulatedEmailAdapter
from src.notifications.channels.in_app import InAppChannelAdapter
from src.notifications.dispatcher import NotificationDispatcher
from src.notifications.models import ChannelType, NotificationStatus
from src.notifications.store import NotificationStore
from src.notifications.streak_risk import REMINDER_TIMES_LOCAL, run_streak_risk_sweep
from tests.experiments.support import (
    RecordingPush,
    ist,
    open_persisted,
    persist_learners,
    streak_learner,
    streak_prefs,
)

ADDRESS = "learner@example.com"


def _dispatcher(tmp_path):
    store = NotificationStore(str(tmp_path / "notif.db"))
    adapters = {
        ChannelType.IN_APP: InAppChannelAdapter(),
        ChannelType.PUSH: RecordingPush(),
        ChannelType.EMAIL: SimulatedEmailAdapter(),
    }
    return NotificationDispatcher(store, adapters=adapters), store


def _sent(store, learner_id):
    rows = store.connection.execute(
        "SELECT proposal_id, channel, dispatched_at FROM notification_dispatches WHERE learner_id = ?",
        (learner_id,),
    ).fetchall()
    return sorted(rows)


def test_one_reminder_goes_out_on_all_three_channels_at_the_same_time(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        learner = streak_learner("variant_a")
        persist_learners(application, learner)
        dispatcher, store = _dispatcher(tmp_path)
        try:
            lid = str(learner.id)
            now = ist(20, 5)
            result = run_streak_risk_sweep(
                application, dispatcher, [lid], {lid: streak_prefs(learner, email=ADDRESS)}, now_utc=now
            )
            fact = result.outcomes[0].fact
            assert fact.status == NotificationStatus.DISPATCHED and fact.channel == "push"
            rows = _sent(store, lid)
            assert {channel for _, channel, _ in rows} == {"in_app", "push", "email"}
            assert len({at for _, _, at in rows}) == 1  # same instant on every channel
            push_id = fact.proposal_id
            assert {pid for pid, _, _ in rows} == {push_id, f"{push_id}:in_app", f"{push_id}:email"}
            email = dispatcher.adapters[ChannelType.EMAIL].delivered_messages[0]
            assert email["to"] == ADDRESS
        finally:
            store.close()


def test_without_an_email_address_only_in_app_and_push_are_sent(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        learner = streak_learner("variant_a")
        persist_learners(application, learner)
        dispatcher, store = _dispatcher(tmp_path)
        try:
            lid = str(learner.id)
            run_streak_risk_sweep(application, dispatcher, [lid], {lid: streak_prefs(learner)}, now_utc=ist(20, 5))
            assert {channel for _, channel, _ in _sent(store, lid)} == {"in_app", "push"}
        finally:
            store.close()


def test_every_scheduled_time_sends_three_and_each_channel_stays_under_its_own_cap(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        learner = streak_learner("variant_a")
        persist_learners(application, learner)
        dispatcher, store = _dispatcher(tmp_path)
        try:
            lid = str(learner.id)
            prefs = {lid: streak_prefs(learner, email=ADDRESS)}
            for hhmm in REMINDER_TIMES_LOCAL:
                hour, minute = (int(part) for part in hhmm.split(":"))
                run_streak_risk_sweep(application, dispatcher, [lid], prefs, now_utc=ist(hour, minute))
            rows = _sent(store, lid)
            per_channel = {ch: sum(1 for _, c, _ in rows if c == ch) for ch in ("in_app", "push", "email")}
            assert per_channel == {"in_app": 4, "push": 4, "email": 4}  # 12 in total, 4 per channel
        finally:
            store.close()


def test_a_channel_the_learner_opted_out_of_is_not_sent(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        learner = streak_learner("variant_a")
        persist_learners(application, learner)
        dispatcher, store = _dispatcher(tmp_path)
        try:
            lid = str(learner.id)
            prefs = streak_prefs(learner, email=ADDRESS, opted_out_channels={ChannelType.EMAIL})
            run_streak_risk_sweep(application, dispatcher, [lid], {lid: prefs}, now_utc=ist(20, 5))
            assert {channel for _, channel, _ in _sent(store, lid)} == {"in_app", "push"}
        finally:
            store.close()


def test_preferences_file_accepts_an_email_address(tmp_path):
    path = tmp_path / "prefs.json"
    path.write_text(json.dumps({"L1": {"timezone": "UTC", "email": ADDRESS}, "L2": {}}), encoding="utf-8")
    prefs = load_preferences(path)
    assert prefs["L1"].email == ADDRESS
    assert prefs["L2"].email is None


@pytest.mark.parametrize("bad", ["not-an-address", 5, ""])
def test_preferences_file_rejects_a_bad_email(tmp_path, bad):
    path = tmp_path / "prefs.json"
    path.write_text(json.dumps({"L1": {"email": bad}}), encoding="utf-8")
    with pytest.raises(ValueError, match="email"):
        load_preferences(path)
