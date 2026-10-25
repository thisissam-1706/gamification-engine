"""Tests for Leaderboard rehydration idempotency, cohort single-row atomicity, and derived rebuilds.

Specifications:
- rehydrate twice (and five times) gives identical in-memory state and the correct first_reached_at.
- Reassigning a cohort after a simulated restart (new projection, no rehydrate) leaves exactly one cohort row for the learner.
- rebuild keeps audit rows and rebuilds everything else to the same result as a fresh replay.
- store.clear_derived preserves reconciliation audits, while wipe_all_for_tests removes them.
- assign_cohort removes pre-existing orphan duplicate cohort rows.
"""

import pytest

from src.leaderboard.models import ReconciliationAudit
from src.leaderboard.projection import LeaderboardProjection
from src.leaderboard.store import InMemoryLeaderboardStore, SQLiteLeaderboardStore
from src.state_machine.learner import Learner, LearnerApplication


def test_rehydrate_idempotency_and_first_reached_at(tmp_path):
    """Proves rehydrate() is strictly idempotent: calling 2x and 5x leaves state identical.

    Specifically verifies that history is replaced rather than appended,
    preventing the bug where doubled events caused 250 XP reached at 11:00
    to compute first_reached_at at 10:00.
    """
    app = LearnerApplication(
        env={
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(tmp_path / "events1.db"),
        }
    )
    db_path = str(tmp_path / "rehydrate_idempotent.db")
    store = SQLiteLeaderboardStore(db_path)
    proj = LeaderboardProjection(store)
    epoch = "2026-W42"

    l = Learner()
    # 09:00: +100 XP (total 100)
    l.complete_lesson("c1", "l1", xp_amount=100, occurred_at="2026-10-12T09:00:00Z", timezone="UTC")
    # 10:00: +100 XP (total 200)
    l.complete_lesson("c2", "l2", xp_amount=100, occurred_at="2026-10-12T10:00:00Z", timezone="UTC")
    # 11:00: +50 XP  (total 250) -> 250 XP first reached at 11:00!
    l.complete_lesson("c3", "l3", xp_amount=50, occurred_at="2026-10-12T11:00:00Z", timezone="UTC")
    app.save(l)

    subject_id = str(l.id)
    events = list(app.events.get(l.id))
    for idx, ev in enumerate(events):
        proj.apply(ev, offset=idx + 1)

    # Verify pre-restart state
    key = (subject_id, epoch)
    assert proj._epoch_scores[key] == 250
    assert proj._first_reached[key] == "2026-10-12T11:00:00Z"
    assert len(proj._subject_events[key]) == 3
    assert proj.compute_first_reached_at(subject_id, epoch, 250) == "2026-10-12T11:00:00Z"

    # Simulate fresh projection over the same database
    fresh_proj = LeaderboardProjection(store)
    assert len(fresh_proj._epoch_scores) == 0
    assert len(fresh_proj._subject_events) == 0

    # 1. First rehydrate
    fresh_proj.rehydrate(epoch)
    snap1_scores = dict(fresh_proj._epoch_scores)
    snap1_first = dict(fresh_proj._first_reached)
    snap1_events = {k: list(v) for k, v in fresh_proj._subject_events.items()}
    snap1_watermark = fresh_proj._watermark

    assert snap1_scores[key] == 250
    assert snap1_first[key] == "2026-10-12T11:00:00Z"
    assert len(snap1_events[key]) == 3
    assert fresh_proj.compute_first_reached_at(subject_id, epoch, 250) == "2026-10-12T11:00:00Z"

    # 2. Second rehydrate (calling twice must produce identical state, NOT double history)
    fresh_proj.rehydrate(epoch)
    snap2_scores = dict(fresh_proj._epoch_scores)
    snap2_first = dict(fresh_proj._first_reached)
    snap2_events = {k: list(v) for k, v in fresh_proj._subject_events.items()}

    assert snap2_scores == snap1_scores
    assert snap2_first == snap1_first
    assert snap2_events == snap1_events
    assert fresh_proj._watermark == snap1_watermark
    # If history had doubled, running sum would be 100(09:00)+100(09:00)+100(10:00) = 300 >= 250 at 10:00
    assert len(fresh_proj._subject_events[key]) == 3
    assert fresh_proj.compute_first_reached_at(subject_id, epoch, 250) == "2026-10-12T11:00:00Z"

    # 3. Five rehydrates in total
    for _ in range(3):
        fresh_proj.rehydrate(epoch)

    snap5_scores = dict(fresh_proj._epoch_scores)
    snap5_first = dict(fresh_proj._first_reached)
    snap5_events = {k: list(v) for k, v in fresh_proj._subject_events.items()}

    assert snap5_scores == snap1_scores
    assert snap5_first == snap1_first
    assert snap5_events == snap1_events
    assert len(fresh_proj._subject_events[key]) == 3
    assert fresh_proj.compute_first_reached_at(subject_id, epoch, 250) == "2026-10-12T11:00:00Z"

    # 4. Explicit list of epochs also works identically
    fresh_proj.rehydrate([epoch])
    assert dict(fresh_proj._epoch_scores) == snap1_scores
    assert dict(fresh_proj._first_reached) == snap1_first
    assert len(fresh_proj._subject_events[key]) == 3

    # 5. Passing None or empty sequence is prohibited
    with pytest.raises(ValueError, match="epoch or sequence of epochs must be explicitly passed"):
        fresh_proj.rehydrate(None)  # type: ignore

    with pytest.raises(ValueError, match="epoch sequence cannot be empty"):
        fresh_proj.rehydrate([])

    store.close()
    app.close()


def test_reassign_cohort_after_restart_without_rehydrate(tmp_path):
    """Proves reassigning cohort after restart without rehydrate leaves exactly one cohort row."""
    app = LearnerApplication(
        env={
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(tmp_path / "events2.db"),
        }
    )
    db_path = str(tmp_path / "restart_cohort.db")
    store = SQLiteLeaderboardStore(db_path)
    proj1 = LeaderboardProjection(store)
    epoch = "2026-W42"

    # 1. Initial cohort assignment to cohort_alpha and award 150 XP
    l = Learner()
    l.assign_cohort("cohort_alpha", epoch, assigned_at="2026-10-12T08:00:00Z")
    l.complete_lesson("c1", "l1", xp_amount=150, occurred_at="2026-10-12T08:30:00Z", timezone="UTC")
    app.save(l)

    subject_id = str(l.id)
    events = list(app.events.get(l.id))
    for idx, ev in enumerate(events):
        proj1.apply(ev, offset=idx + 1)

    # Verify initial membership in store
    cohorts_initial = store.get_member_cohorts(epoch, subject_id)
    assert cohorts_initial == ["cohort_alpha"]
    entry_alpha = store.get_entry("cohort", "cohort_alpha", epoch, subject_id)
    assert entry_alpha is not None
    assert entry_alpha.score == 150

    # 2. Simulate restart: instantiate new projection without calling rehydrate()
    proj2 = LeaderboardProjection(store)
    assert len(proj2._cohort_memberships) == 0  # In-memory map is completely unpopulated

    # 3. Reassign to cohort_beta via projection public API
    proj2.assign_cohort(
        subject_id=subject_id,
        cohort_id="cohort_beta",
        epoch=epoch,
        assigned_at="2026-10-12T09:00:00Z",
        watermark_offset=len(events) + 1,
    )

    # 4. Assert exactly ONE cohort row exists in storage
    cohorts_after = store.get_member_cohorts(epoch, subject_id)
    assert cohorts_after == ["cohort_beta"]
    assert len(cohorts_after) == 1

    # Old cohort row is gone
    assert store.get_entry("cohort", "cohort_alpha", epoch, subject_id) is None

    # New cohort row carries forward the learner's 150 XP
    entry_beta = store.get_entry("cohort", "cohort_beta", epoch, subject_id)
    assert entry_beta is not None
    assert entry_beta.score == 150
    assert entry_beta.scope_id == "cohort_beta"

    # 5. Reassign once more to cohort_gamma via applied domain event
    ev_reassign = Learner.CohortAssigned(
        originator_id=l.id,
        originator_version=len(events) + 2,
        timestamp=events[-1].timestamp,
        cohort_id="cohort_gamma",
        membership_epoch=epoch,
        grouping_rule_version="v1",
        assigned_at="2026-10-12T09:30:00Z",
    )
    proj2.apply(ev_reassign, offset=len(events) + 2)

    cohorts_gamma = store.get_member_cohorts(epoch, subject_id)
    assert cohorts_gamma == ["cohort_gamma"]
    assert len(cohorts_gamma) == 1
    assert store.get_entry("cohort", "cohort_beta", epoch, subject_id) is None
    entry_gamma = store.get_entry("cohort", "cohort_gamma", epoch, subject_id)
    assert entry_gamma is not None
    assert entry_gamma.score == 150

    store.close()
    app.close()


def test_reassign_cohort_purges_preexisting_orphan_duplicates(tmp_path):
    """Proves assign_cohort removes pre-existing orphan duplicate rows in a single atomic transaction."""
    db_path = str(tmp_path / "orphan_purge.db")
    store = SQLiteLeaderboardStore(db_path)
    proj = LeaderboardProjection(store)
    epoch = "2026-W42"
    subject_id = "L_orphan_target"

    # Pre-populate global score
    store.upsert_score(
        scope_type="global",
        scope_id="global",
        epoch=epoch,
        subject_id=subject_id,
        score=300,
        first_reached_at="2026-10-12T08:00:00Z",
        updated_at="2026-10-12T08:00:00Z",
    )

    # Artificially inject multiple orphan cohort rows for this learner and epoch
    store.upsert_score(
        scope_type="cohort",
        scope_id="cohort_orphan_1",
        epoch=epoch,
        subject_id=subject_id,
        score=300,
        first_reached_at="2026-10-12T08:00:00Z",
        updated_at="2026-10-12T08:00:00Z",
    )
    store.upsert_score(
        scope_type="cohort",
        scope_id="cohort_orphan_2",
        epoch=epoch,
        subject_id=subject_id,
        score=300,
        first_reached_at="2026-10-12T08:00:00Z",
        updated_at="2026-10-12T08:00:00Z",
    )
    store.upsert_score(
        scope_type="cohort",
        scope_id="cohort_orphan_3",
        epoch=epoch,
        subject_id=subject_id,
        score=300,
        first_reached_at="2026-10-12T08:00:00Z",
        updated_at="2026-10-12T08:00:00Z",
    )

    assert len(store.get_member_cohorts(epoch, subject_id)) == 3

    # Reassign to canonical cohort_target
    proj.assign_cohort(
        subject_id=subject_id,
        cohort_id="cohort_target",
        epoch=epoch,
        assigned_at="2026-10-12T09:00:00Z",
        watermark_offset=10,
    )

    # Assert that ALL orphan rows were purged, leaving exactly one cohort row
    cohorts = store.get_member_cohorts(epoch, subject_id)
    assert cohorts == ["cohort_target"]
    assert len(cohorts) == 1

    entry = store.get_entry("cohort", "cohort_target", epoch, subject_id)
    assert entry is not None
    assert entry.score == 300

    store.close()


def test_rebuild_preserves_audits_and_matches_fresh_replay(tmp_path):
    """Proves rebuild() clears only derived tables, preserves audit logs, and matches replay."""
    app = LearnerApplication(
        env={
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(tmp_path / "events3.db"),
        }
    )
    db_path = str(tmp_path / "rebuild_audit.db")
    store = SQLiteLeaderboardStore(db_path)
    proj = LeaderboardProjection(store)
    epoch = "2026-W42"

    # 1. Establish persistent reconciliation audit records
    audit1 = ReconciliationAudit(
        audit_id="audit_w42_001",
        scope_type="cohort",
        scope_id="cohort_main",
        epoch=epoch,
        audited_at="2026-10-12T07:00:00Z",
        canonical_count=20,
        projection_count=18,
        divergence_count=2,
        inserted_count=1,
        updated_count=1,
        deleted_count=0,
        corrected_subject_ids=("learner_1", "learner_2"),
        duration_ms=4.5,
    )
    audit2 = ReconciliationAudit(
        audit_id="audit_w42_002",
        scope_type="global",
        scope_id="global",
        epoch=epoch,
        audited_at="2026-10-12T07:30:00Z",
        canonical_count=25,
        projection_count=25,
        divergence_count=0,
        inserted_count=0,
        updated_count=0,
        deleted_count=0,
        corrected_subject_ids=(),
        duration_ms=3.2,
    )
    store.record_reconciliation_audit(audit1)
    store.record_reconciliation_audit(audit2)

    assert len(store.get_reconciliation_audits()) == 2

    # 2. Build stream of real domain events
    l1 = Learner()
    l1.assign_cohort("cohort_main", epoch, assigned_at="2026-10-12T08:00:00Z")
    l1.complete_lesson("c1", "l1", xp_amount=100, occurred_at="2026-10-12T08:30:00Z", timezone="UTC")

    l2 = Learner()
    l2.assign_cohort("cohort_main", epoch, assigned_at="2026-10-12T08:00:00Z")
    l2.complete_lesson("c2", "l2", xp_amount=200, occurred_at="2026-10-12T08:35:00Z", timezone="UTC")

    app.save(l1)
    app.save(l2)

    events = list(app.events.get(l1.id)) + list(app.events.get(l2.id))

    # Initial playback of events
    for pos, ev in enumerate(events, start=1):
        proj.apply(ev, offset=pos)

    # Snapshot pre-rebuild derived state
    entries_before = store.get_epoch_entries("global", epoch)
    cohort_before = store.get_epoch_entries("cohort", epoch)
    applied_events_before = store.get_applied_events(epoch=epoch)
    watermark_before = store.get_committed_offset("global")
    audits_before = store.get_reconciliation_audits()

    assert len(entries_before) == 2
    assert len(cohort_before) == 2
    assert watermark_before == len(events)
    assert len(audits_before) == 2

    # 3. Rebuild projection from genesis event stream
    proj.rebuild(events)

    # 4. Assert audits survived untouched
    audits_after = store.get_reconciliation_audits()
    assert len(audits_after) == 2
    assert audits_after == audits_before
    assert audits_after[0].audit_id == "audit_w42_001"
    assert audits_after[1].audit_id == "audit_w42_002"

    # 5. Assert derived tables match fresh replay exactly
    entries_after = store.get_epoch_entries("global", epoch)
    cohort_after = store.get_epoch_entries("cohort", epoch)
    applied_events_after = store.get_applied_events(epoch=epoch)
    watermark_after = store.get_committed_offset("global")

    assert entries_after == entries_before
    assert cohort_after == cohort_before
    assert applied_events_after == applied_events_before
    assert watermark_after == watermark_before

    store.close()
    app.close()


@pytest.mark.parametrize("store_cls", [SQLiteLeaderboardStore, InMemoryLeaderboardStore])
def test_store_clear_derived_vs_wipe_all_for_tests(tmp_path, store_cls):
    """Proves clear_derived() preserves audits while wipe_all_for_tests() clears everything."""
    if store_cls is SQLiteLeaderboardStore:
        store = SQLiteLeaderboardStore(str(tmp_path / "clear_test.db"))
    else:
        store = InMemoryLeaderboardStore()

    # Seed an audit row and a derived row
    audit = ReconciliationAudit(
        audit_id="audit_test",
        scope_type="global",
        scope_id="global",
        epoch="2026-W42",
        audited_at="2026-10-12T10:00:00Z",
        canonical_count=10,
        projection_count=10,
        divergence_count=0,
        inserted_count=0,
        updated_count=0,
        deleted_count=0,
        duration_ms=1.0,
    )
    store.record_reconciliation_audit(audit)
    store.upsert_score(
        scope_type="global",
        scope_id="global",
        epoch="2026-W42",
        subject_id="L1",
        score=100,
        first_reached_at="2026-10-12T10:00:00Z",
        updated_at="2026-10-12T10:00:00Z",
    )

    assert len(store.get_reconciliation_audits()) == 1
    assert store.get_entry("global", "global", "2026-W42", "L1") is not None

    # clear_derived() clears derived entries but preserves audits
    store.clear_derived()
    assert store.get_entry("global", "global", "2026-W42", "L1") is None
    assert len(store.get_reconciliation_audits()) == 1

    # wipe_all_for_tests() clears audits as well
    store.wipe_all_for_tests()
    assert len(store.get_reconciliation_audits()) == 0

    store.close()
