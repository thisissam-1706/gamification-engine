"""run_tick: one pass over the engine's caller-run jobs, written before scripts/run_engine_tick.py exists.

Every test fails with ImportError until the script exists. The interface these tests pin (see
docs/audit/driver-step1-tests.md):

    run_tick(*, application, projection, dispatcher, evidence, signal_store, prefs, now,
             cohort_min_size=500, cohort_max_size=5000) -> dict

``result["steps"]`` has one dict per step (weekly_cycle, poll, quest_expiry, remediation, streak_risk,
abuse, reconcile); a step that raised has ``"error"`` set and the other steps still run.
``result["summary"]`` has new_decisions, new_signals, new_notifications (work done by THIS call).
The step functions are looked up as module-level names of ``scripts.run_engine_tick`` so tests can patch them.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.abuse.store import AbuseSignalStore
from src.experiments.store import ExperimentStore
from src.ingestion.evidence import EvidenceStore
from src.leaderboard.projection import LeaderboardProjection
from src.leaderboard.store import SQLiteLeaderboardStore
from src.notifications.dispatcher import NotificationDispatcher
from src.notifications.models import ChannelType, LearnerPreferences
from src.notifications.store import NotificationStore
from src.state_machine.learner import LearnerApplication
from tests.experiments.support import (
    EXP_ID,
    stored_event_names,
    RecordingPush,
    make_learner,
    record_attempt,
)

STEPS = ("weekly_cycle", "assign", "poll", "quest_expiry", "remediation", "streak_risk", "abuse", "reconcile")
CREATED = datetime(2026, 9, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 12, 15, 40, tzinfo=timezone.utc)
ATTEMPT_AT = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)
QUEST_ASSIGNED = datetime(2026, 9, 20, tzinfo=timezone.utc)


def _run_tick(world, **overrides):
    # Imported here so a missing script fails each test with ImportError, not at collection.
    from scripts.run_engine_tick import run_tick

    kwargs = dict(
        application=world.application,
        projection=world.projection,
        dispatcher=world.dispatcher,
        evidence=world.evidence,
        signal_store=world.signal_store,
        prefs_by_learner=world.prefs,
        experiment_store=world.experiment_store,
        now=NOW,
        cohort_min_size=2,
        cohort_max_size=10,
    )
    kwargs.update(overrides)
    return run_tick(**kwargs)


@pytest.fixture
def world(tmp_path):
    """Four learners (one lesson each, created long ago) on tmp sqlite files, all stores open."""
    events_db = str(tmp_path / "events.db")
    application = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": events_db}
    )
    evidence = EvidenceStore(events_db)
    notification_store = NotificationStore(str(tmp_path / "notif.db"))
    push = RecordingPush()
    dispatcher = NotificationDispatcher(store=notification_store, adapters={ChannelType.PUSH: push})
    board = SQLiteLeaderboardStore(str(tmp_path / "board.db"))
    projection = LeaderboardProjection(board)
    signal_store = AbuseSignalStore(str(tmp_path / "abuse.db"))
    experiment_store = ExperimentStore(events_db)

    ids = [
        make_learner(application, "variant_a" if n % 2 == 0 else "variant_b", nth=n // 2,
                     created=CREATED, assigned_at=None)
        for n in range(4)
    ]
    for n, learner_id in enumerate(ids):  # one lesson yesterday each, so the weekly builder has activity
        learner = application.repository.get(UUID(learner_id))
        learner.complete_lesson(f"c-{n}", f"lesson-{n}", activity_date="2026-10-11")
        application.save(learner)
    prefs = {i: LearnerPreferences(i, timezone="Asia/Kolkata") for i in ids}

    yield SimpleNamespace(
        application=application, evidence=evidence, notification_store=notification_store,
        push=push, dispatcher=dispatcher, board=board, projection=projection,
        signal_store=signal_store, experiment_store=experiment_store, ids=ids, prefs=prefs,
    )
    experiment_store.close()
    signal_store.close()
    board.close()
    notification_store.close()
    evidence.close()
    application.close()


def _learner(world, learner_id):
    return world.application.repository.get(UUID(learner_id))


def _decision_count(world):
    return sum(len(_learner(world, i).remediation_decisions) for i in world.ids)


def _remediation_notices(world):
    return [d for d in world.notification_store.list_dispatches() if d["category"] == "remediation"]


def test_tick_returns_one_entry_per_step(world):
    result = _run_tick(world)

    assert set(result["steps"]) == set(STEPS)
    for name in STEPS:
        assert isinstance(result["steps"][name], dict), name


def test_tick_is_idempotent(world):
    record_attempt(world.application, world.ids[0], "att-1", passed=False, occurred_at=ATTEMPT_AT)

    first = _run_tick(world)
    assert first["summary"]["new_decisions"] == 1  # sanity: the first run did work

    second = _run_tick(world)

    assert second["summary"]["new_decisions"] == 0
    assert second["summary"]["new_signals"] == 0
    assert second["summary"]["new_notifications"] == 0
    assert _decision_count(world) == 1


def test_failed_attempt_gets_decision_and_notice(world):
    learner_id = world.ids[0]
    record_attempt(world.application, learner_id, "att-1", passed=False, occurred_at=ATTEMPT_AT)
    assert _decision_count(world) == 0
    assert _remediation_notices(world) == []

    _run_tick(world)

    decisions = _learner(world, learner_id).remediation_decisions
    assert list(decisions) == ["remediation:att-1"]
    assert decisions["remediation:att-1"]["action"] == "assign"
    notices = _remediation_notices(world)
    assert len(notices) == 1
    assert notices[0]["learner_id"] == learner_id
    assert _decision_count(world) == 1  # nobody else got a decision


def test_overdue_quest_expires(world):
    learner_id = world.ids[1]
    learner = _learner(world, learner_id)
    assert learner.assign_quest(
        "remediation_v1",
        assignment_id="remediation_quest:att-old",
        assigned_at=QUEST_ASSIGNED,
        deadline_at=NOW - timedelta(days=1),
        definition_version="remediation_quests_v1",
    )
    world.application.save(learner)
    assert _learner(world, learner_id).quest_instances["remediation_quest:att-old"]["status"] == "assigned"

    _run_tick(world)

    assert _learner(world, learner_id).quest_instances["remediation_quest:att-old"]["status"] == "expired"


def test_step_failure_is_isolated(world, monkeypatch):
    import scripts.run_engine_tick as tick_module

    record_attempt(world.application, world.ids[0], "att-1", passed=False, occurred_at=ATTEMPT_AT)

    def boom(*args, **kwargs):
        raise RuntimeError("streak sweep exploded")

    monkeypatch.setattr(tick_module, "run_streak_risk_sweep", boom)

    result = _run_tick(world)

    assert "streak sweep exploded" in result["steps"]["streak_risk"]["error"]
    for name in STEPS:
        if name != "streak_risk":
            assert not result["steps"][name].get("error"), (name, result["steps"][name])
    # a step after the failed one still did its work
    assert _decision_count(world) == 1


def test_now_must_be_timezone_aware(world, monkeypatch):
    import scripts.run_engine_tick as tick_module

    def must_not_run(*args, **kwargs):
        raise AssertionError("a step ran although `now` was naive")

    for name in (
        "run_weekly_cycle", "run_until_idle", "run_quest_expiry",
        "run_remediation_with_notification", "run_streak_risk_sweep",
    ):
        monkeypatch.setattr(tick_module, name, must_not_run)
    record_attempt(world.application, world.ids[0], "att-1", passed=False, occurred_at=ATTEMPT_AT)

    with pytest.raises(ValueError):
        _run_tick(world, now=datetime(2026, 10, 12, 15, 40))  # naive

    assert _decision_count(world) == 0


def _board_rows(world):
    return world.board.connection.execute(
        "SELECT scope_type, scope_id, epoch, subject_id, score, first_reached_at, last_updated_at, "
        "watermark_offset FROM leaderboard_entries ORDER BY 1, 2, 3, 4"
    ).fetchall()


def test_second_tick_leaves_leaderboard_rows_unchanged(world):
    _run_tick(world)
    before = _board_rows(world)
    assert before  # sanity: the first tick put rows on the board

    _run_tick(world)

    assert _board_rows(world) == before


def test_epoch_is_the_iso_week_in_utc():
    from scripts.run_engine_tick import _iso_week

    utc_instant = datetime(2026, 10, 18, 23, 30, tzinfo=timezone.utc)  # Sunday, ISO week 42
    ist_instant = datetime.fromisoformat("2026-10-19T04:00:00+05:30")  # Monday locally, still Sunday 22:30 in UTC

    assert _iso_week(utc_instant) == "2026-W42"
    assert _iso_week(ist_instant) == _iso_week(utc_instant)


def _assignment_events(world):
    return sum(stored_event_names(world.application, i).count("ExperimentAssigned") for i in world.ids)


def test_unassigned_learner_is_assigned_and_gets_an_eligibility_row(world):
    learner_id = world.ids[0]
    assert EXP_ID not in _learner(world, learner_id).experiment_assignments
    assert world.experiment_store.get_eligibility(EXP_ID, learner_id) is None

    result = _run_tick(world)

    assignment = _learner(world, learner_id).experiment_assignments[EXP_ID]
    assert assignment["variant"] in ("variant_a", "variant_b")
    assert world.experiment_store.get_eligibility(EXP_ID, learner_id) is not None
    assert result["steps"]["assign"] == {"assigned": 4, "already_assigned": 0, "ineligible": 0}


def test_already_assigned_learner_keeps_variant_and_assigned_at(world):
    from src.experiments.service import assign_learner_to_experiment

    learner_id = world.ids[1]
    earlier = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    assert assign_learner_to_experiment(world.application, learner_id, EXP_ID, earlier)
    before = dict(_learner(world, learner_id).experiment_assignments[EXP_ID])

    result = _run_tick(world)

    assert _learner(world, learner_id).experiment_assignments[EXP_ID] == before
    assert stored_event_names(world.application, learner_id).count("ExperimentAssigned") == 1
    assert result["steps"]["assign"]["already_assigned"] == 1
    assert result["steps"]["assign"]["assigned"] == 3


def test_second_tick_at_the_same_instant_adds_no_assignment_rows(world):
    _run_tick(world)
    events_before = _assignment_events(world)
    rows_before = world.experiment_store.list_eligibility(EXP_ID)
    assert events_before == 4 and len(rows_before) == 4

    second = _run_tick(world)

    assert _assignment_events(world) == events_before
    assert world.experiment_store.list_eligibility(EXP_ID) == rows_before
    assert second["steps"]["assign"]["assigned"] == 0
    assert second["steps"]["assign"]["already_assigned"] == 4
