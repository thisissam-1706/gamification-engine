"""M5 step 3: streak-risk reminder generator (exp_007 treatment arm).

Written before the implementation. Assumed new module ``src.notifications.streak_risk``:

* ``REMINDER_TIMES_LOCAL`` = ("20:00", "21:00", "21:30", "21:45")
* ``plan_streak_risk_reminders(learner, prefs, now_utc, *, experiment_id="exp_007")``
  -> every DUE reminder for today (oldest first), as NotificationProposal objects; [] when the
  learner is not eligible, or now is inside quiet hours (22:00-08:00 local), or nothing is due.
* ``run_streak_risk_sweep(application, dispatcher, learner_ids, prefs_by_learner, *, now_utc)``
  -> result with ``.outcomes``: one entry per learner (in input order) with ``.learner_id``,
  ``.fact`` (the dispatcher's TerminalFact, or None when nothing was handed over) and
  ``.reason`` (None when handed over; else one of "not_treatment", "no_streak",
  "already_active_today", "quiet_hours", "nothing_due"; step 7 adds the exclusion reasons). Only the LATEST due reminder is dispatched.

Groups: B variant gate, C due times, D proposal id, E proposal fields, F eligibility,
G quiet hours, H sweep idempotence, I real dispatcher at the cap, J DST / local-date offsets,
A the at-risk rule incl. the documented day-boundary behaviour.
"""

from datetime import datetime, timezone

import pytest

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
from src.notifications.streak_risk import (
    REMINDER_TIMES_LOCAL,
    plan_streak_risk_reminders,
    run_streak_risk_sweep,
)
from src.state_machine.learner import Learner
from tests.experiments.support import ASSIGNED_AT, EXP_ID, open_persisted, uuid_for_variant

IST = "Asia/Kolkata"
D = "2026-10-12"  # the local date used by most tests
YESTERDAY = "2026-10-11"
CAP = 2
# Created long before every simulated "now" below, so the 3-day tenure exclusion never applies
# (a Learner built with the real clock would be created after the Oct/Nov 2026 test instants).
CREATED = datetime(2026, 9, 1, tzinfo=timezone.utc)


def utc(y, mo, d, h, mi=0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def ist(h, mi=0, day=12) -> datetime:
    """An instant that reads h:mi on 2026-10-<day> in Asia/Kolkata (UTC+5:30, no DST)."""
    total = h * 60 + mi - 330
    day_shift, total = divmod(total, 24 * 60)
    return utc(2026, 10, day + day_shift, total // 60, total % 60)


def _learner(variant: str | None, active_dates=(YESTERDAY,), nth: int = 0) -> Learner:
    """A learner whose id hashes into `variant` (None: no assignment), with completions on
    `active_dates` (explicit activity dates, so the streak is the run of consecutive days)."""
    learner = Learner._create(
        Learner.Created, id=uuid_for_variant(variant or "variant_a", EXP_ID, nth=nth), timestamp=CREATED
    )
    if variant is not None:
        assert learner.assign_experiment(EXP_ID, assigned_at=ASSIGNED_AT)
        assert learner.experiment_assignments[EXP_ID]["variant"] == variant  # setup sanity
    for i, day in enumerate(active_dates):
        learner.complete_lesson(f"c-{i}", f"lesson-{i}", activity_date=day)
    return learner


def _prefs(learner: Learner, tz: str = IST, **kwargs) -> LearnerPreferences:
    return LearnerPreferences(str(learner.id), timezone=tz, **kwargs)


def _plan(learner, now, tz=IST, **kwargs):
    return plan_streak_risk_reminders(learner, _prefs(learner, tz), now, **kwargs)


def _hhmm_list(proposals):
    return [p.proposal_id.rsplit(":", 1)[1] for p in proposals]


# --- constant --------------------------------------------------------------------------


def test_reminder_times_constant():
    assert REMINDER_TIMES_LOCAL == ("20:00", "21:00", "21:30", "21:45")


# --- A: the at-risk rule -----------------------------------------------------------------


def test_a_streak_and_no_lesson_today_is_at_risk():
    learner = _learner("variant_a")
    assert learner.new_streak >= 1 and D not in learner.activity_dates  # setup sanity
    assert _hhmm_list(_plan(learner, ist(20, 30))) == ["2000"]


def test_a_streak_comes_from_the_learner_aggregate():
    learner = _learner("variant_a", active_dates=("2026-10-09", "2026-10-10", YESTERDAY))
    (proposal,) = _plan(learner, ist(20, 30))
    assert proposal.payload["streak_count"] == learner.new_streak == 3


def test_a_documents_day_boundary_follows_stored_activity_date_not_prefs_timezone():
    """Documents ACTUAL behaviour, not a fix.

    Stored activity dates use the timezone the completion was recorded with (here "UTC").
    The lesson below happened 2026-10-12T20:00Z = 01:30 IST on 2026-10-13, but it is stored
    as activity date 2026-10-12. For an IST learner at 21:00 IST on 2026-10-13 the planner
    compares the stored dates with the IST local date, finds no 2026-10-13, and treats the
    learner as at risk although they did a lesson earlier that IST day.
    """
    learner = Learner._create(
        Learner.Created, id=uuid_for_variant("variant_a", EXP_ID), timestamp=CREATED
    )
    learner.assign_experiment(EXP_ID, assigned_at=ASSIGNED_AT)
    learner.complete_lesson(
        "c-utc", "lesson-1", occurred_at=utc(2026, 10, 12, 20, 0), timezone="UTC"
    )
    assert learner.activity_dates == {"2026-10-12"}  # setup sanity: stored in UTC terms

    proposals = _plan(learner, ist(21, 0, day=13))

    assert _hhmm_list(proposals) == ["2000", "2100"]
    assert all(p.payload["local_date"] == "2026-10-13" for p in proposals)


# --- B: only the treatment variant of exp_007 -----------------------------------------


def test_b_treatment_learner_gets_reminders():
    assert _plan(_learner("variant_a"), ist(20, 30))


def test_b_control_learner_gets_none():
    assert _plan(_learner("variant_b"), ist(21, 10)) == []


def test_b_unassigned_learner_gets_none():
    assert _plan(_learner(None), ist(21, 10)) == []


def test_b_assignment_to_another_experiment_does_not_count():
    learner = _learner(None)
    learner.assign_experiment("exp_other", assigned_at=ASSIGNED_AT)
    assert _plan(learner, ist(21, 10)) == []


# --- C: due times; the sweep sends only the latest -----------------------------------


@pytest.mark.parametrize(
    "now, expected",
    [
        (ist(19, 59), []),
        (ist(20, 0), ["2000"]),
        (ist(20, 59), ["2000"]),
        (ist(21, 10), ["2000", "2100"]),
        (ist(21, 30), ["2000", "2100", "2130"]),
        (ist(21, 50), ["2000", "2100", "2130", "2145"]),
    ],
)
def test_c_plan_returns_every_due_reminder_oldest_first(now, expected):
    assert _hhmm_list(_plan(_learner("variant_a"), now)) == expected


def _dispatcher(tmp_path, **kwargs):
    store = NotificationStore(str(tmp_path / "notif.db"))
    adapter = _RecordingPush()
    dispatcher = NotificationDispatcher(
        store=store, adapters={ChannelType.PUSH: adapter}, **kwargs
    )
    return dispatcher, store, adapter


class _RecordingPush(SimulatedPushAdapter):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.proposals = []

    def send(self, proposal):
        self.proposals.append(proposal)
        return super().send(proposal)


def _persist(application, *learners):
    for learner in learners:
        application.save(learner)
    return [str(learner.id) for learner in learners]


@pytest.mark.parametrize(
    "now, latest",
    [(ist(20, 5), "2000"), (ist(21, 10), "2100"), (ist(21, 35), "2130"), (ist(21, 50), "2145")],
)
def test_c_sweep_sends_only_the_latest_due_reminder(tmp_path, now, latest):
    with open_persisted(tmp_path) as (application, _):
        learner = _learner("variant_a")
        (learner_id,) = _persist(application, learner)
        dispatcher, store, push = _dispatcher(tmp_path)
        try:
            result = run_streak_risk_sweep(
                application, dispatcher, [learner_id], {learner_id: _prefs(learner)}, now_utc=now
            )
            (outcome,) = result.outcomes
            assert outcome.learner_id == learner_id
            assert outcome.reason is None
            assert outcome.fact.status == NotificationStatus.DISPATCHED
            assert [p.proposal_id for p in push.proposals] == [
                f"streak_risk:{learner_id}:{D}:{latest}"
            ]
        finally:
            store.close()


# --- D / E: proposal identity and fields ----------------------------------------------


def test_d_proposal_id_format():
    learner = _learner("variant_a")
    proposals = _plan(learner, ist(21, 10))
    assert [p.proposal_id for p in proposals] == [
        f"streak_risk:{learner.id}:{D}:2000",
        f"streak_risk:{learner.id}:{D}:2100",
    ]


def test_e_proposal_fields():
    learner = _learner("variant_a", active_dates=("2026-10-10", YESTERDAY))
    proposals = _plan(learner, ist(21, 10))
    for proposal, hhmm, created in zip(
        proposals, ("2000", "2100"), ("2026-10-12T14:30:00Z", "2026-10-12T15:30:00Z")
    ):
        assert isinstance(proposal, NotificationProposal)
        assert proposal.learner_id == str(learner.id)
        assert proposal.channel == ChannelType.PUSH
        assert proposal.category == NotificationCategory.STREAK_RISK
        assert proposal.template_id == "streak_risk_v1"
        assert proposal.payload["streak_count"] == 2
        assert proposal.payload["local_date"] == D
        assert proposal.experiment_id == "exp_007"
        assert proposal.variant == "variant_a"
        assert proposal.created_at == created  # the scheduled instant in UTC, not "now"
        assert proposal.correlation_id == f"streak_risk:{D}"


def test_e_variant_is_read_from_the_learner_assignment():
    learner = _learner("variant_a")
    (proposal,) = _plan(learner, ist(20, 30))
    assert proposal.variant == learner.experiment_assignments[EXP_ID]["variant"]


# --- F: eligibility ---------------------------------------------------------------------


def test_f_lesson_completed_today_gets_nothing():
    learner = _learner("variant_a", active_dates=(YESTERDAY, D))
    assert _plan(learner, ist(21, 10)) == []


def test_f_no_streak_gets_nothing():
    learner = _learner("variant_a", active_dates=())
    assert learner.new_streak == 0  # setup sanity
    assert _plan(learner, ist(21, 10)) == []


def test_f_sweep_skip_reasons(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        control = _learner("variant_b", nth=0)
        unassigned = _learner(None, nth=1)
        done = _learner("variant_a", active_dates=(YESTERDAY, D), nth=2)
        no_streak = _learner("variant_a", active_dates=(), nth=3)
        ids = _persist(application, control, unassigned, done, no_streak)
        prefs = {str(l.id): _prefs(l) for l in (control, unassigned, done, no_streak)}
        dispatcher, store, push = _dispatcher(tmp_path)
        try:
            result = run_streak_risk_sweep(
                application, dispatcher, ids, prefs, now_utc=ist(21, 10)
            )
            assert [(o.learner_id, o.fact, o.reason) for o in result.outcomes] == [
                (ids[0], None, "not_treatment"),
                (ids[1], None, "not_treatment"),
                (ids[2], None, "already_active_today"),
                (ids[3], None, "no_streak"),
            ]
            assert push.proposals == []
        finally:
            store.close()


# --- G: quiet hours and the 20:00 floor -------------------------------------------------


@pytest.mark.parametrize("tz, offset_min", [(IST, 330), ("UTC", 0)])
@pytest.mark.parametrize(
    "local_h, local_m",
    [(0, 30), (3, 0), (7, 59), (8, 0), (12, 0), (19, 59), (22, 0), (22, 30), (23, 59)],
)
def test_g_nothing_outside_20_to_22_local(tz, offset_min, local_h, local_m):
    # local clock reading -> UTC instant on the local day 2026-10-12
    total = local_h * 60 + local_m - offset_min
    day_shift, total = divmod(total, 24 * 60)
    now = utc(2026, 10, 12 + day_shift, total // 60, total % 60)
    learner = _learner("variant_a")
    assert plan_streak_risk_reminders(learner, _prefs(learner, tz), now) == []


@pytest.mark.parametrize("tz, offset_min", [(IST, 330), ("UTC", 0)])
def test_g_reminders_are_generated_in_the_window_for_both_timezones(tz, offset_min):
    total = 21 * 60 + 10 - offset_min
    now = utc(2026, 10, 12, total // 60, total % 60)
    learner = _learner("variant_a")
    proposals = plan_streak_risk_reminders(learner, _prefs(learner, tz), now)
    assert _hhmm_list(proposals) == ["2000", "2100"]


def test_g_every_reminder_time_is_before_quiet_hours():
    assert REMINDER_TIMES_LOCAL and all("20:00" <= t < "22:00" for t in REMINDER_TIMES_LOCAL)


@pytest.mark.parametrize(
    "now, reason, last_active",
    [
        (ist(19, 59), "nothing_due", YESTERDAY),
        (ist(22, 30), "quiet_hours", YESTERDAY),
        # 03:00 on the 13th: the streak is only still extendable if the last lesson was on the
        # 12th (step 7 rule); with the 11th it would already be "streak_already_broken".
        (ist(3, 0, day=13), "quiet_hours", D),
    ],
)
def test_g_sweep_reports_why_nothing_was_sent(tmp_path, now, reason, last_active):
    with open_persisted(tmp_path) as (application, _):
        learner = _learner("variant_a", active_dates=(last_active,))
        (learner_id,) = _persist(application, learner)
        dispatcher, store, push = _dispatcher(tmp_path)
        try:
            result = run_streak_risk_sweep(
                application, dispatcher, [learner_id], {learner_id: _prefs(learner)}, now_utc=now
            )
            (outcome,) = result.outcomes
            assert (outcome.fact, outcome.reason) == (None, reason)
            assert push.proposals == []
        finally:
            store.close()


# --- H: idempotent sweeps ----------------------------------------------------------------


def test_h_sweep_twice_at_the_same_instant_sends_once(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        learner = _learner("variant_a")
        (learner_id,) = _persist(application, learner)
        prefs = {learner_id: _prefs(learner)}
        dispatcher, store, push = _dispatcher(tmp_path)
        try:
            first = run_streak_risk_sweep(application, dispatcher, [learner_id], prefs, now_utc=ist(21, 10))
            second = run_streak_risk_sweep(application, dispatcher, [learner_id], prefs, now_utc=ist(21, 10))

            assert first.outcomes[0].fact.status == NotificationStatus.DISPATCHED
            assert second.outcomes[0].fact.reason == "duplicate_proposal"
            assert len(push.proposals) == 1
        finally:
            store.close()


def test_h_a_later_sweep_sends_the_next_reminder_once(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        learner = _learner("variant_a")
        (learner_id,) = _persist(application, learner)
        prefs = {learner_id: _prefs(learner)}
        dispatcher, store, push = _dispatcher(tmp_path)
        try:
            for now in (ist(21, 10), ist(21, 20), ist(21, 31), ist(21, 31)):
                run_streak_risk_sweep(application, dispatcher, [learner_id], prefs, now_utc=now)
            assert _hhmm_list(push.proposals) == ["2100", "2130"]
        finally:
            store.close()


# --- I: real dispatcher at the cap --------------------------------------------------------


def test_i_treatment_learner_at_the_cap_is_suppressed_and_a_violation_recorded(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        learner = _learner("variant_a")
        (learner_id,) = _persist(application, learner)
        prefs = _prefs(learner, "UTC")
        dispatcher, store, push = _dispatcher(tmp_path, fatigue_cap=CAP)
        try:
            now = utc(2026, 10, 12, 21, 10)  # 21:10 UTC, outside quiet hours
            for i in range(CAP):
                filler = NotificationProposal(
                    proposal_id=f"fill-{i}",
                    learner_id=learner_id,
                    channel=ChannelType.PUSH,
                    category=NotificationCategory.GENERAL,
                    created_at="2026-10-12T21:00:00Z",
                )
                assert dispatcher.dispatch(filler, prefs=prefs, now_utc=now).status == (
                    NotificationStatus.DISPATCHED
                )  # setup sanity

            result = run_streak_risk_sweep(
                application, dispatcher, [learner_id], {learner_id: prefs}, now_utc=now
            )

            (outcome,) = result.outcomes
            assert outcome.fact.status == NotificationStatus.SUPPRESSED
            assert outcome.fact.reason == "daily_cap_exceeded"
            assert store.violation_counts_by_variant(EXP_ID) == {"variant_a": 1}
            (violation,) = store.list_guardrail_violations(experiment_id=EXP_ID)
            assert violation["proposal_id"] == f"streak_risk:{learner_id}:{D}:2100"
            assert violation["learner_id"] == learner_id
            assert len(push.proposals) == CAP  # only the fillers were delivered
        finally:
            store.close()


# --- J: a timezone whose local day differs from the UTC day; daylight saving -------------


NY = "America/New_York"


def test_j_new_york_local_date_differs_from_utc_date():
    # 2026-11-02T01:30Z is 2026-11-01 20:30 EST (DST ended 2026-11-01 06:00Z): UTC date is the 2nd.
    learner = _learner("variant_a", active_dates=("2026-10-31",))
    proposals = _plan(learner, utc(2026, 11, 2, 1, 30), tz=NY)
    assert [p.proposal_id for p in proposals] == [f"streak_risk:{learner.id}:2026-11-01:2000"]
    assert proposals[0].payload["local_date"] == "2026-11-01"
    assert proposals[0].correlation_id == "streak_risk:2026-11-01"
    # 20:00 EST on 2026-11-01 is 01:00Z on 2026-11-02 (standard time, UTC-5).
    assert proposals[0].created_at == "2026-11-02T01:00:00Z"


def test_j_new_york_lesson_today_by_local_date_gets_nothing():
    learner = _learner("variant_a", active_dates=("2026-10-31", "2026-11-01"))
    assert _plan(learner, utc(2026, 11, 2, 1, 30), tz=NY) == []  # 2026-11-02 is not "today" here


def test_j_new_york_before_dst_ends_uses_daylight_offset():
    # 2026-10-31 is still EDT (UTC-4): 21:00 local is 01:00Z on 2026-11-01.
    learner = _learner("variant_a", active_dates=("2026-10-30",))
    proposals = _plan(learner, utc(2026, 11, 1, 1, 10), tz=NY)  # 21:10 EDT on 2026-10-31
    assert _hhmm_list(proposals) == ["2000", "2100"]
    assert [p.created_at for p in proposals] == ["2026-11-01T00:00:00Z", "2026-11-01T01:00:00Z"]
    assert all(p.payload["local_date"] == "2026-10-31" for p in proposals)


def test_j_new_york_quiet_hours_follow_local_clock():
    learner = _learner("variant_a", active_dates=("2026-10-31",))
    # 2026-11-02T03:30Z = 22:30 EST on 2026-11-01: quiet hours, nothing generated.
    assert _plan(learner, utc(2026, 11, 2, 3, 30), tz=NY) == []
