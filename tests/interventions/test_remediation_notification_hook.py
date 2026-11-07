"""M4 notification hook: run_remediation_with_notification.

Written before the implementation. The hook calls run_remediation first; only an "assign"
decision becomes a NotificationProposal handed to the dispatcher. A suppressed, rescheduled or
failed notification never changes or undoes the stored decision or quest.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.interventions.policy as policy_module
import src.interventions.service as service_module
from src.interventions.notification_hook import run_remediation_with_notification
from src.interventions.service import run_quest_expiry
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
from tests.interventions.test_checkpoint_attempt_ingestion import (
    OCCURRED,
    _learner,
    _new_learner,
    _open,
)
from tests.interventions.test_remediation_service import NOW, _ingest

QUEST = "remediation_quest:att-1"
NOTICE = "remediation_notice:att-1"


class RecordingPush(SimulatedPushAdapter):
    """SimulatedPushAdapter does not record the template, so this also keeps each proposal it sent."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.proposals = []

    def send(self, proposal):
        delivered = super().send(proposal)
        self.proposals.append(proposal)
        return delivered


@pytest.fixture
def make_env(tmp_path):
    """Factory for an isolated learner + event store + notification store + dispatcher."""
    opened = []

    def _make(name="env", push=None):
        application, evidence = _open(str(tmp_path / f"{name}_events.db"))
        store = NotificationStore(str(tmp_path / f"{name}_notif.db"))
        push = push or RecordingPush()
        dispatcher = NotificationDispatcher(store=store, adapters={ChannelType.PUSH: push})
        learner_id = _new_learner(application)
        opened.append((application, evidence, store))
        return SimpleNamespace(
            application=application, evidence=evidence, store=store, push=push,
            dispatcher=dispatcher, learner_id=learner_id,
            prefs=LearnerPreferences(learner_id=learner_id),
        )

    yield _make
    for application, evidence, store in opened:
        store.close()
        evidence.close()
        application.close()


def _hook(env, attempt_id, now=NOW, prefs="default"):
    return run_remediation_with_notification(
        env.application, env.learner_id, attempt_id,
        clock=lambda: now, dispatcher=env.dispatcher,
        prefs=env.prefs if prefs == "default" else prefs,
    )


def _assert_decision_and_quest_stored(env, attempt_id="att-1"):
    learner = _learner(env.application, env.learner_id)
    assert learner.remediation_decisions[f"remediation:{attempt_id}"]["action"] == "assign"
    assert learner.quest_instances[f"remediation_quest:{attempt_id}"]["status"] == "assigned"


def test_a_failed_first_attempt_delivers_one_push_and_keeps_the_quest(make_env):
    env = make_env()
    _ingest(env.application, env.evidence, env.learner_id, "att-1")

    decision, fact = _hook(env, "att-1")

    assert decision.action == "assign"
    assert fact.event_type == "notification_dispatched"
    assert fact.status == NotificationStatus.DISPATCHED
    assert fact.channel == "push"
    assert len(env.push.delivered_messages) == 1
    message = env.push.delivered_messages[0]
    assert message["proposal_id"] == NOTICE
    assert message["learner_id"] == env.learner_id
    assert message["category"] == "remediation"
    assert set(message["payload"]) == {"quest_id", "assignment_id", "deadline_at", "assessment_id", "attempt_id"}
    assert message["payload"]["quest_id"] == "remediation_v1"
    assert message["payload"]["assignment_id"] == QUEST
    assert message["payload"]["assessment_id"] == decision.assessment_id
    assert message["payload"]["attempt_id"] == "att-1"
    deadline = datetime.fromisoformat(str(message["payload"]["deadline_at"]).replace("Z", "+00:00"))
    assert deadline == decision.deadline_at

    sent = env.push.proposals[0]
    assert sent.template_id == "checkpoint_remediation_v1"
    assert sent.category == NotificationCategory.REMEDIATION
    assert sent.ttl_seconds == 86400
    assert datetime.fromisoformat(sent.created_at.replace("Z", "+00:00")) == decision.decided_at
    _assert_decision_and_quest_stored(env)


def test_no_action_decisions_send_nothing(make_env):
    # A passed first attempt: no decision to notify about, and nothing reaches the notification store.
    passed = make_env("passed")
    _ingest(passed.application, passed.evidence, passed.learner_id, "att-1", passed=True)
    decision, fact = _hook(passed, "att-1")
    assert decision.action == "no_action" and decision.reason == "passed"
    assert fact is None
    assert passed.push.delivered_messages == []
    assert passed.store.get_proposal_status(NOTICE) is None

    # A second attempt at the same assessment, and a new failure inside the 14-day window.
    env = make_env("window")
    _ingest(env.application, env.evidence, env.learner_id, "att-1", assessment_id="math")
    first, first_fact = _hook(env, "att-1")
    assert first.action == "assign" and first_fact.status == NotificationStatus.DISPATCHED
    _ingest(env.application, env.evidence, env.learner_id, "att-2", assessment_id="math",
            occurred_at=OCCURRED + timedelta(days=1))
    _ingest(env.application, env.evidence, env.learner_id, "att-3", assessment_id="physics",
            occurred_at=OCCURRED + timedelta(days=2))

    later = NOW + timedelta(days=3)
    second, second_fact = _hook(env, "att-2", now=later)
    third, third_fact = _hook(env, "att-3", now=later)
    assert (second.action, second.reason) == ("no_action", "not_first_attempt")
    assert (third.action, third.reason) == ("no_action", "remediated_within_window")
    assert second_fact is None and third_fact is None
    assert len(env.push.delivered_messages) == 1  # only the first notice
    assert env.store.get_proposal_status("remediation_notice:att-2") is None
    assert env.store.get_proposal_status("remediation_notice:att-3") is None


def test_rerunning_the_same_attempt_does_not_notify_twice(make_env):
    env = make_env()
    _ingest(env.application, env.evidence, env.learner_id, "att-1")

    first, first_fact = _hook(env, "att-1")
    second, second_fact = _hook(env, "att-1", now=NOW + timedelta(hours=1))

    assert second == first  # the stored decision comes back unchanged
    assert first_fact.status == NotificationStatus.DISPATCHED
    assert second_fact.status == NotificationStatus.SUPPRESSED
    assert second_fact.reason == "duplicate_proposal"
    assert len(env.push.delivered_messages) == 1
    _assert_decision_and_quest_stored(env)


def test_missing_or_opted_out_preferences_suppress_but_keep_decision_and_quest(make_env):
    scenarios = {
        "missing": (None, "missing_preferences"),
        "category": (lambda lid: LearnerPreferences(
            learner_id=lid, opted_out_categories={NotificationCategory.REMEDIATION}), "category_opt_out"),
        "channel": (lambda lid: LearnerPreferences(
            learner_id=lid, opted_out_channels={ChannelType.PUSH}), "opt_out"),
    }
    for name, (build_prefs, expected_reason) in scenarios.items():
        env = make_env(name)
        _ingest(env.application, env.evidence, env.learner_id, "att-1")
        prefs = None if build_prefs is None else build_prefs(env.learner_id)

        decision, fact = _hook(env, "att-1", prefs=prefs)

        assert decision.action == "assign", name
        assert fact.status == NotificationStatus.SUPPRESSED, name
        assert fact.reason == expected_reason, name
        assert env.push.delivered_messages == [], name
        _assert_decision_and_quest_stored(env)


def test_a_decision_made_in_quiet_hours_is_rescheduled_to_08_local(make_env):
    env = make_env()
    env.prefs = LearnerPreferences(learner_id=env.learner_id, timezone="Asia/Kolkata")
    _ingest(env.application, env.evidence, env.learner_id, "att-1")
    quiet = datetime(2026, 9, 10, 17, 40, tzinfo=timezone.utc)  # 23:10 IST

    decision, fact = _hook(env, "att-1", now=quiet)

    assert decision.decided_at == quiet
    assert fact.status == NotificationStatus.QUEUED
    assert fact.event_type == "notification_rescheduled"
    assert fact.reason == "quiet_hours_rescheduled"
    assert fact.rescheduled_for == "2026-09-11T02:30:00Z"  # 08:00 IST
    assert env.push.delivered_messages == []
    _assert_decision_and_quest_stored(env)


def test_a_permanently_failing_adapter_never_undoes_the_decision(make_env):
    env = make_env(push=RecordingPush(fail_permanently=True))
    _ingest(env.application, env.evidence, env.learner_id, "att-1")

    decision, fact = _hook(env, "att-1")

    assert decision.action == "assign"
    assert fact.status == NotificationStatus.FAILED
    assert fact.event_type == "notification_failed"
    _assert_decision_and_quest_stored(env)

    again, again_fact = _hook(env, "att-1", now=NOW + timedelta(hours=1))  # must not raise
    assert again == decision
    assert again_fact is not None
    assert env.push.delivered_messages == []
    _assert_decision_and_quest_stored(env)


def test_policy_and_service_do_not_import_the_notifications_package():
    for module in (policy_module, service_module):
        text = Path(module.__file__).read_text(encoding="utf-8")
        assert "src.notifications" not in text, module.__name__


# --- Edge cases ---------------------------------------------------------------


def _default_dispatcher(env):
    """The default dispatcher: in-app, simulated push and simulated email all registered."""
    env.dispatcher = NotificationDispatcher(store=env.store)
    return env.dispatcher


def test_end_to_end_one_notice_and_the_expiry_sweep_sends_nothing_more(make_env):
    env = make_env()
    dispatcher = _default_dispatcher(env)
    _ingest(env.application, env.evidence, env.learner_id, "att-1")

    decision, fact = _hook(env, "att-1")
    assert fact.status == NotificationStatus.DISPATCHED

    after_deadline = NOW + timedelta(days=8)
    expired = run_quest_expiry(env.application, env.learner_id, clock=lambda: after_deadline)

    assert expired == [QUEST]
    assert _learner(env.application, env.learner_id).quest_instances[QUEST]["status"] == "expired"
    assert len(dispatcher.adapters[ChannelType.PUSH].delivered_messages) == 1
    assert env.store.connection.execute("SELECT COUNT(*) FROM notification_dispatches").fetchone() == (1,)
    assert env.store.connection.execute("SELECT COUNT(*) FROM notification_proposals").fetchone() == (1,)


def test_a_learner_already_at_the_cap_gets_the_decision_and_quest_but_no_notice(make_env):
    env = make_env()
    for i in range(5):  # five pushes in the last 24h, outside quiet hours
        at = f"2026-09-10T11:0{i}:00Z"
        res = env.dispatcher.dispatch(
            NotificationProposal(
                proposal_id=f"earlier_{i}", learner_id=env.learner_id, channel=ChannelType.PUSH,
                category=NotificationCategory.STREAK_RISK, created_at=at,
            ),
            prefs=env.prefs, now_utc=at,
        )
        assert res.status == NotificationStatus.DISPATCHED
    _ingest(env.application, env.evidence, env.learner_id, "att-1")

    decision, fact = _hook(env, "att-1")

    assert decision.action == "assign"
    assert fact.status == NotificationStatus.SUPPRESSED
    assert fact.reason == "daily_cap_exceeded"
    assert len(env.push.delivered_messages) == 5
    _assert_decision_and_quest_stored(env)


def test_channel_override_in_app_delivers_and_email_without_a_recipient_fails_permanently(make_env):
    in_app_env = make_env("in_app")
    dispatcher = _default_dispatcher(in_app_env)
    _ingest(in_app_env.application, in_app_env.evidence, in_app_env.learner_id, "att-1")
    decision, fact = run_remediation_with_notification(
        in_app_env.application, in_app_env.learner_id, "att-1", clock=lambda: NOW,
        dispatcher=dispatcher, prefs=in_app_env.prefs, channel=ChannelType.IN_APP,
    )
    assert fact.status == NotificationStatus.DISPATCHED
    assert fact.channel == "in_app"
    assert [m["proposal_id"] for m in dispatcher.adapters[ChannelType.IN_APP].delivered_messages] == [NOTICE]
    assert dispatcher.adapters[ChannelType.PUSH].delivered_messages == []
    _assert_decision_and_quest_stored(in_app_env)

    # The hook puts no recipient in the payload, so an email proposal cannot be delivered:
    # the email adapter raises a permanent error and the notice goes to the DLQ.
    email_env = make_env("email")
    email_dispatcher = _default_dispatcher(email_env)
    _ingest(email_env.application, email_env.evidence, email_env.learner_id, "att-1")
    decision, fact = run_remediation_with_notification(
        email_env.application, email_env.learner_id, "att-1", clock=lambda: NOW,
        dispatcher=email_dispatcher, prefs=email_env.prefs, channel=ChannelType.EMAIL,
    )
    assert fact.status == NotificationStatus.FAILED
    assert fact.channel == "email"
    assert "permanent_failure" in (fact.reason or "")
    assert fact.attempt_count == 1
    assert email_dispatcher.adapters[ChannelType.EMAIL].delivered_messages == []
    assert [(e["proposal_id"], e["channel"]) for e in email_env.store.get_dlq_entries()] == [(NOTICE, "email")]
    _assert_decision_and_quest_stored(email_env)


def test_an_exploding_dispatcher_leaves_decision_and_quest_saved_and_a_retry_delivers_once(make_env):
    class ExplodingDispatcher:
        def dispatch(self, proposal, prefs=None, now_utc=None):
            raise RuntimeError("dispatcher blew up")

    env = make_env()
    _ingest(env.application, env.evidence, env.learner_id, "att-1")

    with pytest.raises(RuntimeError, match="dispatcher blew up"):  # the hook does not hide it
        run_remediation_with_notification(
            env.application, env.learner_id, "att-1", clock=lambda: NOW,
            dispatcher=ExplodingDispatcher(), prefs=env.prefs,
        )
    _assert_decision_and_quest_stored(env)
    stored = _learner(env.application, env.learner_id).remediation_decisions["remediation:att-1"]

    decision, fact = _hook(env, "att-1", now=NOW + timedelta(minutes=5))

    assert decision.decision_id == stored["decision_id"]
    assert decision.decided_at.isoformat() == stored["decided_at"]  # the stored decision, not a new one
    assert fact.status == NotificationStatus.DISPATCHED
    assert len(env.push.delivered_messages) == 1
    again, again_fact = _hook(env, "att-1", now=NOW + timedelta(minutes=10))
    assert again_fact.reason == "duplicate_proposal"
    assert len(env.push.delivered_messages) == 1


def test_two_failures_far_apart_each_get_their_own_notice_checked_at_their_own_time(make_env):
    later = NOW + timedelta(days=20)  # well outside the 14-day window
    scenarios = {
        # Day 0 fills the cap (4 earlier pushes + the first notice); day 20 has an empty 24h window.
        "cap": (later, "UTC", NotificationStatus.DISPATCHED, None),
        # Day 20 at 23:10 IST is inside quiet hours; day 0 at 17:30 IST was not.
        "quiet": (datetime(2026, 9, 30, 17, 40, tzinfo=timezone.utc), "Asia/Kolkata",
                  NotificationStatus.QUEUED, "2026-10-01T02:30:00Z"),
    }
    for name, (second_time, tz, second_status, rescheduled_for) in scenarios.items():
        env = make_env(name)
        env.prefs = LearnerPreferences(learner_id=env.learner_id, timezone=tz)
        if name == "cap":
            for i in range(4):
                at = f"2026-09-10T11:0{i}:00Z"
                env.dispatcher.dispatch(
                    NotificationProposal(
                        proposal_id=f"earlier_{i}", learner_id=env.learner_id, channel=ChannelType.PUSH,
                        category=NotificationCategory.STREAK_RISK, created_at=at,
                    ),
                    prefs=env.prefs, now_utc=at,
                )
        _ingest(env.application, env.evidence, env.learner_id, "att-1", assessment_id="math")
        _ingest(env.application, env.evidence, env.learner_id, "att-2", assessment_id="physics",
                occurred_at=OCCURRED + timedelta(days=19))

        first, first_fact = _hook(env, "att-1")
        second, second_fact = _hook(env, "att-2", now=second_time)

        assert (first.action, second.action) == ("assign", "assign"), name
        assert first_fact.status == NotificationStatus.DISPATCHED, name  # the 5th push on day 0 in "cap"
        assert second_fact.proposal_id == "remediation_notice:att-2", name
        assert first_fact.proposal_id == "remediation_notice:att-1", name
        assert second_fact.status == second_status, name
        assert second_fact.rescheduled_for == rescheduled_for, name
        learner = _learner(env.application, env.learner_id)
        assert set(learner.quest_instances) == {"remediation_quest:att-1", "remediation_quest:att-2"}, name


def test_a_naive_clock_raises_and_sends_nothing_while_a_non_utc_clock_is_normalised(make_env):
    env = make_env("naive")
    _ingest(env.application, env.evidence, env.learner_id, "att-1")

    with pytest.raises(ValueError, match="timezone-aware"):  # raised by the policy, not swallowed
        run_remediation_with_notification(
            env.application, env.learner_id, "att-1", clock=lambda: datetime(2026, 9, 10, 12, 0),
            dispatcher=env.dispatcher, prefs=env.prefs,
        )
    learner = _learner(env.application, env.learner_id)
    assert learner.remediation_decisions == {}
    assert learner.quest_instances == {}
    assert env.push.delivered_messages == []
    assert env.store.get_proposal_status(NOTICE) is None

    # A timezone-aware, non-UTC clock is fine: 23:10 IST is 17:40Z, so quiet hours apply.
    ist = timezone(timedelta(hours=5, minutes=30))
    env2 = make_env("ist")
    env2.prefs = LearnerPreferences(learner_id=env2.learner_id, timezone="Asia/Kolkata")
    _ingest(env2.application, env2.evidence, env2.learner_id, "att-1")
    decision, fact = _hook(env2, "att-1", now=datetime(2026, 9, 10, 23, 10, tzinfo=ist))
    assert decision.decided_at == datetime(2026, 9, 10, 17, 40, tzinfo=timezone.utc)
    assert fact.status == NotificationStatus.QUEUED
    assert fact.rescheduled_for == "2026-09-11T02:30:00Z"
    _assert_decision_and_quest_stored(env2)
