"""M6 storage, service and replay safety on a real ingestion database."""

import random
import sqlite3
from datetime import timedelta

import pytest

from src.abuse.service import AbuseDetectionService, compare_runs
from src.abuse.store import AbuseSignalStore
from src.leaderboard.projection import LeaderboardProjection
from src.leaderboard.store import SQLiteLeaderboardStore
from tests.abuse.harness import EPOCH, Harness


@pytest.fixture
def harness(tmp_path):
    h = Harness(str(tmp_path / "events.db"))
    yield h
    h.close()


def service_for(h: Harness, **kwargs):
    store = AbuseSignalStore(h.db)
    return AbuseDetectionService(h.application, h.evidence, store, **kwargs), store


def seed_attackers(h: Harness) -> dict[str, str]:
    """A fast completer, a replay attacker and a quiet normal learner."""
    fast, replayer, normal = h.new_learner(), h.new_learner(), h.new_learner()
    for i in range(5):  # 5 s gaps -> impossible_timing
        h.complete(fast, EPOCH + timedelta(seconds=5 * i), lesson_id=f"f-{i}")
    event_ids = []
    for i in range(6):  # six accepted events, each re-sent once -> 6 distinct replays
        event_id = h.next_event_id()
        event_ids.append((event_id, f"r-{i}"))
        h.complete(replayer, EPOCH + timedelta(minutes=10 * i), lesson_id=f"r-{i}", event_id=event_id,
                   completion_id=f"rc-{i}")
    for event_id, name in event_ids:
        envelope = h.envelope(replayer, f"rc-{event_ids.index((event_id, name))}", event_id,
                              EPOCH + timedelta(minutes=10 * event_ids.index((event_id, name))),
                              lesson_id=name)
        h.deliver(envelope, EPOCH + timedelta(hours=2))
    for i in range(3):
        h.complete(normal, EPOCH + timedelta(minutes=30 * i), lesson_id=f"n-{i}")
    return {"fast": fast, "replayer": replayer, "normal": normal}


def snapshot(h: Harness, learners) -> dict:
    notifications = h.notifications()
    states = {}
    for learner_id in learners:
        l = h.learner(learner_id)
        states[learner_id] = (
            l.xp_total, l.new_streak, l.freezes_remaining, sorted(l.completed_lesson_ids),
            dict(l.eligible_lesson_counts), dict(l.applied_rule_versions), l.version,
        )
    evidence = [tuple(r.__dict__.values()) for r in h.evidence.list()]
    return {"events": len(notifications), "states": states, "evidence": evidence}


def leaderboard_scores(h: Harness, tmp_path, name: str) -> list:
    store = SQLiteLeaderboardStore(str(tmp_path / f"{name}.db"))
    projection = LeaderboardProjection(store)
    for n in h.notifications():
        projection.apply(n, offset=n.id)
    scores = sorted((e.subject_id, e.score) for e in store.get_epoch_entries("global", "2026-W42"))
    store.close()
    return scores


# -- store ---------------------------------------------------------------------


def test_store_is_idempotent_and_keeps_first_detected_at(harness):
    ids = seed_attackers(harness)
    service, store = service_for(harness)
    first = service.run()
    assert first.new_signals == len(first.signals) > 0 and first.existing_signals == 0
    rows_before = store.list()
    second = service.run()
    assert second.new_signals == 0 and second.existing_signals == len(second.signals)
    assert store.count() == len(first.signals)
    assert store.list() == rows_before
    assert [s.signal_id for s in first.signals] == [s.signal_id for s in second.signals]
    assert {r.learner_id for r in rows_before} == {ids["fast"], ids["replayer"]}


def test_store_rows_carry_every_field_and_a_passed_in_detected_at(harness):
    seed_attackers(harness)
    service, store = service_for(harness)
    summary = service.run(detected_at="2030-01-01T00:00:00+00:00")
    rows = store.list()
    assert len(rows) == len(summary.signals)
    for row, signal in zip(rows, summary.signals):
        assert row.signal_id == signal.signal_id
        assert (row.learner_id, row.signal_type, row.status) == (signal.learner_id, signal.signal_type, "suspected")
        assert (row.rule_id, row.rule_version, row.rule_digest) == (signal.rule_id, "v2", signal.rule_digest)
        assert row.evidence_refs == signal.evidence_refs
        assert row.detected_at == "2030-01-01T00:00:00+00:00"
        assert (row.window_start, row.window_end) == signal.detected_window
    stored_refs = sqlite3.connect(harness.db).execute("SELECT evidence_refs FROM abuse_signals").fetchall()
    assert all(r[0].startswith("[") and ", " not in r[0] for r in stored_refs)  # canonical JSON


def test_default_detected_at_comes_from_data_not_a_clock(harness):
    seed_attackers(harness)
    service, store = service_for(harness)
    service.run()
    stamps = {r.detected_at for r in store.list()}
    assert len(stamps) == 1
    assert stamps.pop() >= EPOCH.isoformat()


def test_store_filters_by_learner_and_rejects_bad_status(harness):
    ids = seed_attackers(harness)
    service, store = service_for(harness)
    service.run()
    assert {r.learner_id for r in store.list(ids["fast"])} == {ids["fast"]}
    assert store.list("nobody") == []
    with pytest.raises(sqlite3.IntegrityError):
        sqlite3.connect(harness.db).execute(
            "INSERT INTO abuse_signals (signal_id, learner_id, signal_type, status, rule_id, rule_version,"
            " rule_digest, evidence_refs, window_start, window_end, detected_at)"
            " VALUES ('x','l','replay','confirmed','r','v1','d','[\"a\"]','s','e','t')"
        )


# -- service: flag-only --------------------------------------------------------


def test_detection_changes_nothing_else(harness, tmp_path):
    ids = seed_attackers(harness)
    before = snapshot(harness, ids.values())
    scores_before = leaderboard_scores(harness, tmp_path, "lb_before")
    service, _ = service_for(harness)
    service.run()
    service.run()
    assert snapshot(harness, ids.values()) == before
    assert leaderboard_scores(harness, tmp_path, "lb_after") == scores_before
    assert scores_before  # the comparison was not vacuous


def test_service_summary_counts(harness):
    ids = seed_attackers(harness)
    service, _ = service_for(harness)
    summary = service.run()
    assert summary.counts_by_type == {"impossible_timing": 1, "replay": 1}
    assert summary.learners_scanned == 3
    assert summary.events_read == len(harness.notifications())
    assert summary.shadow is False
    assert {s.learner_id for s in summary.signals} == {ids["fast"], ids["replayer"]}


# -- replay safety -----------------------------------------------------------------


def test_signals_identical_after_full_rebuild(harness):
    seed_attackers(harness)
    service, store = service_for(harness)
    first = [s.signal_id for s in service.run().signals]
    store.close()
    harness.reopen()
    service2, store2 = service_for(harness)
    again = service2.run()
    assert [s.signal_id for s in again.signals] == first
    assert again.new_signals == 0
    assert store2.count() == len(first)
    # an empty signal store over the same history rebuilds the same ids
    other = AbuseSignalStore(harness.db.replace("events.db", "fresh.db"))
    fresh = AbuseDetectionService(harness.application, harness.evidence, other).run()
    assert [s.signal_id for s in fresh.signals] == first
    assert fresh.new_signals == len(first)


def test_signals_identical_after_replaying_event_history(harness):
    from eventsourcing.persistence import Mapper  # noqa: F401  (the service uses application.mapper)

    seed_attackers(harness)
    service, _ = service_for(harness)
    first = [s.signal_id for s in service.run().signals]
    # replay: rebuild every aggregate from its stored events, then detect again
    for n in harness.notifications():
        harness.application.repository.get(n.originator_id)
    assert [s.signal_id for s in service.run(shadow=True).signals] == first


# -- shadow --------------------------------------------------------------------------


def test_shadow_mode_writes_nothing(harness):
    seed_attackers(harness)
    service, store = service_for(harness)
    shadow = service.run(shadow=True)
    assert shadow.shadow is True and shadow.signals
    assert shadow.new_signals == 0 and shadow.existing_signals == 0
    assert store.count() == 0


def test_shadow_compares_two_rule_versions(harness):
    from dataclasses import replace
    from src.abuse.rules import get_abuse_rules

    seed_attackers(harness)
    service, store = service_for(harness)
    base = service.run(shadow=True)
    rules = get_abuse_rules("v1")
    looser = replace(rules, replay=replace(rules.replay, min_distinct_replayed_events=7))
    trial = service.run(shadow=True, rules_version=looser)
    diff = compare_runs(base, trial)
    assert diff["only_in_a"] == [(s.learner_id, s.rule_id) for s in base.signals if s.signal_type == "replay"]
    assert diff["only_in_b"] == []
    assert len(diff["in_both"]) == 1
    assert store.count() == 0


def test_unknown_rules_version_is_rejected(harness):
    with pytest.raises(KeyError):
        service_for(harness, rules_version="v9")


# -- robustness ------------------------------------------------------------------------


def test_empty_database_and_learner_without_events(harness):
    service, store = service_for(harness)
    empty = service.run()
    assert empty.signals == [] and empty.learners_scanned == 0 and store.count() == 0
    lonely = harness.new_learner()
    result = service.run([lonely])
    assert result.signals == [] and result.learners_scanned == 1


def test_learner_ids_restrict_the_scan(harness):
    ids = seed_attackers(harness)
    service, _ = service_for(harness)
    only_fast = service.run([ids["fast"]], shadow=True)
    assert {s.learner_id for s in only_fast.signals} == {ids["fast"]}
    assert only_fast.learners_scanned == 1


def test_result_does_not_depend_on_learner_order(harness):
    ids = seed_attackers(harness)
    service, _ = service_for(harness)
    wanted = list(ids.values())
    reference = [s.signal_id for s in service.run(wanted, shadow=True).signals]
    for seed in range(4):
        shuffled = wanted[:]
        random.Random(seed).shuffle(shuffled)
        assert [s.signal_id for s in service.run(shuffled, shadow=True).signals] == reference
