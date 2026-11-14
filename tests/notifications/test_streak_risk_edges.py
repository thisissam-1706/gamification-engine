"""M5 step 5: edge cases of the streak-risk sweep, end to end through the real dispatcher.

1 AT-14 style: treatment learner at the daily cap -> blocked + exactly one violation row
2 treatment vs control under the same conditions
3 the cap is shared with the remediation notice
4 DLQ / retry keeps experiment_id and variant through replay
5 a late sweep sends only the latest slot, nothing in quiet hours
6 rebuilding the learner from the event log does not change the plan
"""

from dataclasses import asdict

import pytest

import src.notifications.streak_risk as streak_risk
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
from src.notifications.streak_risk import plan_streak_risk_reminders, run_streak_risk_sweep
from src.state_machine.projection import rebuild_learner_state
from tests.experiments.support import EXP_ID, open_persisted
from tests.notifications.test_streak_risk import (
    D,
    IST,
    YESTERDAY,
    _hhmm_list,
    _learner,
    _persist,
    _prefs,
    _RecordingPush,
    ist,
)


def _dispatcher(tmp_path, adapter=None, **kwargs):
    store = NotificationStore(str(tmp_path / "notif.db"))
    adapter = adapter or _RecordingPush()
    dispatcher = NotificationDispatcher(store=store, adapters={ChannelType.PUSH: adapter}, **kwargs)
    return dispatcher, store, adapter


def _fill(dispatcher, learner_id, prefs, n=5, first_hour=10):
    """n unflagged messages dispatched on the same IST day, well before the evening slots."""
    for i in range(n):
        fact = dispatcher.dispatch(
            NotificationProposal(
                proposal_id=f"fill-{learner_id}-{i}",
                learner_id=learner_id,
                channel=ChannelType.PUSH,
                category=NotificationCategory.GENERAL,
                created_at="2026-10-12T04:00:00Z",
            ),
            prefs=prefs,
            now_utc=ist(first_hour + i),
        )
        assert fact.status == NotificationStatus.DISPATCHED  # setup sanity


def _sweep(application, dispatcher, learners, now):
    ids = [str(l.id) for l in learners]
    return run_streak_risk_sweep(
        application, dispatcher, ids, {str(l.id): _prefs(l) for l in learners}, now_utc=now
    )


# --- 1: AT-14 end to end ---------------------------------------------------------------


def test_1_treatment_learner_with_five_messages_is_blocked_and_flagged_once(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        learner = _learner("variant_a")
        (learner_id,) = _persist(application, learner)
        dispatcher, store, push = _dispatcher(tmp_path)  # default cap: 5 per 24h
        try:
            _fill(dispatcher, learner_id, _prefs(learner))

            first = _sweep(application, dispatcher, [learner], ist(21, 10))

            (outcome,) = first.outcomes
            assert outcome.fact.status == NotificationStatus.SUPPRESSED
            assert outcome.fact.reason == "daily_cap_exceeded"
            assert len(push.proposals) == 5  # the reminder was not delivered
            (violation,) = store.list_guardrail_violations(experiment_id=EXP_ID)
            assert violation["variant"] == "variant_a"
            assert violation["learner_id"] == learner_id
            assert violation["proposal_id"] == f"streak_risk:{learner_id}:{D}:2100"
            assert violation["reason"] == "daily_cap_exceeded"

            second = _sweep(application, dispatcher, [learner], ist(21, 20))  # same 21:00 slot

            assert second.outcomes[0].fact.reason == "duplicate_proposal"
            assert store.violation_counts_by_variant(EXP_ID) == {"variant_a": 1}
            assert len(store.list_guardrail_violations()) == 1
        finally:
            store.close()


# --- 2: treatment vs control --------------------------------------------------------------


def test_2_only_the_treatment_learner_is_sent_or_flagged(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        treatment = _learner("variant_a", nth=0)
        control = _learner("variant_b", nth=0)
        _persist(application, treatment, control)
        dispatcher, store, push = _dispatcher(tmp_path)
        try:
            result = _sweep(application, dispatcher, [treatment, control], ist(21, 10))

            by_id = {o.learner_id: o for o in result.outcomes}
            assert by_id[str(treatment.id)].fact.status == NotificationStatus.DISPATCHED
            assert by_id[str(control.id)].fact is None
            assert by_id[str(control.id)].reason == "not_treatment"
            assert [p.learner_id for p in push.proposals] == [str(treatment.id)]
        finally:
            store.close()


def test_2_at_the_cap_only_the_treatment_variant_can_have_violations(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        treatment = _learner("variant_a", nth=0)
        control = _learner("variant_b", nth=0)
        _persist(application, treatment, control)
        dispatcher, store, push = _dispatcher(tmp_path)
        try:
            for learner in (treatment, control):  # identical conditions: both at the cap
                _fill(dispatcher, str(learner.id), _prefs(learner))

            _sweep(application, dispatcher, [treatment, control], ist(21, 10))
            _sweep(application, dispatcher, [treatment, control], ist(21, 20))  # same 21:00 slot

            assert store.violation_counts_by_variant(EXP_ID) == {"variant_a": 1}
            assert store.list_guardrail_violations(variant="variant_b") == []
            assert {v["learner_id"] for v in store.list_guardrail_violations()} == {str(treatment.id)}
        finally:
            store.close()


# --- 3: the cap is shared with the remediation notice -----------------------------------


def test_3_four_reminders_plus_a_remediation_notice_hit_the_cap(tmp_path, monkeypatch):
    # Five configured slots so a fifth reminder exists after four reminders and the notice.
    monkeypatch.setattr(
        streak_risk, "REMINDER_TIMES_LOCAL", ("20:00", "21:00", "21:30", "21:40", "21:45")
    )
    with open_persisted(tmp_path) as (application, _):
        learner = _learner("variant_a")
        (learner_id,) = _persist(application, learner)
        dispatcher, store, push = _dispatcher(tmp_path)
        try:
            for hh, mm in ((20, 0), (21, 0), (21, 30), (21, 40)):
                fact = _sweep(application, dispatcher, [learner], ist(hh, mm)).outcomes[0].fact
                assert fact.status == NotificationStatus.DISPATCHED  # setup sanity

            remediation = NotificationProposal(
                proposal_id="remediation_notice:att-1",
                learner_id=learner_id,
                channel=ChannelType.PUSH,
                category=NotificationCategory.REMEDIATION,
                template_id="checkpoint_remediation_v1",
                created_at="2026-10-12T16:10:00Z",
            )
            notice = dispatcher.dispatch(remediation, prefs=_prefs(learner), now_utc=ist(21, 42))
            assert notice.status == NotificationStatus.DISPATCHED  # that is message 5
            assert len(push.proposals) == 5

            fact = _sweep(application, dispatcher, [learner], ist(21, 45)).outcomes[0].fact

            assert fact.status == NotificationStatus.SUPPRESSED
            assert fact.reason == "daily_cap_exceeded"
            (violation,) = store.list_guardrail_violations(experiment_id=EXP_ID)
            assert violation["proposal_id"] == f"streak_risk:{learner_id}:{D}:2145"
            assert violation["variant"] == "variant_a"
        finally:
            store.close()


# --- 4: DLQ and replay ---------------------------------------------------------------------


def test_4_failed_reminder_goes_to_the_dlq_and_replays_with_its_fields(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        learner = _learner("variant_a")
        (learner_id,) = _persist(application, learner)
        prefs = _prefs(learner)
        dispatcher, store, _ = _dispatcher(
            tmp_path,
            adapter=SimulatedPushAdapter(fail_attempts=5),
            retry_scheduler=RetryScheduler(max_retries=3, backoff_delay_sec=0.0),
        )
        try:
            failed = _sweep(application, dispatcher, [learner], ist(21, 10)).outcomes[0].fact
            assert failed.status == NotificationStatus.FAILED
            assert failed.correlation_id == f"streak_risk:{D}"

            (entry,) = store.get_dlq_entries()
            assert entry["proposal_id"] == f"streak_risk:{learner_id}:{D}:2100"
            assert (entry["experiment_id"], entry["variant"]) == (EXP_ID, "variant_a")
            assert entry["correlation_id"] == f"streak_risk:{D}"

            # The outage ends: a recovery dispatcher with a working adapter replays the entry.
            push = _RecordingPush(fail_attempts=0)
            recovery = NotificationDispatcher(store=store, adapters={ChannelType.PUSH: push})
            replayed = recovery.replay_dlq(entry["dlq_id"], prefs=prefs, now_utc=ist(21, 20))

            assert replayed.status == NotificationStatus.DISPATCHED
            assert replayed.correlation_id == f"streak_risk:{D}"
            (sent,) = push.proposals
            assert (sent.experiment_id, sent.variant) == (EXP_ID, "variant_a")
            assert sent.template_id == "streak_risk_v1"
            assert sent.category == NotificationCategory.STREAK_RISK
            assert sent.payload["streak_count"] == learner.new_streak

            # Replay did not create a duplicate: a later sweep of the same slot is deduplicated.
            again = _sweep(application, dispatcher, [learner], ist(21, 25)).outcomes[0].fact
            assert again.reason == "duplicate_proposal"
            assert store.list_guardrail_violations() == []
        finally:
            store.close()


# --- 5: late sweeps send nothing stale -------------------------------------------------------


def test_5_a_sweep_at_2150_sends_only_the_2145_reminder_once(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        learner = _learner("variant_a")
        _persist(application, learner)
        dispatcher, store, push = _dispatcher(tmp_path)
        try:
            _sweep(application, dispatcher, [learner], ist(21, 50))
            repeat = _sweep(application, dispatcher, [learner], ist(21, 55)).outcomes[0].fact

            assert _hhmm_list(push.proposals) == ["2145"]  # 20:00, 21:00, 21:30 never sent
            assert repeat.reason == "duplicate_proposal"
        finally:
            store.close()


def test_5_a_sweep_at_2230_sends_nothing(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        learner = _learner("variant_a")
        _persist(application, learner)
        dispatcher, store, push = _dispatcher(tmp_path)
        try:
            outcome = _sweep(application, dispatcher, [learner], ist(22, 30)).outcomes[0]

            assert (outcome.fact, outcome.reason) == (None, "quiet_hours")
            assert push.proposals == []
            assert store.get_dlq_entries() == []
            assert store.list_guardrail_violations() == []
        finally:
            store.close()


# --- 6: the plan depends only on stored state --------------------------------------------------


def test_6_plan_is_unchanged_by_rebuilding_the_learner_from_the_event_log(tmp_path):
    now = ist(21, 40)
    with open_persisted(tmp_path) as (application, _):
        learner = _learner("variant_a", active_dates=("2026-10-09", "2026-10-10", YESTERDAY))
        (learner_id,) = _persist(application, learner)
        prefs = _prefs(learner)
        before = plan_streak_risk_reminders(learner, prefs, now)
        assert _hhmm_list(before) == ["2000", "2100", "2130"]  # setup sanity

        reloaded = application.repository.get(learner.id)
        replayed = rebuild_learner_state(list(application.events.get(learner.id)))

        assert replayed.new_streak == reloaded.new_streak == learner.new_streak == 3
        assert replayed.activity_dates == tuple(sorted(reloaded.activity_dates))
        assert [asdict(p) for p in plan_streak_risk_reminders(reloaded, prefs, now)] == [
            asdict(p) for p in before
        ]

    # A fresh application on the same database (a new process) plans the same thing.
    with open_persisted(tmp_path) as (fresh_application, _):
        fresh = fresh_application.repository.get(learner.id)
        assert [asdict(p) for p in plan_streak_risk_reminders(fresh, prefs, now)] == [
            asdict(p) for p in before
        ]
