"""AT-13 step 1: experiment assignment is a durable event on the Learner aggregate."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

import pytest

from src.experiments.assignment import HASH_VERSION, assign
from src.experiments.service import assign_learner_to_experiment
from src.state_machine.learner import Learner, LearnerApplication

EXP_ID = "exp_007"
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _uuid_for_variant(variant: str) -> uuid.UUID:
    for i in range(10_000):
        candidate = uuid.uuid5(uuid.NAMESPACE_URL, f"learner-{i}")
        if assign(str(candidate), EXP_ID)[0] == variant:
            return candidate
    raise AssertionError(f"no uuid found for {variant}")


def _env(db: Path) -> dict[str, str]:
    return {"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": str(db)}


def _learner_with_id(learner_id: uuid.UUID) -> Learner:
    # Learner.__init__ takes no id; _create is the eventsourcing hook for a chosen id.
    return Learner._create(Learner.Created, id=learner_id)


def _names(events) -> list[str]:
    return [type(e).__name__ for e in events]


@pytest.fixture
def app(tmp_path):
    application = LearnerApplication(env=_env(tmp_path / "events.db"))
    yield application
    application.close()


def _stored_learner(app, variant: str) -> Learner:
    learner = _learner_with_id(_uuid_for_variant(variant))
    app.save(learner)
    return learner


def test_assignment_stores_one_assigned_and_zero_exposed_event(app):
    learner = _stored_learner(app, "variant_a")
    assert assign(str(learner.id), EXP_ID)[0] == "variant_a"

    assert assign_learner_to_experiment(app, str(learner.id), EXP_ID, T0) is True

    names = _names(app.events.get(learner.id))
    assert names.count("ExperimentAssigned") == 1
    assert names.count("ExperimentExposed") == 0


@pytest.mark.parametrize("variant", ["variant_a", "variant_b"])
def test_stored_fields_equal_assign_result(app, variant):
    learner = _stored_learner(app, variant)
    expected_variant, expected_propensity = assign(str(learner.id), EXP_ID)
    assert expected_variant == variant

    assign_learner_to_experiment(app, str(learner.id), EXP_ID, T0)

    reloaded = app.repository.get(learner.id)
    record = reloaded.experiment_assignments[EXP_ID]
    assert record["variant"] == expected_variant
    assert record["propensity"] == expected_propensity
    assert record["hash_version"] == HASH_VERSION == "hash_v2"
    assert record["exp_id"] == EXP_ID
    assert record["assigned_at"] == T0.isoformat()


def test_second_call_same_exp_is_noop_even_with_different_assigned_at(app):
    learner = _stored_learner(app, "variant_a")
    assert assign_learner_to_experiment(app, str(learner.id), EXP_ID, T0) is True
    before = dict(app.repository.get(learner.id).experiment_assignments)
    count_before = len(list(app.events.get(learner.id)))

    later = T0 + timedelta(days=3)
    assert assign_learner_to_experiment(app, str(learner.id), EXP_ID, later) is False

    assert len(list(app.events.get(learner.id))) == count_before
    assert app.repository.get(learner.id).experiment_assignments == before

    # Also at the aggregate level: no pending event, state unchanged.
    reloaded = app.repository.get(learner.id)
    assert reloaded.assign_experiment(EXP_ID, assigned_at=later) is False
    assert reloaded.pending_events == [] or not any(
        type(e).__name__ == "ExperimentAssigned" for e in reloaded.pending_events
    )
    assert reloaded.experiment_assignments == before


def test_naive_assigned_at_raises_and_emits_nothing(app):
    learner = _stored_learner(app, "variant_a")
    count_before = len(list(app.events.get(learner.id)))
    naive = datetime(2026, 10, 1, 12, 0)

    with pytest.raises(ValueError):
        assign_learner_to_experiment(app, str(learner.id), EXP_ID, naive)

    assert len(list(app.events.get(learner.id))) == count_before
    assert app.repository.get(learner.id).experiment_assignments == {}

    fresh = _learner_with_id(_uuid_for_variant("variant_b"))
    with pytest.raises(ValueError):
        fresh.assign_experiment(EXP_ID, assigned_at=naive)
    assert _names(fresh.pending_events) == ["Created"]
    assert fresh.experiment_assignments == {}


def test_fresh_application_on_same_sqlite_file_sees_same_assignments(tmp_path):
    db = tmp_path / "events.db"
    first = LearnerApplication(env=_env(db))
    try:
        learner = _learner_with_id(_uuid_for_variant("variant_b"))
        first.save(learner)
        assert assign_learner_to_experiment(first, str(learner.id), EXP_ID, T0) is True
        saved = dict(first.repository.get(learner.id).experiment_assignments)
    finally:
        first.close()

    second = LearnerApplication(env=_env(db))
    try:
        reloaded = second.repository.get(learner.id)
        assert reloaded.experiment_assignments == saved
        assert saved[EXP_ID]["variant"] == "variant_b"
    finally:
        second.close()
