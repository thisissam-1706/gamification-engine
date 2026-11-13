"""M1 step 5: notifications carry correlation_id / causation_id.

I  NotificationProposal accepts both ids; every terminal outcome's TerminalFact carries them
J  the ids are stored (rescheduled row, DLQ row) and readable back through the store
K  release_rescheduled / replay_dlq produce facts that still carry the same ids
L  run_remediation_with_notification propagates the attempt delivery's ids to the proposal/fact
"""

from datetime import datetime, timezone

import pytest

from src.interventions.notification_hook import run_remediation_with_notification
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
from tests.interventions.test_checkpoint_attempt_ingestion import (
    _checkpoint_envelope,
    _ingest_checkpoint,
    _new_learner,
    _open,
)

CORR = "n-corr"
CAUSE = "n-cause"
DAYTIME = "2026-10-12T12:00:00Z"
QUIET = "2026-10-12T17:30:00Z"  # 23:00 IST, inside quiet hours 22-08
MORNING = "2026-10-13T03:30:00Z"  # 09:00 IST, after the rescheduled 08:00 IST release time


def _proposal(proposal_id: str, learner_id: str = "L1", **extra) -> NotificationProposal:
    return NotificationProposal(
        proposal_id=proposal_id,
        learner_id=learner_id,
        channel=ChannelType.PUSH,
        category=NotificationCategory.REMEDIATION,
        template_id="checkpoint_remediation_v1",
        payload={"module": "math_101"},
        created_at=DAYTIME,
        **extra,
    )


def _with_ids(proposal_id: str, **kwargs) -> NotificationProposal:
    return _proposal(proposal_id, correlation_id=CORR, causation_id=CAUSE, **kwargs)


def _ids(obj) -> tuple:
    """Reads both ids; a missing attribute reads as None so the failure says None != 'n-corr'."""
    return getattr(obj, "correlation_id", None), getattr(obj, "causation_id", None)


def _dispatcher(tmp_path, adapter=None, **kwargs):
    store = NotificationStore(str(tmp_path / "notif.db"))
    adapter = adapter or SimulatedPushAdapter()
    dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.PUSH: adapter},
        quiet_start_hour=22,
        quiet_end_hour=8,
        **kwargs,
    )
    return dispatcher, store, adapter


def _failing_dispatcher(tmp_path):
    return _dispatcher(
        tmp_path,
        adapter=SimulatedPushAdapter(fail_attempts=5),
        retry_scheduler=RetryScheduler(max_retries=3, backoff_delay_sec=0.0),
    )


# --- I: the proposal accepts ids; every outcome's fact carries them ---------------


def test_i_proposal_ids_default_to_none():
    proposal = _proposal("p-default")
    assert proposal.correlation_id is None
    assert proposal.causation_id is None


def test_i_dispatched_fact_carries_ids(tmp_path):
    dispatcher, store, _ = _dispatcher(tmp_path)
    try:
        fact = dispatcher.dispatch(
            _with_ids("p-i-dispatched"), prefs=LearnerPreferences("L1"), now_utc=DAYTIME
        )
        assert fact.status == NotificationStatus.DISPATCHED
        assert _ids(fact) == (CORR, CAUSE)
    finally:
        store.close()


def test_i_suppressed_fact_carries_ids(tmp_path):
    dispatcher, store, _ = _dispatcher(tmp_path)
    try:
        prefs = LearnerPreferences("L1", opted_out_channels={ChannelType.PUSH})
        fact = dispatcher.dispatch(_with_ids("p-i-suppressed"), prefs=prefs, now_utc=DAYTIME)
        assert fact.status == NotificationStatus.SUPPRESSED
        assert _ids(fact) == (CORR, CAUSE)
    finally:
        store.close()


def test_i_rescheduled_fact_carries_ids(tmp_path):
    dispatcher, store, _ = _dispatcher(tmp_path)
    try:
        fact = dispatcher.dispatch(
            _with_ids("p-i-rescheduled"),
            prefs=LearnerPreferences("L1", timezone="Asia/Kolkata"),
            now_utc=QUIET,
        )
        assert fact.status == NotificationStatus.QUEUED
        assert _ids(fact) == (CORR, CAUSE)
    finally:
        store.close()


def test_i_failed_fact_carries_ids(tmp_path):
    dispatcher, store, _ = _failing_dispatcher(tmp_path)
    try:
        fact = dispatcher.dispatch(
            _with_ids("p-i-failed"), prefs=LearnerPreferences("L1"), now_utc=DAYTIME
        )
        assert fact.status == NotificationStatus.FAILED
        assert _ids(fact) == (CORR, CAUSE)
    finally:
        store.close()


# --- J: the ids are stored ----------------------------------------------------------


def test_j_rescheduled_row_stores_ids(tmp_path):
    dispatcher, store, _ = _dispatcher(tmp_path)
    try:
        dispatcher.dispatch(
            _with_ids("p-j-rescheduled"),
            prefs=LearnerPreferences("L1", timezone="Asia/Kolkata"),
            now_utc=QUIET,
        )
        entry = store.get_rescheduled_entry("p-j-rescheduled")
        assert entry is not None
        assert (entry.get("correlation_id"), entry.get("causation_id")) == (CORR, CAUSE)
    finally:
        store.close()


def test_j_dlq_row_stores_ids(tmp_path):
    dispatcher, store, _ = _failing_dispatcher(tmp_path)
    try:
        dispatcher.dispatch(
            _with_ids("p-j-failed"), prefs=LearnerPreferences("L1"), now_utc=DAYTIME
        )
        (entry,) = store.get_dlq_entries()
        assert (entry.get("correlation_id"), entry.get("causation_id")) == (CORR, CAUSE)
    finally:
        store.close()


# --- K: round trip through release_rescheduled and replay_dlq -------------------------


def test_k_release_rescheduled_fact_keeps_ids(tmp_path):
    dispatcher, store, _ = _dispatcher(tmp_path)
    try:
        prefs = LearnerPreferences("L1", timezone="Asia/Kolkata")
        dispatcher.dispatch(_with_ids("p-k-release"), prefs=prefs, now_utc=QUIET)

        facts = dispatcher.release_rescheduled(now_utc=MORNING, prefs_provider={"L1": prefs})

        assert len(facts) == 1
        assert facts[0].status == NotificationStatus.DISPATCHED
        assert _ids(facts[0]) == (CORR, CAUSE)
    finally:
        store.close()


def test_k_replay_dlq_fact_keeps_ids(tmp_path):
    dispatcher, store, _ = _failing_dispatcher(tmp_path)
    try:
        prefs = LearnerPreferences("L1")
        dispatcher.dispatch(_with_ids("p-k-replay"), prefs=prefs, now_utc=DAYTIME)
        (entry,) = store.get_dlq_entries()

        recovery = NotificationDispatcher(
            store=store, adapters={ChannelType.PUSH: SimulatedPushAdapter(fail_attempts=0)}
        )
        fact = recovery.replay_dlq(entry["dlq_id"], prefs=prefs, now_utc=DAYTIME)

        assert fact.status == NotificationStatus.DISPATCHED
        assert _ids(fact) == (CORR, CAUSE)
    finally:
        store.close()


# --- L: remediation notification inherits the attempt delivery's ids -------------------


class _RecordingPush(SimulatedPushAdapter):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.proposals = []

    def send(self, proposal):
        self.proposals.append(proposal)
        return super().send(proposal)


def test_l_remediation_notification_carries_attempt_ids(tmp_path):
    attempt_event_id = "00000000-0000-4000-8000-00000000e001"
    now = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
    application, evidence = _open(str(tmp_path / "events.db"))
    store = NotificationStore(str(tmp_path / "notif.db"))
    push = _RecordingPush()
    dispatcher = NotificationDispatcher(store=store, adapters={ChannelType.PUSH: push})
    try:
        learner_id = _new_learner(application)
        envelope = _checkpoint_envelope(learner_id, "att-1", attempt_event_id)
        envelope["payload"]["passed"] = False
        envelope["correlation_id"] = "att-corr"
        assert _ingest_checkpoint(application, evidence, envelope).status == "accepted"

        decision, fact = run_remediation_with_notification(
            application,
            learner_id,
            "att-1",
            clock=lambda: now,
            dispatcher=dispatcher,
            prefs=LearnerPreferences(learner_id=learner_id),
        )

        assert decision.action == "assign"  # setup sanity
        assert fact is not None and fact.status == NotificationStatus.DISPATCHED
        expected = ("att-corr", attempt_event_id)
        assert _ids(fact) == expected

        # The proposal the dispatcher actually handled carries the ids ...
        assert len(push.proposals) == 1
        assert _ids(push.proposals[0]) == expected

        # ... and so does the stored proposal row.
        row = store.connection.execute(
            "SELECT correlation_id, causation_id FROM notification_proposals WHERE proposal_id = ?",
            ("remediation_notice:att-1",),
        ).fetchone()
        assert row == expected
    finally:
        store.close()
        evidence.close()
        application.close()
