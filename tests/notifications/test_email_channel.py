"""Simulated email channel (M5).

Email shares the guardrails of every other channel: the 5-per-24h cap (per learner, all channels),
quiet hours 22:00-08:00 in the learner's timezone, and per-channel consent. The recipient is read
from payload["to"]; a missing recipient is a permanent provider failure.
"""

import pytest

from src.notifications.channels.email import SimulatedEmailAdapter
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

NOW = "2026-10-12T10:00:00Z"


def _proposal(proposal_id, channel, learner_id="L1", payload=None, created_at=NOW,
              category=NotificationCategory.STREAK_RISK):
    """The existing notification tests build proposals inline; this is the same shape in one place."""
    return NotificationProposal(
        proposal_id=proposal_id,
        learner_id=learner_id,
        channel=channel,
        category=category,
        template_id="streak_warning_v1",
        payload={"to": "learner@example.com"} if payload is None else payload,
        created_at=created_at,
    )


def _at(minute):
    return f"2026-10-12T10:{minute:02d}:00Z"


def test_default_dispatcher_delivers_an_email(tmp_path):
    store = NotificationStore(str(tmp_path / "email_default.db"))
    dispatcher = NotificationDispatcher(store=store)  # default adapters, default guardrails
    prefs = LearnerPreferences(learner_id="L1")

    res = dispatcher.dispatch(_proposal("p_email_1", ChannelType.EMAIL), prefs=prefs, now_utc=NOW)

    assert res.status == NotificationStatus.DISPATCHED
    assert res.channel == "email"
    rows = store.connection.execute(
        "SELECT channel FROM notification_dispatches WHERE learner_id = 'L1'"
    ).fetchall()
    assert rows == [("email",)]
    email = dispatcher.adapters[ChannelType.EMAIL]
    assert email.delivered_messages == [{
        "proposal_id": "p_email_1",
        "learner_id": "L1",
        "category": "streak_risk",
        "payload": {"to": "learner@example.com"},
        "to": "learner@example.com",
    }]
    store.close()


@pytest.mark.parametrize("first, second, extra_first, extra_second", [
    # 3 push + 2 email fill the cap; a 6th message (email) is suppressed.
    (ChannelType.PUSH, ChannelType.EMAIL, 3, 2),
    # 5 emails fill the cap; a push is suppressed.
    (ChannelType.EMAIL, ChannelType.PUSH, 5, 0),
])
def test_cap_is_shared_between_push_and_email(tmp_path, first, second, extra_first, extra_second):
    store = NotificationStore(str(tmp_path / "email_cap.db"))
    push, email = SimulatedPushAdapter(), SimulatedEmailAdapter()
    dispatcher = NotificationDispatcher(
        store=store, adapters={ChannelType.PUSH: push, ChannelType.EMAIL: email}, fatigue_cap=5,
    )
    prefs = LearnerPreferences(learner_id="L1")

    minute = 0
    for channel, count in ((first, extra_first), (second, extra_second)):
        for _ in range(count):
            minute += 1
            res = dispatcher.dispatch(
                _proposal(f"p_{minute}", channel), prefs=prefs, now_utc=_at(minute)
            )
            assert res.status == NotificationStatus.DISPATCHED
    assert len(push.delivered_messages) + len(email.delivered_messages) == 5

    # The sixth message, on the channel that is not yet "full" on its own, is blocked.
    sixth_channel = ChannelType.EMAIL if first == ChannelType.PUSH else ChannelType.PUSH
    res_6 = dispatcher.dispatch(_proposal("p_6", sixth_channel), prefs=prefs, now_utc=_at(30))
    assert res_6.status == NotificationStatus.SUPPRESSED
    assert res_6.reason == "daily_cap_exceeded"
    assert len(push.delivered_messages) + len(email.delivered_messages) == 5
    store.close()


def test_email_in_quiet_hours_is_rescheduled_to_08_local_and_released_then(tmp_path):
    store = NotificationStore(str(tmp_path / "email_quiet.db"))
    dispatcher = NotificationDispatcher(store=store)  # default quiet hours
    prefs = LearnerPreferences(learner_id="L1", timezone="Asia/Kolkata")

    # 23:10 IST on 2026-10-12 is 17:40 UTC
    generated = "2026-10-12T17:40:00Z"
    res = dispatcher.dispatch(
        _proposal("p_email_quiet", ChannelType.EMAIL, created_at=generated),
        prefs=prefs, now_utc=generated,
    )
    email = dispatcher.adapters[ChannelType.EMAIL]
    assert res.status == NotificationStatus.QUEUED
    assert res.reason == "quiet_hours_rescheduled"
    assert res.rescheduled_for == "2026-10-13T02:30:00Z"  # 08:00 IST
    assert email.delivered_messages == []

    released = dispatcher.release_rescheduled(
        now_utc=res.rescheduled_for, prefs_provider={"L1": prefs},
    )
    assert len(released) == 1
    assert released[0].status == NotificationStatus.DISPATCHED
    assert released[0].channel == "email"
    assert [m["proposal_id"] for m in email.delivered_messages] == ["p_email_quiet"]
    assert email.delivered_messages[0]["to"] == "learner@example.com"
    store.close()


def test_email_opt_out_is_per_channel(tmp_path):
    store = NotificationStore(str(tmp_path / "email_optout.db"))
    push, email = SimulatedPushAdapter(), SimulatedEmailAdapter()
    dispatcher = NotificationDispatcher(
        store=store, adapters={ChannelType.PUSH: push, ChannelType.EMAIL: email},
    )
    prefs = LearnerPreferences(learner_id="L1", opted_out_channels={ChannelType.EMAIL})

    res_email = dispatcher.dispatch(_proposal("p_email", ChannelType.EMAIL), prefs=prefs, now_utc=NOW)
    assert res_email.status == NotificationStatus.SUPPRESSED
    assert res_email.reason == "opt_out"
    assert email.attempts_seen == 0
    assert email.delivered_messages == []

    res_push = dispatcher.dispatch(_proposal("p_push", ChannelType.PUSH), prefs=prefs, now_utc=_at(1))
    assert res_push.status == NotificationStatus.DISPATCHED
    assert len(push.delivered_messages) == 1
    store.close()


def test_email_transient_failure_succeeds_after_a_retry(tmp_path):
    store = NotificationStore(str(tmp_path / "email_retry.db"))
    email = SimulatedEmailAdapter(fail_attempts=1)
    dispatcher = NotificationDispatcher(
        store=store, adapters={ChannelType.EMAIL: email},
        retry_scheduler=RetryScheduler(max_retries=3, backoff_delay_sec=0.0),
    )

    res = dispatcher.dispatch(
        _proposal("p_email_retry", ChannelType.EMAIL), prefs=LearnerPreferences(learner_id="L1"), now_utc=NOW,
    )

    assert res.status == NotificationStatus.DISPATCHED
    assert res.attempt_count == 2
    assert email.attempts_seen == 2
    assert len(email.delivered_messages) == 1
    assert store.get_dlq_entries() == []
    store.close()


def test_email_permanent_failure_goes_to_dlq_without_retry(tmp_path):
    store = NotificationStore(str(tmp_path / "email_perm.db"))
    email = SimulatedEmailAdapter(fail_permanently=True)
    dispatcher = NotificationDispatcher(
        store=store, adapters={ChannelType.EMAIL: email},
        retry_scheduler=RetryScheduler(max_retries=3, backoff_delay_sec=0.0),
    )

    res = dispatcher.dispatch(
        _proposal("p_email_perm", ChannelType.EMAIL), prefs=LearnerPreferences(learner_id="L1"), now_utc=NOW,
    )

    assert res.status == NotificationStatus.FAILED
    assert res.attempt_count == 1
    assert email.attempts_seen == 1
    assert "permanent_failure" in (res.reason or "")
    dlq = store.get_dlq_entries()
    assert [(e["proposal_id"], e["channel"], e["attempts"]) for e in dlq] == [("p_email_perm", "email", 1)]
    assert email.delivered_messages == []
    store.close()


def test_email_without_a_recipient_is_a_permanent_failure(tmp_path):
    store = NotificationStore(str(tmp_path / "email_no_to.db"))
    email = SimulatedEmailAdapter()
    dispatcher = NotificationDispatcher(
        store=store, adapters={ChannelType.EMAIL: email},
        retry_scheduler=RetryScheduler(max_retries=3, backoff_delay_sec=0.0),
    )

    res = dispatcher.dispatch(
        _proposal("p_email_no_to", ChannelType.EMAIL, payload={"subject": "no recipient"}),
        prefs=LearnerPreferences(learner_id="L1"), now_utc=NOW,
    )

    assert res.status == NotificationStatus.FAILED
    assert res.attempt_count == 1  # permanent: not retried
    assert "permanent_failure" in (res.reason or "")
    assert email.delivered_messages == []
    dlq = store.get_dlq_entries()
    assert [(e["proposal_id"], e["channel"]) for e in dlq] == [("p_email_no_to", "email")]
    store.close()


# --- Edge cases ---------------------------------------------------------------


def test_cap_boundary_with_mixed_channels_fifth_email_delivered_sixth_suppressed(tmp_path):
    store = NotificationStore(str(tmp_path / "email_boundary.db"))
    dispatcher = NotificationDispatcher(store=store)  # default adapters: in_app, push, email
    prefs = LearnerPreferences(learner_id="L1")

    prior = [ChannelType.PUSH, ChannelType.IN_APP, ChannelType.EMAIL, ChannelType.PUSH]
    for minute, channel in enumerate(prior, start=1):
        res = dispatcher.dispatch(_proposal(f"p_prior_{minute}", channel), prefs=prefs, now_utc=_at(minute))
        assert res.status == NotificationStatus.DISPATCHED

    fifth = dispatcher.dispatch(_proposal("p_email_5", ChannelType.EMAIL), prefs=prefs, now_utc=_at(10))
    assert fifth.status == NotificationStatus.DISPATCHED
    sixth = dispatcher.dispatch(_proposal("p_email_6", ChannelType.EMAIL), prefs=prefs, now_utc=_at(11))
    assert sixth.status == NotificationStatus.SUPPRESSED
    assert sixth.reason == "daily_cap_exceeded"
    assert [m["proposal_id"] for m in dispatcher.adapters[ChannelType.EMAIL].delivered_messages] == [
        "p_prior_3", "p_email_5",
    ]
    store.close()


def test_cap_window_edge_a_dispatch_exactly_24h_old_still_counts(tmp_path):
    """check_fatigue_cap counts timestamps >= now - 24h (inclusive), and so does the store query:
    a dispatch exactly 24h old counts towards the cap, one 24h + 1s old does not."""
    store = NotificationStore(str(tmp_path / "email_window.db"))
    dispatcher = NotificationDispatcher(store=store)
    now = "2026-10-13T12:00:00Z"
    cases = {
        "L_exact": "2026-10-12T12:00:00Z",       # exactly 24h before now: inside the window
        "L_just_outside": "2026-10-12T11:59:59Z",  # 24h + 1s before now: outside the window
    }
    for learner, old_time in cases.items():
        prefs = LearnerPreferences(learner_id=learner)
        res = dispatcher.dispatch(
            _proposal(f"{learner}_old", ChannelType.PUSH, learner_id=learner, created_at=old_time),
            prefs=prefs, now_utc=old_time,
        )
        assert res.status == NotificationStatus.DISPATCHED
        for i in range(1, 5):  # four recent dispatches, so 5 in window only if the old one counts
            recent = f"2026-10-13T10:0{i}:00Z"
            res = dispatcher.dispatch(
                _proposal(f"{learner}_recent_{i}", ChannelType.PUSH, learner_id=learner, created_at=recent),
                prefs=prefs, now_utc=recent,
            )
            assert res.status == NotificationStatus.DISPATCHED

    exact = dispatcher.dispatch(
        _proposal("exact_email", ChannelType.EMAIL, learner_id="L_exact", created_at=now),
        prefs=LearnerPreferences(learner_id="L_exact"), now_utc=now,
    )
    assert exact.status == NotificationStatus.SUPPRESSED
    assert exact.reason == "daily_cap_exceeded"

    outside = dispatcher.dispatch(
        _proposal("outside_email", ChannelType.EMAIL, learner_id="L_just_outside", created_at=now),
        prefs=LearnerPreferences(learner_id="L_just_outside"), now_utc=now,
    )
    assert outside.status == NotificationStatus.DISPATCHED
    store.close()


def test_email_quiet_hour_boundaries_with_default_arguments(tmp_path):
    store = NotificationStore(str(tmp_path / "email_boundaries.db"))
    dispatcher = NotificationDispatcher(store=store)  # defaults: quiet 22:00-08:00 local
    # Asia/Kolkata is UTC+5:30 with no DST.
    cases = [
        ("2026-10-12T16:29:00Z", "delivered", None),                   # 21:59 IST
        ("2026-10-12T16:30:00Z", "queued", "2026-10-13T02:30:00Z"),    # 22:00:00 IST
        ("2026-10-13T02:29:59Z", "queued", "2026-10-13T02:30:00Z"),    # 07:59:59 IST
        ("2026-10-13T02:30:00Z", "delivered", None),                   # 08:00:00 IST
    ]
    for i, (now, expected, rescheduled_for) in enumerate(cases):
        learner = f"L_{i}"  # one learner per case, so the cap never interferes
        res = dispatcher.dispatch(
            _proposal(f"p_{i}", ChannelType.EMAIL, learner_id=learner, created_at=now),
            prefs=LearnerPreferences(learner_id=learner, timezone="Asia/Kolkata"), now_utc=now,
        )
        if expected == "delivered":
            assert res.status == NotificationStatus.DISPATCHED, now
        else:
            assert res.status == NotificationStatus.QUEUED, now
            assert res.rescheduled_for == rescheduled_for, now
    assert [m["proposal_id"] for m in dispatcher.adapters[ChannelType.EMAIL].delivered_messages] == ["p_0", "p_3"]
    store.close()


def test_release_of_a_queued_email_rechecks_the_cap(tmp_path):
    store = NotificationStore(str(tmp_path / "email_release_cap.db"))
    dispatcher = NotificationDispatcher(store=store)
    prefs = LearnerPreferences(learner_id="L1", timezone="Asia/Kolkata")

    queued_at = "2026-10-12T17:40:00Z"  # 23:10 IST
    res = dispatcher.dispatch(
        _proposal("p_email_q", ChannelType.EMAIL, created_at=queued_at), prefs=prefs, now_utc=queued_at,
    )
    assert res.status == NotificationStatus.QUEUED
    assert res.rescheduled_for == "2026-10-13T02:30:00Z"

    # Morning: five pushes use up the cap before the release job runs (08:00 IST is outside quiet hours).
    for i in range(5):
        pushed = dispatcher.dispatch(
            _proposal(f"p_push_{i}", ChannelType.PUSH, created_at="2026-10-13T02:30:00Z"),
            prefs=prefs, now_utc=f"2026-10-13T02:30:0{i}Z",
        )
        assert pushed.status == NotificationStatus.DISPATCHED

    # release_rescheduled re-checks the cap across all channels and suppresses (as it does for push).
    facts = dispatcher.release_rescheduled(now_utc="2026-10-13T02:31:00Z", prefs_provider={"L1": prefs})
    assert len(facts) == 1
    assert facts[0].proposal_id == "p_email_q"
    assert facts[0].channel == "email"
    assert facts[0].status == NotificationStatus.SUPPRESSED
    assert facts[0].reason == "daily_cap_exceeded"
    assert dispatcher.adapters[ChannelType.EMAIL].delivered_messages == []
    assert store.get_rescheduled_entry("p_email_q")["released"] is True
    store.close()


def test_replay_dlq_for_email_succeeds_once_the_adapter_works_and_respects_the_cap(tmp_path):
    store = NotificationStore(str(tmp_path / "email_replay.db"))
    prefs = LearnerPreferences(learner_id="L1")
    broken = NotificationDispatcher(
        store=store, adapters={ChannelType.EMAIL: SimulatedEmailAdapter(fail_permanently=True)},
    )
    for name in ("p_email_a", "p_email_b"):
        res = broken.dispatch(_proposal(name, ChannelType.EMAIL), prefs=prefs, now_utc=NOW)
        assert res.status == NotificationStatus.FAILED
    dlq_ids = {e["proposal_id"]: e["dlq_id"] for e in store.get_dlq_entries()}
    assert set(dlq_ids) == {"p_email_a", "p_email_b"}

    working_email = SimulatedEmailAdapter()
    push = SimulatedPushAdapter()
    recovered = NotificationDispatcher(
        store=store, adapters={ChannelType.EMAIL: working_email, ChannelType.PUSH: push},
    )

    replayed = recovered.replay_dlq(dlq_ids["p_email_a"], prefs=prefs, now_utc=_at(1))
    assert replayed.status == NotificationStatus.DISPATCHED
    assert replayed.proposal_id == "p_email_a"
    assert [m["proposal_id"] for m in working_email.delivered_messages] == ["p_email_a"]
    resolved = {e["proposal_id"]: e["resolved"] for e in store.get_dlq_entries()}
    assert resolved == {"p_email_a": True, "p_email_b": False}

    # Four pushes bring the learner to the cap of 5 (the replayed email counts as one).
    for i in range(4):
        pushed = recovered.dispatch(_proposal(f"p_push_{i}", ChannelType.PUSH), prefs=prefs, now_utc=_at(2 + i))
        assert pushed.status == NotificationStatus.DISPATCHED

    blocked = recovered.replay_dlq(dlq_ids["p_email_b"], prefs=prefs, now_utc=_at(10))
    assert blocked.status == NotificationStatus.SUPPRESSED
    assert blocked.reason == "daily_cap_exceeded"
    assert len(working_email.delivered_messages) == 1
    store.close()


def test_a_channel_without_a_registered_adapter_is_suppressed_as_unsupported(tmp_path):
    store = NotificationStore(str(tmp_path / "email_unsupported.db"))
    push = SimulatedPushAdapter()
    dispatcher = NotificationDispatcher(store=store, adapters={ChannelType.PUSH: push})  # no email, no in-app
    prefs = LearnerPreferences(learner_id="L1")

    for minute, channel in enumerate((ChannelType.EMAIL, ChannelType.IN_APP), start=1):
        res = dispatcher.dispatch(_proposal(f"p_{channel.value}", channel), prefs=prefs, now_utc=_at(minute))
        assert res.status == NotificationStatus.SUPPRESSED
        assert res.reason == "unsupported_channel"
    assert push.delivered_messages == []
    store.close()


def test_duplicate_email_proposal_id_is_rejected_like_push(tmp_path):
    store = NotificationStore(str(tmp_path / "email_dup.db"))
    dispatcher = NotificationDispatcher(store=store)
    prefs = LearnerPreferences(learner_id="L1")

    results = {}
    for ch in (ChannelType.PUSH, ChannelType.EMAIL):
        first = dispatcher.dispatch(_proposal(f"dup_{ch.value}", ch), prefs=prefs, now_utc=_at(1))
        second = dispatcher.dispatch(_proposal(f"dup_{ch.value}", ch), prefs=prefs, now_utc=_at(2))
        assert first.status == NotificationStatus.DISPATCHED
        results[ch] = (second.status, second.reason)

    assert results[ChannelType.PUSH] == (NotificationStatus.SUPPRESSED, "duplicate_proposal")
    assert results[ChannelType.EMAIL] == results[ChannelType.PUSH]  # same treatment for email as for push
    assert len(dispatcher.adapters[ChannelType.EMAIL].delivered_messages) == 1
    assert len(dispatcher.adapters[ChannelType.PUSH].delivered_messages) == 1
    store.close()
