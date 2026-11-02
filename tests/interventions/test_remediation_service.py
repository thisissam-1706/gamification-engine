"""M4 step 2: storing remediation decisions on the Learner and the run_remediation runner.

Written before the implementation. Assumed new API (see the Week 6 M4 step 2 audit):
``Learner.record_remediation_decision`` emitting one ``RemediationDecided`` event,
``Learner.remediation_decisions`` (decision_id -> record) and
``src.interventions.service.run_remediation``.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from src.interventions.config import RemediationConfig
from src.interventions.policy import RemediationDecision
from src.state_machine.learner import Learner
from tests.interventions.test_checkpoint_attempt_ingestion import (
    OCCURRED,
    _checkpoint_envelope,
    _ingest_checkpoint,
    _learner,
    _new_learner,
    _open,
)

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def _ingest(application, evidence, learner_id, attempt_id, *, assessment_id="checkpoint_math_v1",
            passed=False, occurred_at=OCCURRED):
    envelope = _checkpoint_envelope(
        learner_id, attempt_id, occurred_at=occurred_at, assessment_id=assessment_id
    )
    envelope["payload"]["passed"] = passed
    result = _ingest_checkpoint(application, evidence, envelope)
    assert result.status == "accepted"


def _run(application, learner_id, attempt_id, now):
    # Imported here so a missing feature fails each test with a clear ImportError.
    from src.interventions.service import run_remediation

    return run_remediation(application, learner_id, attempt_id, clock=lambda: now)


def _remediation_events(application, learner_id) -> list:
    return [
        event
        for event in application.events.get(UUID(learner_id))
        if type(event).__name__ == "RemediationDecided"
    ]


def _decision(attempt_id="att-1", action="assign", decided_at=NOW) -> RemediationDecision:
    config = RemediationConfig()
    assign = action == "assign"
    return RemediationDecision(
        decision_id=f"remediation:{attempt_id}",
        attempt_id=attempt_id,
        assessment_id="checkpoint_math_v1",
        action=action,
        reason=None if assign else "passed",
        decided_at=decided_at,
        deadline_at=decided_at + config.quest_deadline if assign else None,
        quest_id=config.quest_id if assign else None,
        policy_version=config.policy_version,
    )


# --- Learner.record_remediation_decision -------------------------------------


def test_record_stores_decision_with_iso_datetimes_and_emits_one_event():
    learner = Learner()
    decision = _decision()
    learner.collect_events()  # drop the creation event

    assert learner.record_remediation_decision(decision) is True

    assert set(learner.remediation_decisions) == {"remediation:att-1"}
    stored = learner.remediation_decisions["remediation:att-1"]
    assert stored["decision_id"] == "remediation:att-1"
    assert stored["attempt_id"] == "att-1"
    assert stored["assessment_id"] == "checkpoint_math_v1"
    assert stored["action"] == "assign"
    assert stored["reason"] is None
    assert stored["decided_at"] == NOW.isoformat()
    assert stored["deadline_at"] == (NOW + timedelta(days=7)).isoformat()
    assert stored["quest_id"] == "remediation_v1"
    assert stored["policy_version"] == "remediation_policy_v1"
    events = learner.collect_events()
    assert [type(e).__name__ for e in events] == ["RemediationDecided"]


def test_record_no_action_stores_none_fields():
    learner = Learner()
    learner.record_remediation_decision(_decision(action="no_action"))
    stored = learner.remediation_decisions["remediation:att-1"]
    assert stored["action"] == "no_action"
    assert stored["reason"] == "passed"
    assert stored["deadline_at"] is None
    assert stored["quest_id"] is None


def test_record_is_idempotent_on_decision_id():
    learner = Learner()
    learner.record_remediation_decision(_decision())
    learner.collect_events()

    later = _decision(action="no_action", decided_at=NOW + timedelta(days=30))
    assert learner.record_remediation_decision(later) is False

    assert learner.collect_events() == []
    assert learner.remediation_decisions["remediation:att-1"]["action"] == "assign"
    assert learner.remediation_decisions["remediation:att-1"]["decided_at"] == NOW.isoformat()


def test_record_leaves_xp_streak_quests_and_attempts_unchanged():
    learner = Learner()
    before = (
        learner.xp_total,
        learner.new_streak,
        set(learner.assigned_quest_ids),
        set(learner.completed_quest_ids),
        dict(learner.quest_progress),
        dict(learner.quest_instances),
        dict(learner.checkpoint_attempts),
        dict(learner.first_checkpoint_attempt_ids),
    )

    learner.record_remediation_decision(_decision())

    after = (
        learner.xp_total,
        learner.new_streak,
        set(learner.assigned_quest_ids),
        set(learner.completed_quest_ids),
        dict(learner.quest_progress),
        dict(learner.quest_instances),
        dict(learner.checkpoint_attempts),
        dict(learner.first_checkpoint_attempt_ids),
    )
    assert after == before


# --- run_remediation ----------------------------------------------------------


def test_failed_first_attempt_is_assigned_and_stored_once(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        _ingest(application, evidence, learner_id, "att-1")

        decision = _run(application, learner_id, "att-1", NOW)

        assert decision.action == "assign"
        assert decision.decision_id == "remediation:att-1"
        assert decision.decided_at == NOW
        assert decision.deadline_at == NOW + timedelta(days=7)
        assert len(_remediation_events(application, learner_id)) == 1
        stored = _learner(application, learner_id).remediation_decisions["remediation:att-1"]
        assert stored["action"] == "assign"
        assert stored["decided_at"] == NOW.isoformat()
    finally:
        evidence.close()
        application.close()


def test_rerun_returns_stored_decision_even_when_clock_has_moved_on(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        _ingest(application, evidence, learner_id, "att-1")
        first = _run(application, learner_id, "att-1", NOW)

        second = _run(application, learner_id, "att-1", NOW + timedelta(days=30))

        assert second == first
        assert second.decided_at == NOW
        assert len(_remediation_events(application, learner_id)) == 1
    finally:
        evidence.close()
        application.close()


def test_second_failed_first_attempt_within_window_is_no_action(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        _ingest(application, evidence, learner_id, "att-1", assessment_id="math")
        _ingest(application, evidence, learner_id, "att-2", assessment_id="physics")

        first = _run(application, learner_id, "att-1", NOW)
        second = _run(application, learner_id, "att-2", NOW + timedelta(days=3))

        assert first.action == "assign"
        assert (second.action, second.reason) == ("no_action", "remediated_within_window")
        assert len(_remediation_events(application, learner_id)) == 2
        stored = _learner(application, learner_id).remediation_decisions
        assert set(stored) == {"remediation:att-1", "remediation:att-2"}
    finally:
        evidence.close()
        application.close()


def test_passed_attempt_stores_an_explicit_no_action(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        _ingest(application, evidence, learner_id, "att-1", passed=True)

        decision = _run(application, learner_id, "att-1", NOW)

        assert (decision.action, decision.reason) == ("no_action", "passed")
        assert len(_remediation_events(application, learner_id)) == 1
        stored = _learner(application, learner_id).remediation_decisions["remediation:att-1"]
        assert (stored["action"], stored["reason"]) == ("no_action", "passed")
    finally:
        evidence.close()
        application.close()


def test_run_remediation_leaves_xp_streak_and_attempts_unchanged(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        _ingest(application, evidence, learner_id, "att-1")

        def snapshot():
            learner = _learner(application, learner_id)
            return (
                learner.xp_total,
                learner.new_streak,
                dict(learner.checkpoint_attempts),
                dict(learner.first_checkpoint_attempt_ids),
            )

        before = snapshot()
        _run(application, learner_id, "att-1", NOW)
        assert snapshot() == before
        # Quest assignment is the one intended addition (Step 3).
        learner = _learner(application, learner_id)
        assert set(learner.quest_instances) == {"remediation_quest:att-1"}
        assert learner.completed_quest_ids == set()
    finally:
        evidence.close()
        application.close()


# --- edge cases ---------------------------------------------------------------


def _event_count(application, learner_id) -> int:
    return len(list(application.events.get(UUID(learner_id))))


def test_replay_in_a_fresh_application_keeps_decisions_and_returns_stored(tmp_path):
    db = str(tmp_path / "events.db")
    application, evidence = _open(db)
    try:
        learner_id = _new_learner(application)
        _ingest(application, evidence, learner_id, "att-1", assessment_id="math")
        _ingest(application, evidence, learner_id, "att-2", assessment_id="physics")
        _ingest(application, evidence, learner_id, "att-3", assessment_id="chem", passed=True)
        decisions = [
            _run(application, learner_id, "att-1", NOW),
            _run(application, learner_id, "att-2", NOW + timedelta(days=1)),
            _run(application, learner_id, "att-3", NOW + timedelta(days=2)),
        ]
        expected = dict(_learner(application, learner_id).remediation_decisions)
        events_before = _event_count(application, learner_id)
    finally:
        evidence.close()
        application.close()

    fresh, fresh_evidence = _open(db)
    try:
        assert _learner(fresh, learner_id).remediation_decisions == expected
        again = [_run(fresh, learner_id, a, NOW + timedelta(days=60)) for a in ("att-1", "att-2", "att-3")]
        assert again == decisions
        assert _event_count(fresh, learner_id) == events_before
    finally:
        fresh_evidence.close()
        fresh.close()


def test_late_earlier_attempt_keeps_stored_decision_and_is_blocked_by_it(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        _ingest(application, evidence, learner_id, "att-A")
        a = _run(application, learner_id, "att-A", NOW)
        assert a.action == "assign"

        _ingest(application, evidence, learner_id, "att-B", occurred_at=OCCURRED - timedelta(hours=1))
        learner = _learner(application, learner_id)
        assert learner.first_checkpoint_attempt_ids["checkpoint_math_v1"] == "att-B"

        b = _run(application, learner_id, "att-B", NOW + timedelta(hours=1))

        assert (b.action, b.reason) == ("no_action", "remediated_within_window")
        stored = _learner(application, learner_id).remediation_decisions
        assert stored["remediation:att-A"]["action"] == "assign"
        assert stored["remediation:att-A"]["decided_at"] == NOW.isoformat()
        assert _run(application, learner_id, "att-A", NOW + timedelta(days=1)) == a
        assert len(_remediation_events(application, learner_id)) == 2
    finally:
        evidence.close()
        application.close()


def test_unknown_attempt_raises_key_error_and_saves_nothing(tmp_path):
    import pytest

    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        _ingest(application, evidence, learner_id, "att-1")
        before = _event_count(application, learner_id)

        with pytest.raises(KeyError):
            _run(application, learner_id, "missing", NOW)

        assert _event_count(application, learner_id) == before
        assert _learner(application, learner_id).remediation_decisions == {}
    finally:
        evidence.close()
        application.close()


def test_naive_clock_raises_value_error_and_saves_nothing(tmp_path):
    import pytest

    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        _ingest(application, evidence, learner_id, "att-1")
        before = _event_count(application, learner_id)

        with pytest.raises(ValueError):
            _run(application, learner_id, "att-1", datetime(2026, 9, 10, 12, 0))

        assert _event_count(application, learner_id) == before
        assert _learner(application, learner_id).remediation_decisions == {}
    finally:
        evidence.close()
        application.close()


def test_window_is_measured_from_decided_at_not_attempt_occurred_at(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        learner_id = _new_learner(application)
        _ingest(application, evidence, learner_id, "att-old", assessment_id="math",
                occurred_at=NOW - timedelta(days=20))
        _ingest(application, evidence, learner_id, "att-new", assessment_id="physics",
                occurred_at=NOW - timedelta(hours=1))

        old = _run(application, learner_id, "att-old", NOW - timedelta(days=3))
        new = _run(application, learner_id, "att-new", NOW)

        assert old.action == "assign"
        assert (new.action, new.reason) == ("no_action", "remediated_within_window")
    finally:
        evidence.close()
        application.close()


def test_learners_are_independent(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    try:
        first_id = _new_learner(application)
        second_id = _new_learner(application)
        _ingest(application, evidence, first_id, "att-1")
        _ingest(application, evidence, second_id, "att-1")

        one = _run(application, first_id, "att-1", NOW)
        two = _run(application, second_id, "att-1", NOW + timedelta(days=1))

        assert one.action == "assign"
        assert two.action == "assign"
        assert len(_remediation_events(application, first_id)) == 1
        assert len(_remediation_events(application, second_id)) == 1
    finally:
        evidence.close()
        application.close()
