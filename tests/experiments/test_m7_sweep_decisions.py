"""M7 step 2: the streak-risk sweep records decisions (exp_007). Written before the implementation.

Assumed change to ``run_streak_risk_sweep``: a new keyword ``store: ExperimentStore | None = None``.

With a store, one ``experiment_decisions`` row per learner outcome (read here with raw SQL):
exp_id, learner_id, variant, decided_at (= ``now_utc.isoformat()``), outcome, reason, proposal_id,
fact_event_id, fact_status.

* handed to the dispatcher: outcome "sent", reason NULL, proposal_id = the dispatched proposal's id,
  fact_event_id = fact.event_id, fact_status = fact.status.value;
* not handed over: outcome "skipped", reason = the sweep reason, proposal/fact columns NULL;
* a learner whose stored ``experiment_eligibility`` row has eligible=0 is skipped with reason
  ``"ineligible:<stored reason>"`` (also on ``SweepOutcome.reason``) and the dispatcher is not
  called, even if the live tenure/opt-out check would pass.

Assumptions: variant is the learner's stored assignment (also for skipped rows); a learner with no
stored eligibility row falls through to the existing checks (untested); the sweep returns the same
outcomes with or without a store, apart from the ineligible case.
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.experiments.store import ExperimentStore
from src.notifications.models import NotificationStatus, TerminalFact
from src.notifications.streak_risk import run_streak_risk_sweep
from src.state_machine.learner import Learner
from tests.experiments.support import EXP_ID, open_persisted
from tests.notifications.test_streak_risk import (
    D,
    YESTERDAY,
    _dispatcher,
    _learner,
    _persist,
    _prefs,
    ist,
)

COLUMNS = "exp_id, learner_id, variant, decided_at, outcome, reason, proposal_id, fact_event_id, fact_status"
NAMES = [c.strip() for c in COLUMNS.split(",")]


def _decisions(store: ExperimentStore) -> list[dict]:
    rows = store.connection.execute(
        f"SELECT {COLUMNS} FROM experiment_decisions ORDER BY learner_id, decided_at"
    ).fetchall()
    return [dict(zip(NAMES, r)) for r in rows]


def _by_learner(store: ExperimentStore) -> dict[str, dict]:
    return {r["learner_id"]: r for r in _decisions(store)}


def _sweep(application, dispatcher, learners, now, store=None, **kwargs):
    ids = [str(learner.id) for learner in learners]
    prefs = {str(learner.id): _prefs(learner) for learner in learners}
    return run_streak_risk_sweep(
        application, dispatcher, ids, prefs, now_utc=now, store=store, **kwargs
    )


def _eligibility_row(learner, *, eligible: bool, reason: str | None) -> dict:
    return {
        "exp_id": EXP_ID,
        "learner_id": str(learner.id),
        "variant": learner.experiment_assignments[EXP_ID]["variant"],
        "assigned_at": "2026-10-01T12:00:00+00:00",
        "eligible": int(eligible),
        "reason": reason,
        "opted_out_push": 0,
        "opted_out_streak_risk": 0,
        "timezone": "Asia/Kolkata",
        "tenure_days": 40,
    }


# 1 ---------------------------------------------------------------------------------------------


def test_sent_and_skipped_outcomes_each_get_one_decision_row(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        sent = _learner("variant_a")
        done = _learner("variant_a", active_dates=(YESTERDAY, D), nth=1)
        _persist(application, sent, done)
        dispatcher, notif_store, push = _dispatcher(tmp_path)
        store = ExperimentStore(":memory:")
        try:
            now = ist(21, 10)
            result = _sweep(application, dispatcher, [sent, done], now, store=store)
            rows = _by_learner(store)
            assert len(_decisions(store)) == 2

            fact = result.outcomes[0].fact
            row = rows[str(sent.id)]
            assert row["exp_id"] == EXP_ID
            assert row["variant"] == "variant_a"
            assert row["decided_at"] == now.isoformat()
            assert row["outcome"] == "sent"
            assert row["reason"] is None
            assert row["proposal_id"] == push.proposals[0].proposal_id
            assert row["fact_event_id"] == fact.event_id
            assert row["fact_status"] == NotificationStatus.DISPATCHED.value

            skipped = rows[str(done.id)]
            assert skipped["outcome"] == "skipped"
            assert skipped["reason"] == "already_active_today"
            assert skipped["proposal_id"] is None
            assert skipped["fact_event_id"] is None
            assert skipped["fact_status"] is None
        finally:
            store.close()
            notif_store.close()


def test_quiet_hours_skip_is_recorded_with_its_reason(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        learner = _learner("variant_a")
        _persist(application, learner)
        dispatcher, notif_store, push = _dispatcher(tmp_path)
        store = ExperimentStore(":memory:")
        try:
            _sweep(application, dispatcher, [learner], ist(23, 0), store=store)
            (row,) = _decisions(store)
            assert (row["outcome"], row["reason"]) == ("skipped", "quiet_hours")
            assert push.proposals == []
        finally:
            store.close()
            notif_store.close()


# 2 ---------------------------------------------------------------------------------------------


def test_store_none_behaves_as_before(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        sent = _learner("variant_a")
        control = _learner("variant_b", nth=1)
        _persist(application, sent, control)
        dispatcher, notif_store, push = _dispatcher(tmp_path)
        try:
            result = _sweep(application, dispatcher, [sent, control], ist(21, 10), store=None)
            first, second = result.outcomes
            assert first.reason is None
            assert first.fact.status == NotificationStatus.DISPATCHED
            assert second.fact is None and second.reason == "not_treatment"
            assert len(push.proposals) == 1
        finally:
            notif_store.close()


# 3 ---------------------------------------------------------------------------------------------


def test_rerun_with_same_now_adds_no_duplicate_rows(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        sent = _learner("variant_a")
        control = _learner("variant_b", nth=1)
        _persist(application, sent, control)
        dispatcher, notif_store, push = _dispatcher(tmp_path)
        store = ExperimentStore(":memory:")
        try:
            now = ist(21, 10)
            _sweep(application, dispatcher, [sent, control], now, store=store)
            before = _decisions(store)
            _sweep(application, dispatcher, [sent, control], now, store=store)
            assert len(before) == 2
            assert _decisions(store) == before
        finally:
            store.close()
            notif_store.close()


# 4 ---------------------------------------------------------------------------------------------


def test_stored_ineligible_learner_is_skipped_without_calling_the_dispatcher(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        # Live checks pass: treatment arm, created long ago, no opt-outs, streak at risk.
        learner = _learner("variant_a")
        _persist(application, learner)
        dispatcher, notif_store, push = _dispatcher(tmp_path)
        store = ExperimentStore(":memory:")
        try:
            store.record_eligibility(
                _eligibility_row(learner, eligible=False, reason="excluded_tenure")
            )
            result = _sweep(application, dispatcher, [learner], ist(21, 10), store=store)

            (outcome,) = result.outcomes
            assert outcome.fact is None
            assert outcome.reason == "ineligible:excluded_tenure"
            assert push.proposals == []
            assert not notif_store.is_proposal_seen(
                f"streak_risk:{learner.id}:{D}:2100"
            )
            (row,) = _decisions(store)
            assert row["outcome"] == "skipped"
            assert row["reason"] == "ineligible:excluded_tenure"
            assert row["proposal_id"] is None and row["fact_event_id"] is None
        finally:
            store.close()
            notif_store.close()


def test_stored_eligible_learner_is_still_sent(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        learner = _learner("variant_a")
        _persist(application, learner)
        dispatcher, notif_store, push = _dispatcher(tmp_path)
        store = ExperimentStore(":memory:")
        try:
            store.record_eligibility(_eligibility_row(learner, eligible=True, reason=None))
            _sweep(application, dispatcher, [learner], ist(21, 10), store=store)
            (row,) = _decisions(store)
            assert row["outcome"] == "sent"
            assert len(push.proposals) == 1
        finally:
            store.close()
            notif_store.close()


# 5 ---------------------------------------------------------------------------------------------


def test_control_learners_are_recorded_as_skipped_not_treatment(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        control = _learner("variant_b")
        _persist(application, control)
        dispatcher, notif_store, push = _dispatcher(tmp_path)
        store = ExperimentStore(":memory:")
        try:
            _sweep(application, dispatcher, [control], ist(21, 10), store=store)
            (row,) = _decisions(store)
            assert row["variant"] == "variant_b"
            assert (row["outcome"], row["reason"]) == ("skipped", "not_treatment")
            assert row["proposal_id"] is None
            assert push.proposals == []
        finally:
            store.close()
            notif_store.close()


# Edge cases -----------------------------------------------------------------------------------


def test_sweep_at_a_different_now_adds_new_rows(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        learner = _learner("variant_a")
        _persist(application, learner)
        dispatcher, notif_store, push = _dispatcher(tmp_path)
        store = ExperimentStore(":memory:")
        try:
            _sweep(application, dispatcher, [learner], ist(20, 10), store=store)
            _sweep(application, dispatcher, [learner], ist(21, 10), store=store)
            rows = _decisions(store)
            assert len(rows) == 2
            assert {r["decided_at"] for r in rows} == {ist(20, 10).isoformat(), ist(21, 10).isoformat()}
            assert {r["proposal_id"].rsplit(":", 1)[1] for r in rows} == {"2000", "2100"}
        finally:
            store.close()
            notif_store.close()


class _FixedStatusDispatcher:
    """Returns a terminal fact with a fixed status for every proposal."""

    def __init__(self, status: NotificationStatus):
        self.status = status
        self.calls = 0

    def dispatch(self, proposal, prefs=None, now_utc=None):
        self.calls += 1
        return TerminalFact(
            event_id=f"evt-{self.calls}",
            event_type=f"notification_{self.status.value}",
            proposal_id=proposal.proposal_id,
            learner_id=proposal.learner_id,
            channel=proposal.channel.value,
            category=proposal.category.value,
            timestamp="2026-10-12T15:40:00Z",
            status=self.status,
            reason="test",
        )


@pytest.mark.parametrize("status", [NotificationStatus.SUPPRESSED, NotificationStatus.FAILED])
def test_suppressed_or_failed_fact_is_recorded_as_sent_with_that_status(tmp_path, status):
    with open_persisted(tmp_path) as (application, _):
        learner = _learner("variant_a")
        _persist(application, learner)
        dispatcher = _FixedStatusDispatcher(status)
        store = ExperimentStore(":memory:")
        try:
            result = _sweep(application, dispatcher, [learner], ist(21, 10), store=store)
            (row,) = _decisions(store)
            assert row["outcome"] == "sent"
            assert row["reason"] is None
            assert row["fact_status"] == status.value
            assert row["fact_event_id"] == result.outcomes[0].fact.event_id == "evt-1"
            assert row["proposal_id"] == f"streak_risk:{learner.id}:{D}:2100"
        finally:
            store.close()


def test_no_stored_eligibility_row_falls_through_to_normal_evaluation(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        ok = _learner("variant_a")
        young = _learner("variant_a", nth=1)
        young_created = ist(21, 10) - timedelta(days=1)
        young = _young(young, young_created)
        _persist(application, ok, young)
        dispatcher, notif_store, push = _dispatcher(tmp_path)
        store = ExperimentStore(":memory:")
        try:
            # An unrelated learner's row exists; neither of these learners has one.
            other = _learner("variant_a", nth=2)
            store.record_eligibility(_eligibility_row(other, eligible=False, reason="excluded_tenure"))
            result = _sweep(application, dispatcher, [ok, young], ist(21, 10), store=store)
            first, second = result.outcomes
            assert first.reason is None and first.fact is not None
            assert second.reason == "excluded_tenure"  # the live check, not "ineligible:..."
            rows = _by_learner(store)
            assert rows[str(ok.id)]["outcome"] == "sent"
            assert rows[str(young.id)]["reason"] == "excluded_tenure"
        finally:
            store.close()
            notif_store.close()


def _young(learner: Learner, created: datetime) -> Learner:
    """Same id/assignment/activity as `learner` but created at `created` (tenure under 3 days)."""
    fresh = Learner._create(Learner.Created, id=learner.id, timestamp=created)
    fresh.assign_experiment(EXP_ID, assigned_at=created)
    fresh.complete_lesson("c-0", "lesson-0", activity_date=YESTERDAY)
    return fresh


def test_decision_rows_survive_store_reopen(tmp_path):
    db = str(tmp_path / "experiments.db")
    with open_persisted(tmp_path) as (application, _):
        sent = _learner("variant_a")
        control = _learner("variant_b", nth=1)
        _persist(application, sent, control)
        dispatcher, notif_store, push = _dispatcher(tmp_path)
        store = ExperimentStore(db)
        try:
            now = ist(21, 10)
            _sweep(application, dispatcher, [sent, control], now, store=store)
            before = _decisions(store)
            store.close()
            reopened = ExperimentStore(db)
            try:
                assert _decisions(reopened) == before
                assert len(before) == 2
                _sweep(application, dispatcher, [sent, control], now, store=reopened)
                assert _decisions(reopened) == before
            finally:
                reopened.close()
        finally:
            notif_store.close()
