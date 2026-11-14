"""M5 step 1: experiment flag on notifications and guardrail-violation recording (AT-14 support).

A  NotificationProposal carries optional experiment_id / variant; the store keeps them on
   proposal, rescheduled and DLQ rows
B  a daily_cap_exceeded suppression of an experiment proposal records exactly one violation
C  recording the same (proposal_id, reason) twice stores one row
D  no experiment_id -> no violation
E  other suppression reasons (opt-out, quiet hours) record no violation
F  violation_counts_by_variant / list_guardrail_violations filter and count correctly
G  release_rescheduled and replay_dlq rebuild proposals that keep both fields
H  an old database (no new columns, no violations table) is migrated and old rows read as None
"""

import sqlite3

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
from src.notifications.queue import RetryScheduler
from src.notifications.store import NotificationStore

EXP = "exp-nudge-1"
DAYTIME = "2026-10-12T12:00:00Z"
QUIET = "2026-10-12T17:30:00Z"  # 23:00 IST, inside quiet hours 22-08
MORNING = "2026-10-13T03:30:00Z"  # 09:00 IST, after the rescheduled 08:00 IST release time
CAP = 2  # small cap so a test only needs two prior dispatches


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


def _flagged(proposal_id: str, variant: str = "treatment", learner_id: str = "L1"):
    return _proposal(proposal_id, learner_id, experiment_id=EXP, variant=variant)


class _RecordingPush(SimulatedPushAdapter):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.proposals = []

    def send(self, proposal):
        self.proposals.append(proposal)
        return super().send(proposal)


def _dispatcher(tmp_path, adapter=None, **kwargs):
    store = NotificationStore(str(tmp_path / "notif.db"))
    adapter = adapter or SimulatedPushAdapter()
    dispatcher = NotificationDispatcher(
        store=store,
        adapters={ChannelType.PUSH: adapter},
        quiet_start_hour=22,
        quiet_end_hour=8,
        fatigue_cap=CAP,
        **kwargs,
    )
    return dispatcher, store, adapter


def _fill_cap(dispatcher, learner_id: str = "L1", now=DAYTIME, timezone: str = "UTC"):
    """Uses up the learner's cap with unflagged proposals."""
    for i in range(CAP):
        fact = dispatcher.dispatch(
            _proposal(f"fill-{learner_id}-{i}", learner_id),
            prefs=LearnerPreferences(learner_id, timezone=timezone),
            now_utc=now,
        )
        assert fact.status == NotificationStatus.DISPATCHED  # setup sanity


def _failing_dispatcher(tmp_path):
    return _dispatcher(
        tmp_path,
        adapter=SimulatedPushAdapter(fail_attempts=5),
        retry_scheduler=RetryScheduler(max_retries=3, backoff_delay_sec=0.0),
    )


def _proposal_row(store, proposal_id):
    return store.connection.execute(
        "SELECT experiment_id, variant FROM notification_proposals WHERE proposal_id = ?",
        (proposal_id,),
    ).fetchone()


def _violation_row_count(store) -> int:
    return store.connection.execute(
        "SELECT COUNT(*) FROM notification_guardrail_violations"
    ).fetchone()[0]


# --- A: proposal fields and stored rows -----------------------------------------------


def test_a_proposal_experiment_fields_default_to_none():
    proposal = _proposal("p-a-default")
    assert proposal.experiment_id is None
    assert proposal.variant is None


def test_a_proposal_row_stores_experiment_fields(tmp_path):
    dispatcher, store, _ = _dispatcher(tmp_path)
    try:
        fact = dispatcher.dispatch(
            _flagged("p-a-row", "control"), prefs=LearnerPreferences("L1"), now_utc=DAYTIME
        )
        assert fact.status == NotificationStatus.DISPATCHED
        assert _proposal_row(store, "p-a-row") == (EXP, "control")
    finally:
        store.close()


def test_a_unflagged_proposal_row_reads_none(tmp_path):
    dispatcher, store, _ = _dispatcher(tmp_path)
    try:
        dispatcher.dispatch(_proposal("p-a-plain"), prefs=LearnerPreferences("L1"), now_utc=DAYTIME)
        assert _proposal_row(store, "p-a-plain") == (None, None)
    finally:
        store.close()


def test_a_rescheduled_row_stores_experiment_fields(tmp_path):
    dispatcher, store, _ = _dispatcher(tmp_path)
    try:
        dispatcher.dispatch(
            _flagged("p-a-resched"),
            prefs=LearnerPreferences("L1", timezone="Asia/Kolkata"),
            now_utc=QUIET,
        )
        entry = store.get_rescheduled_entry("p-a-resched")
        assert entry is not None
        assert (entry.get("experiment_id"), entry.get("variant")) == (EXP, "treatment")
    finally:
        store.close()


def test_a_dlq_row_stores_experiment_fields(tmp_path):
    dispatcher, store, _ = _failing_dispatcher(tmp_path)
    try:
        dispatcher.dispatch(
            _flagged("p-a-dlq"), prefs=LearnerPreferences("L1"), now_utc=DAYTIME
        )
        (entry,) = store.get_dlq_entries()
        assert (entry.get("experiment_id"), entry.get("variant")) == (EXP, "treatment")
    finally:
        store.close()


# --- B: cap hit records exactly one violation -----------------------------------------


def test_b_cap_hit_records_one_violation_for_the_variant(tmp_path):
    dispatcher, store, _ = _dispatcher(tmp_path)
    try:
        _fill_cap(dispatcher)
        fact = dispatcher.dispatch(
            _flagged("p-b-cap", "treatment"), prefs=LearnerPreferences("L1"), now_utc=DAYTIME
        )
        assert fact.status == NotificationStatus.SUPPRESSED
        assert fact.reason == "daily_cap_exceeded"

        violations = store.list_guardrail_violations()
        assert len(violations) == 1
        v = violations[0]
        assert v["experiment_id"] == EXP
        assert v["variant"] == "treatment"
        assert v["learner_id"] == "L1"
        assert v["proposal_id"] == "p-b-cap"
        assert v["reason"] == "daily_cap_exceeded"
        assert v["occurred_at"] == DAYTIME
        assert v["violation_id"]
    finally:
        store.close()


# --- C: idempotent recording ------------------------------------------------------------


def test_c_same_proposal_hit_twice_records_one_row(tmp_path):
    dispatcher, store, _ = _dispatcher(tmp_path)
    try:
        _fill_cap(dispatcher)
        proposal = _flagged("p-c-twice")
        dispatcher.dispatch(proposal, prefs=LearnerPreferences("L1"), now_utc=DAYTIME)
        dispatcher.dispatch(proposal, prefs=LearnerPreferences("L1"), now_utc=DAYTIME)
        assert _violation_row_count(store) == 1
    finally:
        store.close()


def test_c_store_record_twice_stores_one_row(tmp_path):
    store = NotificationStore(str(tmp_path / "notif.db"))
    try:
        for violation_id in ("v-1", "v-2"):
            store.record_guardrail_violation(
                violation_id=violation_id,
                experiment_id=EXP,
                variant="treatment",
                learner_id="L1",
                proposal_id="p-c-direct",
                reason="daily_cap_exceeded",
                occurred_at=DAYTIME,
            )
        assert _violation_row_count(store) == 1
    finally:
        store.close()


# --- D: no experiment_id -> no violation ---------------------------------------------


def test_d_cap_hit_without_experiment_records_none(tmp_path):
    dispatcher, store, _ = _dispatcher(tmp_path)
    try:
        _fill_cap(dispatcher)
        fact = dispatcher.dispatch(
            _proposal("p-d-plain"), prefs=LearnerPreferences("L1"), now_utc=DAYTIME
        )
        assert fact.reason == "daily_cap_exceeded"  # setup sanity: the cap really did suppress
        assert _violation_row_count(store) == 0
        assert store.list_guardrail_violations() == []
    finally:
        store.close()


# --- E: other reasons record nothing --------------------------------------------------


def test_e_opt_out_records_no_violation(tmp_path):
    dispatcher, store, _ = _dispatcher(tmp_path)
    try:
        prefs = LearnerPreferences("L1", opted_out_channels={ChannelType.PUSH})
        fact = dispatcher.dispatch(_flagged("p-e-optout"), prefs=prefs, now_utc=DAYTIME)
        assert fact.reason == "opt_out"
        assert _violation_row_count(store) == 0
    finally:
        store.close()


def test_e_quiet_hours_reschedule_records_no_violation(tmp_path):
    dispatcher, store, _ = _dispatcher(tmp_path)
    try:
        fact = dispatcher.dispatch(
            _flagged("p-e-quiet"),
            prefs=LearnerPreferences("L1", timezone="Asia/Kolkata"),
            now_utc=QUIET,
        )
        assert fact.status == NotificationStatus.QUEUED
        assert _violation_row_count(store) == 0
    finally:
        store.close()


def test_e_duplicate_suppression_records_no_violation(tmp_path):
    dispatcher, store, _ = _dispatcher(tmp_path)
    try:
        proposal = _flagged("p-e-dup")
        dispatcher.dispatch(proposal, prefs=LearnerPreferences("L1"), now_utc=DAYTIME)
        fact = dispatcher.dispatch(proposal, prefs=LearnerPreferences("L1"), now_utc=DAYTIME)
        assert fact.reason == "duplicate_proposal"
        assert _violation_row_count(store) == 0
    finally:
        store.close()


# --- F: reads --------------------------------------------------------------------------


def _record(store, n, experiment_id, variant, learner_id="L1", reason="daily_cap_exceeded"):
    store.record_guardrail_violation(
        violation_id=f"v-{n}",
        experiment_id=experiment_id,
        variant=variant,
        learner_id=learner_id,
        proposal_id=f"p-f-{n}",
        reason=reason,
        occurred_at=DAYTIME,
    )


def test_f_counts_by_variant(tmp_path):
    store = NotificationStore(str(tmp_path / "notif.db"))
    try:
        _record(store, 1, EXP, "treatment")
        _record(store, 2, EXP, "treatment")
        _record(store, 3, EXP, "control")
        _record(store, 4, "other-exp", "treatment")
        assert store.violation_counts_by_variant(EXP) == {"treatment": 2, "control": 1}
        assert store.violation_counts_by_variant("other-exp") == {"treatment": 1}
        assert store.violation_counts_by_variant("no-such-exp") == {}
    finally:
        store.close()


def test_f_list_filters_by_experiment_and_variant(tmp_path):
    store = NotificationStore(str(tmp_path / "notif.db"))
    try:
        _record(store, 1, EXP, "treatment")
        _record(store, 2, EXP, "control")
        _record(store, 3, "other-exp", "treatment")
        assert len(store.list_guardrail_violations()) == 3
        assert {v["proposal_id"] for v in store.list_guardrail_violations(experiment_id=EXP)} == {
            "p-f-1",
            "p-f-2",
        }
        only = store.list_guardrail_violations(experiment_id=EXP, variant="control")
        assert [v["proposal_id"] for v in only] == ["p-f-2"]
        by_variant = store.list_guardrail_violations(variant="treatment")
        assert {v["proposal_id"] for v in by_variant} == {"p-f-1", "p-f-3"}
    finally:
        store.close()


# --- G: rebuilt proposals keep the fields ----------------------------------------------


def test_g_release_rescheduled_keeps_fields_on_delivery(tmp_path):
    push = _RecordingPush()
    dispatcher, store, _ = _dispatcher(tmp_path, adapter=push)
    try:
        prefs = LearnerPreferences("L1", timezone="Asia/Kolkata")
        dispatcher.dispatch(_flagged("p-g-release", "control"), prefs=prefs, now_utc=QUIET)

        facts = dispatcher.release_rescheduled(now_utc=MORNING, prefs_provider={"L1": prefs})

        assert [f.status for f in facts] == [NotificationStatus.DISPATCHED]
        (sent,) = push.proposals
        assert (sent.experiment_id, sent.variant) == (EXP, "control")
    finally:
        store.close()


def test_g_release_rescheduled_cap_hit_records_violation_with_variant(tmp_path):
    dispatcher, store, _ = _dispatcher(tmp_path)
    try:
        prefs = LearnerPreferences("L1", timezone="Asia/Kolkata")
        dispatcher.dispatch(_flagged("p-g-rel-cap", "control"), prefs=prefs, now_utc=QUIET)
        _fill_cap(dispatcher, now=MORNING, timezone="Asia/Kolkata")  # cap used up before the release runs

        facts = dispatcher.release_rescheduled(now_utc=MORNING, prefs_provider={"L1": prefs})

        assert [f.reason for f in facts] == ["daily_cap_exceeded"]
        (v,) = store.list_guardrail_violations()
        assert (v["experiment_id"], v["variant"], v["proposal_id"]) == (EXP, "control", "p-g-rel-cap")
        assert v["occurred_at"] == MORNING
    finally:
        store.close()


def test_g_replay_dlq_keeps_fields_on_delivery(tmp_path):
    dispatcher, store, _ = _failing_dispatcher(tmp_path)
    try:
        prefs = LearnerPreferences("L1")
        dispatcher.dispatch(_flagged("p-g-replay", "control"), prefs=prefs, now_utc=DAYTIME)
        (entry,) = store.get_dlq_entries()

        push = _RecordingPush(fail_attempts=0)
        recovery = NotificationDispatcher(store=store, adapters={ChannelType.PUSH: push})
        fact = recovery.replay_dlq(entry["dlq_id"], prefs=prefs, now_utc=DAYTIME)

        assert fact.status == NotificationStatus.DISPATCHED
        (sent,) = push.proposals
        assert (sent.experiment_id, sent.variant) == (EXP, "control")
    finally:
        store.close()


def test_g_replay_dlq_cap_hit_records_violation_with_variant(tmp_path):
    dispatcher, store, _ = _failing_dispatcher(tmp_path)
    try:
        prefs = LearnerPreferences("L1")
        dispatcher.dispatch(_flagged("p-g-rep-cap", "control"), prefs=prefs, now_utc=DAYTIME)
        (entry,) = store.get_dlq_entries()

        recovery = NotificationDispatcher(
            store=store,
            adapters={ChannelType.PUSH: SimulatedPushAdapter(fail_attempts=0)},
            fatigue_cap=CAP,
        )
        _fill_cap(recovery)

        fact = recovery.replay_dlq(entry["dlq_id"], prefs=prefs, now_utc=DAYTIME)

        assert fact.reason == "daily_cap_exceeded"
        (v,) = store.list_guardrail_violations()
        assert (v["experiment_id"], v["variant"], v["proposal_id"]) == (EXP, "control", "p-g-rep-cap")
    finally:
        store.close()


# --- H: migration of an old database ----------------------------------------------------

# The CREATE TABLE statements as they were before this change (ids present, experiment
# columns and the violations table absent).
_OLD_SCHEMA = [
    """
    CREATE TABLE notification_proposals (
        proposal_id TEXT PRIMARY KEY, learner_id TEXT NOT NULL, channel TEXT NOT NULL,
        category TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL,
        correlation_id TEXT, causation_id TEXT
    )
    """,
    """
    CREATE TABLE notification_dispatches (
        dispatch_id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, learner_id TEXT NOT NULL,
        channel TEXT NOT NULL, category TEXT NOT NULL, dispatched_at TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE notification_suppressions (
        suppression_id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, learner_id TEXT NOT NULL,
        channel TEXT NOT NULL, category TEXT NOT NULL, suppressed_at TEXT NOT NULL,
        reason TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE notification_dlq (
        dlq_id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, learner_id TEXT NOT NULL,
        channel TEXT NOT NULL, category TEXT NOT NULL, failed_at TEXT NOT NULL,
        reason TEXT NOT NULL, attempts INTEGER NOT NULL, payload_json TEXT NOT NULL,
        resolved INTEGER NOT NULL DEFAULT 0, created_at TEXT DEFAULT '',
        template_id TEXT DEFAULT 'default_template', ttl_seconds INTEGER DEFAULT 86400,
        correlation_id TEXT, causation_id TEXT
    )
    """,
    """
    CREATE TABLE notification_rescheduled (
        proposal_id TEXT PRIMARY KEY, learner_id TEXT NOT NULL, channel TEXT NOT NULL,
        category TEXT NOT NULL, rescheduled_for TEXT NOT NULL, created_at TEXT NOT NULL,
        payload_json TEXT NOT NULL, released INTEGER NOT NULL DEFAULT 0,
        template_id TEXT DEFAULT 'default_template', ttl_seconds INTEGER DEFAULT 86400,
        correlation_id TEXT, causation_id TEXT
    )
    """,
]


def _build_old_db(path: str) -> None:
    conn = sqlite3.connect(path)
    for statement in _OLD_SCHEMA:
        conn.execute(statement)
    conn.execute(
        "INSERT INTO notification_proposals VALUES ('old-p', 'L1', 'push', 'remediation', ?, 'queued', NULL, NULL)",
        (DAYTIME,),
    )
    conn.execute(
        "INSERT INTO notification_rescheduled (proposal_id, learner_id, channel, category, "
        "rescheduled_for, created_at, payload_json) VALUES ('old-p', 'L1', 'push', 'remediation', ?, ?, '{}')",
        (MORNING, DAYTIME),
    )
    conn.execute(
        "INSERT INTO notification_dlq (dlq_id, proposal_id, learner_id, channel, category, "
        "failed_at, reason, attempts, payload_json) "
        "VALUES ('old-dlq', 'old-dlq-p', 'L1', 'push', 'remediation', ?, 'boom', 3, '{}')",
        (DAYTIME,),
    )
    conn.commit()
    conn.close()


def _columns(store, table):
    return {row[1] for row in store.connection.execute(f"PRAGMA table_info({table})")}


def test_h_old_database_gains_columns_and_table_and_old_rows_read_none(tmp_path):
    path = str(tmp_path / "old.db")
    _build_old_db(path)

    store = NotificationStore(path)
    try:
        for table in ("notification_proposals", "notification_rescheduled", "notification_dlq"):
            assert {"experiment_id", "variant"} <= _columns(store, table), table
        assert {
            "violation_id",
            "experiment_id",
            "variant",
            "learner_id",
            "proposal_id",
            "reason",
            "occurred_at",
        } <= _columns(store, "notification_guardrail_violations")

        assert _proposal_row(store, "old-p") == (None, None)
        resched = store.get_rescheduled_entry("old-p")
        assert (resched.get("experiment_id", "MISSING"), resched.get("variant", "MISSING")) == (None, None)
        dlq = store.get_dlq_entry("old-dlq")
        assert (dlq.get("experiment_id", "MISSING"), dlq.get("variant", "MISSING")) == (None, None)
        assert store.list_guardrail_violations() == []
    finally:
        store.close()


def test_h_reopening_a_migrated_database_is_safe(tmp_path):
    path = str(tmp_path / "old.db")
    _build_old_db(path)
    NotificationStore(path).close()
    store = NotificationStore(path)  # second open must not fail on duplicate columns
    try:
        assert store.violation_counts_by_variant(EXP) == {}
    finally:
        store.close()
