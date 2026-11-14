"""M5 step 7: stale-streak rule and the exp_007 exclusions for streak-risk reminders.

a  at risk = the streak can still be extended today, by the same continuation rule as
   ``calculate_streak`` (a freeze can bridge one missed day); else "streak_already_broken"
b  platform tenure under 3 x 24 h (now - the learner's Created timestamp) -> "excluded_tenure"
c  opted out of the push channel OR the streak_risk category -> "excluded_opted_out"; no proposal
   is built and the dispatcher is not called
d  check order: not_treatment, excluded_tenure, excluded_opted_out, streak_already_broken,
   already_active_today, no_streak (then quiet_hours, nothing_due)
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.notifications.models import (
    ChannelType,
    LearnerPreferences,
    NotificationCategory,
    NotificationStatus,
)
from src.notifications.streak_risk import plan_streak_risk_reminders, run_streak_risk_sweep
from src.state_machine.learner import Learner
from tests.experiments.support import ASSIGNED_AT, EXP_ID, open_persisted, uuid_for_variant
from tests.notifications.test_streak_risk import (
    D,
    IST,
    YESTERDAY,
    _dispatcher,
    _hhmm_list,
    _persist,
    _prefs,
    ist,
)

NOW = ist(21, 10)  # 2026-10-12 21:10 IST
OLD = NOW - timedelta(days=30)  # created long ago: passes the tenure exclusion


def _make(
    variant="variant_a",
    active_dates=(YESTERDAY,),
    created_at: datetime = OLD,
    nth: int = 0,
    freezes=(),
) -> Learner:
    """Learner with an explicit Created timestamp, completions on `active_dates` and
    freezes given as (freeze_id, acquired_on) pairs."""
    learner = Learner._create(
        Learner.Created, id=uuid_for_variant(variant or "variant_a", EXP_ID, nth=nth), timestamp=created_at
    )
    if variant is not None:
        assert learner.assign_experiment(EXP_ID, assigned_at=ASSIGNED_AT)
    for i, day in enumerate(active_dates):
        learner.complete_lesson(f"c-{i}", f"lesson-{i}", activity_date=day)
    for freeze_id, acquired_on in freezes:
        learner.acquire_freeze(freeze_id, activity_date=acquired_on)
    return learner


def _sweep_one(tmp_path, learner, prefs=None, now=NOW):
    """Persists the learner, sweeps once; returns (outcome, push adapter, store)."""
    with open_persisted(tmp_path) as (application, _):
        (learner_id,) = _persist(application, learner)
        dispatcher, store, push = _dispatcher(tmp_path)
        try:
            result = run_streak_risk_sweep(
                application,
                dispatcher,
                [learner_id],
                {learner_id: prefs or _prefs(learner)},
                now_utc=now,
            )
            snapshot = (
                list(push.proposals),
                store.get_suppression_entries(),
                store.get_dlq_entries(),
                store.get_proposal_status(f"streak_risk:{learner_id}:{D}:2100"),
            )
            return result.outcomes[0], snapshot
        finally:
            store.close()


# --- a: stale streaks and freezes (follows calculate_streak) -----------------------------


def test_a_last_lesson_two_local_days_ago_without_freeze_is_already_broken(tmp_path):
    learner = _make(active_dates=("2026-10-09", "2026-10-10"))  # nothing on 11th or 12th
    assert learner.new_streak == 2  # setup sanity: the stored streak is still >= 1
    assert plan_streak_risk_reminders(learner, _prefs(learner), NOW) == []

    outcome, (sent, _, _, _) = _sweep_one(tmp_path, learner)

    assert (outcome.fact, outcome.reason) == (None, "streak_already_broken")
    assert sent == []


def test_a_one_missed_day_without_freeze_is_already_broken(tmp_path):
    learner = _make(active_dates=("2026-10-09", "2026-10-10"))
    # last activity 10th, 11th missed, today the 12th: calculate_streak resets without a freeze
    outcome, _ = _sweep_one(tmp_path, learner)
    assert outcome.reason == "streak_already_broken"


def test_a_one_missed_day_with_a_freeze_still_gets_a_reminder():
    learner = _make(
        active_dates=("2026-10-09", "2026-10-10"), freezes=(("fr-1", "2026-10-10"),)
    )
    assert learner.freezes_remaining == 1  # setup sanity
    proposals = plan_streak_risk_reminders(learner, _prefs(learner), NOW)
    assert _hhmm_list(proposals) == ["2000", "2100"]
    assert proposals[0].payload["streak_count"] == learner.new_streak


def test_a_two_missed_days_with_one_freeze_is_already_broken():
    learner = _make(
        active_dates=("2026-10-08", "2026-10-09"), freezes=(("fr-1", "2026-10-09"),)
    )  # 10th and 11th missed, one freeze cannot bridge both
    assert plan_streak_risk_reminders(learner, _prefs(learner), NOW) == []


def test_a_last_lesson_yesterday_is_still_at_risk():
    learner = _make(active_dates=(YESTERDAY,))
    assert _hhmm_list(plan_streak_risk_reminders(learner, _prefs(learner), NOW)) == ["2000", "2100"]


def test_a_continuation_uses_the_learners_local_date():
    # 2026-11-02T01:30Z is 2026-11-01 in New York: a lesson on 10-31 is "yesterday" there.
    now = datetime(2026, 11, 2, 1, 30, tzinfo=timezone.utc)
    learner = _make(active_dates=("2026-10-31",), created_at=now - timedelta(days=30))
    prefs = LearnerPreferences(str(learner.id), timezone="America/New_York")
    assert _hhmm_list(plan_streak_risk_reminders(learner, prefs, now)) == ["2000"]
    # whereas a lesson on 10-30 is two local days old
    stale = _make(active_dates=("2026-10-30",), created_at=now - timedelta(days=30), nth=1)
    prefs = LearnerPreferences(str(stale.id), timezone="America/New_York")
    assert plan_streak_risk_reminders(stale, prefs, now) == []


# --- b: tenure ----------------------------------------------------------------------------


def test_b_two_days_23_hours_is_excluded(tmp_path):
    learner = _make(created_at=NOW - timedelta(days=2, hours=23))
    assert plan_streak_risk_reminders(learner, _prefs(learner), NOW) == []

    outcome, (sent, _, _, _) = _sweep_one(tmp_path, learner)

    assert (outcome.fact, outcome.reason) == (None, "excluded_tenure")
    assert sent == []


def test_b_exactly_three_days_is_included(tmp_path):
    learner = _make(created_at=NOW - timedelta(days=3))
    assert _hhmm_list(plan_streak_risk_reminders(learner, _prefs(learner), NOW)) == ["2000", "2100"]

    outcome, (sent, _, _, _) = _sweep_one(tmp_path, learner)

    assert outcome.reason is None
    assert outcome.fact.status == NotificationStatus.DISPATCHED
    assert len(sent) == 1


def test_b_tenure_is_read_from_the_stored_created_event(tmp_path):
    """The excluded learner is rebuilt from the event log, so the timestamp is the stored one."""
    learner = _make(created_at=NOW - timedelta(days=1))
    outcome, _ = _sweep_one(tmp_path, learner)
    assert outcome.reason == "excluded_tenure"


# --- c: opt-outs -----------------------------------------------------------------------------


def test_c_opted_out_of_push_is_excluded_without_calling_the_dispatcher(tmp_path):
    learner = _make()
    prefs = _prefs(learner, opted_out_channels={ChannelType.PUSH})
    assert plan_streak_risk_reminders(learner, prefs, NOW) == []

    outcome, (sent, suppressions, dlq, status) = _sweep_one(tmp_path, learner, prefs)

    assert (outcome.fact, outcome.reason) == (None, "excluded_opted_out")
    assert sent == []
    assert suppressions == []  # no opt_out suppression/failure row: the dispatcher never ran
    assert dlq == []
    assert status is None  # not even a registered proposal


def test_c_opted_out_of_streak_risk_category_is_excluded(tmp_path):
    learner = _make()
    prefs = _prefs(learner, opted_out_categories={NotificationCategory.STREAK_RISK})
    assert plan_streak_risk_reminders(learner, prefs, NOW) == []

    outcome, (sent, suppressions, dlq, status) = _sweep_one(tmp_path, learner, prefs)

    assert (outcome.fact, outcome.reason) == (None, "excluded_opted_out")
    assert (sent, suppressions, dlq, status) == ([], [], [], None)


def test_c_opting_out_of_other_channels_or_categories_does_not_exclude():
    learner = _make()
    prefs = _prefs(
        learner,
        opted_out_channels={ChannelType.EMAIL},
        opted_out_categories={NotificationCategory.SOCIAL_NUDGE},
    )
    assert plan_streak_risk_reminders(learner, prefs, NOW)


# --- d: order and coverage ---------------------------------------------------------------------


def test_d_control_learner_still_shows_not_treatment_first(tmp_path):
    learner = _make(
        variant="variant_b",
        created_at=NOW - timedelta(days=1),  # would be excluded_tenure
        active_dates=("2026-10-01",),  # would be streak_already_broken
    )
    prefs = _prefs(learner, opted_out_channels={ChannelType.PUSH})  # would be excluded_opted_out
    outcome, _ = _sweep_one(tmp_path, learner, prefs)
    assert outcome.reason == "not_treatment"


def test_d_unassigned_learner_is_not_treatment_first(tmp_path):
    learner = _make(variant=None, created_at=NOW - timedelta(days=1))
    outcome, _ = _sweep_one(tmp_path, learner)
    assert outcome.reason == "not_treatment"


def test_d_tenure_comes_before_opt_out_and_stale_streak(tmp_path):
    learner = _make(created_at=NOW - timedelta(days=1), active_dates=("2026-10-01",))
    prefs = _prefs(learner, opted_out_channels={ChannelType.PUSH})
    outcome, _ = _sweep_one(tmp_path, learner, prefs)
    assert outcome.reason == "excluded_tenure"


def test_d_opt_out_comes_before_stale_streak(tmp_path):
    learner = _make(active_dates=("2026-10-01",))
    prefs = _prefs(learner, opted_out_channels={ChannelType.PUSH})
    outcome, _ = _sweep_one(tmp_path, learner, prefs)
    assert outcome.reason == "excluded_opted_out"


def test_d_stale_streak_comes_before_the_quiet_hours_check(tmp_path):
    learner = _make(active_dates=("2026-10-01",))
    outcome, _ = _sweep_one(tmp_path, learner, now=ist(22, 30))
    assert outcome.reason == "streak_already_broken"


def test_d_every_skip_reason_appears_in_one_sweep(tmp_path):
    learners = [
        _make("variant_b", nth=0),  # not_treatment
        _make(nth=1, created_at=NOW - timedelta(days=1)),  # excluded_tenure
        _make(nth=2),  # excluded_opted_out (prefs below)
        _make(nth=3, active_dates=("2026-10-01",)),  # streak_already_broken
        _make(nth=4, active_dates=(YESTERDAY, D)),  # already_active_today
        _make(nth=5, active_dates=()),  # no_streak
        _make(nth=6),  # sent
    ]
    prefs = {str(l.id): _prefs(l) for l in learners}
    prefs[str(learners[2].id)] = _prefs(learners[2], opted_out_channels={ChannelType.PUSH})
    with open_persisted(tmp_path) as (application, _):
        ids = _persist(application, *learners)
        dispatcher, store, push = _dispatcher(tmp_path)
        try:
            result = run_streak_risk_sweep(application, dispatcher, ids, prefs, now_utc=NOW)

            assert [o.reason for o in result.outcomes] == [
                "not_treatment",
                "excluded_tenure",
                "excluded_opted_out",
                "streak_already_broken",
                "already_active_today",
                "no_streak",
                None,
            ]
            assert [p.learner_id for p in push.proposals] == [ids[6]]
        finally:
            store.close()
