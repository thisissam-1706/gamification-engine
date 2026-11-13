"""Edge cases for build_weekly_cohorts (M3 cohort builder, step 3).

1 idempotent re-run          5 lesson ids without "/"
2 inactive learners          6 replay gives the same cohorts and ranks
3 min_size merge / max_size  7 SQLite vs in-memory store parity
4 sealed or finalized cohort

Assumed additions to the build result (see docs/audit/m3-cohort-step3-edges.md):
``result.inactive`` (learner ids excluded as inactive) and ``result.definitions``
(cohort_id -> CohortDefinition for every cohort the build produced).
Tests that need exact cohort sizes pin the hash buckets to a constant by patching the two
``assign_*`` names inside ``src.leaderboard.cohort_builder``; cohorts then differ only by tier
and module.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from src.leaderboard.cohorts import is_inactive
from src.leaderboard.models import CohortDefinition
from src.leaderboard.projection import LeaderboardProjection
from src.leaderboard.store import InMemoryLeaderboardStore, SQLiteLeaderboardStore
from src.state_machine.learner import LearnerApplication
from tests.leaderboard.test_cohort_builder import (
    EPOCH,
    OLDER_WEEK_START,
    PRIOR_WEEK_START,
    REFERENCE,
    THIS_WEEK_START,
    _app,
    _assert_key,
    _learner,
    _week,
)
from tests.leaderboard.test_leaderboard_e2e import read_all_notifications


def _run(app, learner_ids, *, min_size=1, max_size=5000, epoch=EPOCH, reference_time=REFERENCE):
    # Imported here so a missing module fails each test with a clear ImportError.
    from src.leaderboard.cohort_builder import build_weekly_cohorts

    return build_weekly_cohorts(
        app, learner_ids, epoch, min_size=min_size, max_size=max_size, reference_time=reference_time
    )


def _events(app, learner_id) -> list:
    return list(app.events.get(UUID(learner_id)))


def _cohort_assigned(app, learner_id) -> list:
    return [e for e in _events(app, learner_id) if type(e).__name__ == "CohortAssigned"]


def _fixed_buckets(monkeypatch):
    """Pins both hash buckets so cohorts differ only by tier and module."""
    import src.leaderboard.cohort_builder as builder

    real_activity = builder.assign_activity_volume_cohort
    real_module = builder.assign_module_cohort
    monkeypatch.setattr(
        builder,
        "assign_activity_volume_cohort",
        lambda subject_id, epoch, n, *a, **k: real_activity("fixed", epoch, n, *a, **k),
    )
    monkeypatch.setattr(
        builder,
        "assign_module_cohort",
        lambda subject_id, epoch, module, *a, **k: real_module("fixed", epoch, module, *a, **k),
    )


# --- 1. re-running changes nothing ----------------------------------------------------


def test_1_second_run_changes_nothing_and_stores_no_extra_events(tmp_path):
    app = _app(tmp_path)
    try:
        ids = [_learner(app, _week(PRIOR_WEEK_START, n)) for n in (1, 4, 11)]

        first = _run(app, ids)
        events_after_first = {i: _events(app, i) for i in ids}
        second = _run(app, ids)

        assert second == first
        assert {i: _events(app, i) for i in ids} == events_after_first
        assert all(len(_cohort_assigned(app, i)) == 1 for i in ids)
    finally:
        app.close()


# --- 2. inactive learners -------------------------------------------------------------


def test_2_learner_inactive_for_30_days_is_excluded_and_not_assigned(tmp_path):
    app = _app(tmp_path)
    try:
        thirty_days_ago = REFERENCE - timedelta(days=30)
        just_inside = thirty_days_ago + timedelta(minutes=1)
        inactive = _learner(app, [(thirty_days_ago, "math/l0")])
        edge_active = _learner(app, [(just_inside, "math/l0")])
        recent = _learner(app, _week(PRIOR_WEEK_START, 3))
        # The expectations are is_inactive itself, with the build's reference_time.
        assert is_inactive(thirty_days_ago, REFERENCE) is True
        assert is_inactive(just_inside, REFERENCE) is False

        result = _run(app, [inactive, edge_active, recent])

        assert inactive not in result.assignments
        assert tuple(result.inactive) == (inactive,)
        assert _cohort_assigned(app, inactive) == []
        assert EPOCH not in app.repository.get(UUID(inactive)).cohort_memberships
        assert set(result.assignments) == {edge_active, recent}
        assert sum(result.cohort_counts.values()) == 2
    finally:
        app.close()


# --- 3. merging undersized cohorts and the max_size bound ------------------------------


def _three_group_population(app):
    """5 intensive/math, 1 casual/math, 1 casual/physics learners."""
    big = [_learner(app, _week(PRIOR_WEEK_START, 10)) for _ in range(5)]
    small_math = _learner(app, _week(PRIOR_WEEK_START, 1, module="math"))
    small_physics = _learner(app, _week(PRIOR_WEEK_START, 1, module="physics"))
    return big, small_math, small_physics


def test_3_undersized_cohorts_are_merged_into_a_viable_one(tmp_path, monkeypatch):
    _fixed_buckets(monkeypatch)
    app = _app(tmp_path)
    try:
        big, small_math, small_physics = _three_group_population(app)
        everyone = big + [small_math, small_physics]

        result = _run(app, everyone, min_size=3, max_size=10)

        assert sorted(result.cohort_counts.values()) == [7]  # the two singletons joined the five
        assert set(result.assignments) == set(everyone)
        assert len(set(result.assignments.values())) == 1
        # Merged learners are recorded in the cohort they ended up in.
        assert _cohort_assigned(app, small_math)[0].cohort_id == result.assignments[small_math]
    finally:
        app.close()


def test_3_no_cohort_exceeds_max_size_even_when_merging(tmp_path, monkeypatch):
    _fixed_buckets(monkeypatch)
    app = _app(tmp_path)
    try:
        big, small_math, small_physics = _three_group_population(app)
        everyone = big + [small_math, small_physics]

        # Room for exactly one of the two singletons next to the five: the other stays alone.
        result = _run(app, everyone, min_size=3, max_size=6)

        assert max(result.cohort_counts.values()) <= 6
        assert sorted(result.cohort_counts.values()) == [1, 6]
        assert sum(result.cohort_counts.values()) == 7
    finally:
        app.close()


def test_3_oversized_cohort_is_split_evenly_to_respect_max_size(tmp_path, monkeypatch):
    _fixed_buckets(monkeypatch)
    app = _app(tmp_path)
    try:
        ids = [_learner(app, _week(PRIOR_WEEK_START, 1)) for _ in range(7)]  # one big casual/math group

        result = _run(app, ids, min_size=2, max_size=3)

        assert sorted(result.cohort_counts.values()) == [2, 2, 3]
        assert set(result.assignments) == set(ids)
    finally:
        app.close()


def test_3_every_cohort_keeps_the_epoch_and_one_strategy(tmp_path, monkeypatch):
    _fixed_buckets(monkeypatch)
    app = _app(tmp_path)
    try:
        big, small_math, small_physics = _three_group_population(app)

        result = _run(app, big + [small_math, small_physics], min_size=3, max_size=10)

        assert set(result.definitions) == set(result.cohort_counts)
        assert {d.epoch for d in result.definitions.values()} == {EPOCH}
        assert len({d.strategy for d in result.definitions.values()}) == 1
        assert all(d.max_size == 10 and d.min_size == 3 for d in result.definitions.values())
    finally:
        app.close()


# --- 4. sealed or finalized cohorts ----------------------------------------------------


@pytest.mark.parametrize("status", ["sealed", "finalized"])
def test_4_learner_in_a_sealed_or_finalized_cohort_is_not_moved(tmp_path, monkeypatch, status):
    _fixed_buckets(monkeypatch)
    app = _app(tmp_path)
    store = InMemoryLeaderboardStore()
    try:
        fixed = _learner(app, _week(PRIOR_WEEK_START, 1))
        learner = app.repository.get(UUID(fixed))
        learner.assign_cohort("cohort_sealed_x", EPOCH, assigned_at="2026-10-12T07:00:00Z")
        app.save(learner)
        store.save_cohort_definition(CohortDefinition("cohort_sealed_x", epoch=EPOCH, status=status))
        newcomers = [_learner(app, _week(PRIOR_WEEK_START, 1)) for _ in range(3)]
        events_before = _events(app, fixed)

        # min_size=3: the lone member of the sealed cohort looks undersized, a naive merge would move it.
        result = _run(app, [fixed] + newcomers, min_size=3, max_size=10)

        assert result.assignments[fixed] == "cohort_sealed_x"
        assert _events(app, fixed) == events_before
        assert app.repository.get(UUID(fixed)).cohort_memberships[EPOCH]["cohort_id"] == "cohort_sealed_x"
        assert store.get_cohort_definition("cohort_sealed_x").status == status
        assert all(result.assignments[i] != "cohort_sealed_x" for i in newcomers)
    finally:
        store.close()
        app.close()


# --- 5. lesson ids without "/" ---------------------------------------------------------


def test_5_lesson_ids_without_a_slash_count_for_the_tier_and_give_general(tmp_path):
    app = _app(tmp_path)
    try:
        names = ["lesson_3", "l_492", "lesson_3", "l_492"]
        plain = _learner(app, [(PRIOR_WEEK_START + timedelta(hours=i), n) for i, n in enumerate(names)])
        leading_slash = _learner(
            app, [(PRIOR_WEEK_START + timedelta(hours=i), f"/l{i}") for i in range(10)]
        )  # empty prefix: no module
        mixed = _learner(
            app,
            [(PRIOR_WEEK_START, "lesson_3"), (PRIOR_WEEK_START + timedelta(hours=1), "l_492")]
            + [(PRIOR_WEEK_START + timedelta(hours=2), "math/l1")],
        )

        cohorts = _run(app, [plain, leading_slash, mixed])  # must not raise

        _assert_key(cohorts, plain, EPOCH, 4, "general", "regular")
        _assert_key(cohorts, leading_slash, EPOCH, 10, "general", "intensive")
        _assert_key(cohorts, mixed, EPOCH, 3, "math", "regular")
    finally:
        app.close()


# --- 6. replay gives the same cohorts and ranks ----------------------------------------


def test_6_rebuilding_from_the_event_log_gives_same_cohorts_and_ranks(tmp_path):
    events_db = str(tmp_path / "events.db")
    env = {"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": events_db}
    app = LearnerApplication(env=env)
    live_store = SQLiteLeaderboardStore(str(tmp_path / "live.db"))
    try:
        # Prior-week lessons decide the cohort; epoch-week lessons decide the scores.
        shapes = [(2, 1), (2, 3), (4, 2), (11, 2), (11, 5), (0, 4)]
        ids = [
            _learner(app, _week(PRIOR_WEEK_START, prior) + _week(THIS_WEEK_START, now, module="math"))
            for prior, now in shapes
        ]
        result = _run(app, ids, min_size=1)
        live = LeaderboardProjection(live_store)
        for notification in read_all_notifications(app):
            live.apply(notification, offset=notification.id)
    finally:
        app.close()

    # A fresh application on the same log, and a fresh read model rebuilt from it.
    restarted = LearnerApplication(env=env)
    rebuilt_store = SQLiteLeaderboardStore(str(tmp_path / "rebuilt.db"))
    try:
        rebuilt = LeaderboardProjection(rebuilt_store)
        rebuilt.rebuild(read_all_notifications(restarted))

        replayed = {
            i: restarted.repository.get(UUID(i)).cohort_memberships[EPOCH]["cohort_id"] for i in ids
        }
        assert replayed == dict(result.assignments)
        assert _run(restarted, ids, min_size=1) == result  # a fresh instance changes nothing

        for scope_type, scope_id in [("global", "global")] + [
            ("cohort", c) for c in sorted(set(result.assignments.values()))
        ]:
            live_top = live_store.get_top_k(scope_type, scope_id, EPOCH, k=100)
            assert live_top, (scope_type, scope_id)
            assert rebuilt_store.get_top_k(scope_type, scope_id, EPOCH, k=100) == live_top
            for ranked in live_top:
                assert (
                    rebuilt_store.get_rank(scope_type, scope_id, EPOCH, ranked.subject_id) == ranked
                )
    finally:
        live_store.close()
        rebuilt_store.close()
        restarted.close()


# --- 7. store parity (SQLite vs in-memory, head to head) -------------------------------
# Existing tests (test_deterministic_ties, test_atomic_awards, ...) run each spec once per store
# type but never compare the two stores' answers to the same awards; this test does.

AWARDS = [  # (subject, xp, occurred_at, event_id, offset)
    ("L10", 100, "2026-10-12T09:00:00Z", "e1", 1),
    ("L2", 100, "2026-10-12T09:00:00Z", "e2", 2),  # tie on score and time: subject_id decides
    ("l1", 100, "2026-10-12T09:00:00Z", "e3", 3),  # lower-case sorts after upper-case
    ("L3", 250, "2026-10-12T11:00:00Z", "e4", 4),
    ("L4", 250, "2026-10-12T10:00:00Z", "e5", 5),  # same score, earlier time wins
    ("L5", 40, "2026-10-12T08:00:00Z", "e6", 6),
    ("L3", -50, "2026-10-12T12:00:00Z", "e7", 8),  # score drops: rank worsens
    ("L6", 100, "2026-10-12T09:00:00Z", "e8", 7),  # arrives out of order
]
COHORTS = {"L10": "c_a", "L2": "c_a", "l1": "c_a", "L3": "c_b", "L4": "c_b", "L5": "c_b", "L6": "c_b"}


def _populate(store):
    projection = LeaderboardProjection(store)
    for subject, cohort in COHORTS.items():
        projection.assign_cohort(subject, cohort, EPOCH, "2026-10-12T08:00:00Z", watermark_offset=0)
    for subject, xp, occurred_at, event_id, offset in AWARDS:
        projection.record_xp(
            subject, xp, occurred_at, epoch=EPOCH, watermark_offset=offset, event_id=event_id
        )


def test_7_sqlite_and_in_memory_stores_return_identical_ranks_and_top_k(tmp_path):
    sqlite_store = SQLiteLeaderboardStore(str(tmp_path / "parity.db"))
    memory_store = InMemoryLeaderboardStore()
    try:
        _populate(sqlite_store)
        _populate(memory_store)

        scopes = [("global", "global"), ("cohort", "c_a"), ("cohort", "c_b"), ("cohort", "c_missing")]
        for scope_type, scope_id in scopes:
            for k in (1, 3, 100):
                assert sqlite_store.get_top_k(scope_type, scope_id, EPOCH, k=k) == memory_store.get_top_k(
                    scope_type, scope_id, EPOCH, k=k
                ), (scope_type, scope_id, k)
            for subject in [*COHORTS, "nobody"]:
                assert sqlite_store.get_rank(
                    scope_type, scope_id, EPOCH, subject
                ) == memory_store.get_rank(scope_type, scope_id, EPOCH, subject), (
                    scope_type,
                    scope_id,
                    subject,
                )
                assert sqlite_store.get_surrounding(
                    scope_type, scope_id, EPOCH, subject, window=2
                ) == memory_store.get_surrounding(scope_type, scope_id, EPOCH, subject, window=2)
            assert sorted(
                sqlite_store.get_all_entries(scope_type, scope_id, EPOCH), key=lambda e: e.subject_id
            ) == sorted(
                memory_store.get_all_entries(scope_type, scope_id, EPOCH), key=lambda e: e.subject_id
            )
        # The scenario really ranks something (guards against comparing two empty answers).
        assert len(sqlite_store.get_top_k("global", "global", EPOCH, k=100)) == len(COHORTS)
    finally:
        sqlite_store.close()
        memory_store.close()
