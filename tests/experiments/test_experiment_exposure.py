"""AT-13 step 2: exposure is a separate durable event, gated by assignment and surface."""

from datetime import datetime, timedelta, timezone
import uuid

import pytest

from src.experiments.assignment import assign
from src.experiments.exposure import EXPOSURE_SURFACES, ExposureRefused
from src.state_machine.learner import Learner, LearnerApplication

EXP = "exp_007"
SURFACE = "streak_risk_reminder"
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)


def _uuid_for_variant(variant: str, nth: int = 0) -> uuid.UUID:
    seen = 0
    for i in range(10_000):
        candidate = uuid.uuid5(uuid.NAMESPACE_URL, f"learner-{i}")
        if assign(str(candidate), EXP)[0] == variant:
            if seen == nth:
                return candidate
            seen += 1
    raise AssertionError(f"no uuid found for {variant}")


@pytest.fixture
def app(tmp_path):
    application = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite",
             "SQLITE_DBNAME": str(tmp_path / "events.db")}
    )
    yield application
    application.close()


def _saved(app, variant: str, *, assigned: bool = True, nth: int = 0) -> Learner:
    learner = Learner._create(Learner.Created, id=_uuid_for_variant(variant, nth))
    if assigned:
        assert learner.assign_experiment(EXP, assigned_at=T0)
    app.save(learner)
    return app.repository.get(learner.id)  # reloaded: no pending events


def _names(app, learner) -> list[str]:
    return [type(e).__name__ for e in app.events.get(learner.id)]


def _state(learner: Learner) -> dict:
    return {
        "exposures": {k: dict(v) for k, v in learner.experiment_exposures.items()},
        "first": dict(learner.first_exposure_view_ids),
        "sources": dict(learner.exposure_sources),
        "assignments": {k: dict(v) for k, v in learner.experiment_assignments.items()},
    }


def test_surface_table():
    assert EXPOSURE_SURFACES == {EXP: {"variant_a": frozenset({SURFACE})}}


def test_assignment_without_exposure_call_has_no_exposure(app):
    learner = _saved(app, "variant_a")
    assert _names(app, learner).count("ExperimentExposed") == 0
    assert learner.first_experiment_exposure(EXP) is None


def test_exposure_after_assignment_stores_one_event(app):
    learner = _saved(app, "variant_a")
    assert learner.record_experiment_exposure(
        EXP, SURFACE, view_id="V1", exposed_at=T0 + HOUR
    ) is True
    app.save(learner)

    reloaded = app.repository.get(learner.id)
    assert _names(app, learner).count("ExperimentExposed") == 1
    record = reloaded.experiment_exposures["V1"]
    assert record["exp_id"] == EXP
    assert record["variant"] == "variant_a"
    assert record["exposure_surface"] == SURFACE
    assert record["assignment_ref"] == "experiment_assignment:exp_007"
    assert record["exposed_at"] == (T0 + HOUR).isoformat()
    assert reloaded.first_experiment_exposure(EXP) == record


def _refused(app, learner, reason, *, surface=SURFACE, exposed_at=T0 + HOUR):
    before = _state(learner)
    count = len(list(app.events.get(learner.id)))
    with pytest.raises(ExposureRefused) as info:
        learner.record_experiment_exposure(
            EXP, surface, view_id="V1", exposed_at=exposed_at
        )
    assert info.value.reason == reason
    assert str(info.value) == reason
    assert isinstance(info.value, ValueError)
    # Refusals leave state unchanged and nothing pending or stored.
    assert _state(learner) == before
    assert learner.pending_events == []
    assert app.save(learner) == []
    assert len(list(app.events.get(learner.id))) == count


def test_no_assignment_is_refused(app):
    _refused(app, _saved(app, "variant_a", assigned=False), "no_assignment")


def test_variant_b_has_no_treatment_surface(app):
    _refused(app, _saved(app, "variant_b"), "no_treatment_surface_for_variant")


def test_non_treatment_surface_is_refused(app):
    _refused(app, _saved(app, "variant_a"), "not_treatment_surface", surface="home_banner")


def test_exposure_before_assignment_refused_equal_allowed(app):
    learner = _saved(app, "variant_a")
    _refused(app, learner, "exposure_before_assignment", exposed_at=T0 - timedelta(seconds=1))
    assert learner.record_experiment_exposure(
        EXP, SURFACE, view_id="V1", exposed_at=T0
    ) is True


def test_naive_exposed_at_raises_value_error(app):
    learner = _saved(app, "variant_a")
    with pytest.raises(ValueError):
        learner.record_experiment_exposure(
            EXP, SURFACE, view_id="V1", exposed_at=datetime(2026, 10, 1, 13, 0)
        )
    assert learner.pending_events == []
    assert learner.experiment_exposures == {}


def test_same_view_id_twice_returns_false_without_event(app):
    learner = _saved(app, "variant_a")
    assert learner.record_experiment_exposure(EXP, SURFACE, view_id="V1", exposed_at=T0 + HOUR)
    after_first = _state(learner)
    pending = len(learner.pending_events)
    assert learner.record_experiment_exposure(
        EXP, SURFACE, view_id="V1", exposed_at=T0 + 2 * HOUR
    ) is False
    assert len(learner.pending_events) == pending
    assert _state(learner) == after_first


def test_first_exposure_is_earliest_regardless_of_arrival_order(app):
    learner = _saved(app, "variant_a")
    for view_id, offset in (("V2", 3), ("V1", 2), ("V3", 5)):
        learner.record_experiment_exposure(
            EXP, SURFACE, view_id=view_id, exposed_at=T0 + offset * HOUR
        )
    assert learner.first_exposure_view_ids[EXP] == "V1"
    assert learner.first_experiment_exposure(EXP)["view_id"] == "V1"
    # Equal timestamps tie-break on view_id.
    other = _saved(app, "variant_a", nth=1)
    for view_id in ("VB", "VA"):
        other.record_experiment_exposure(EXP, SURFACE, view_id=view_id, exposed_at=T0 + HOUR)
    assert other.first_exposure_view_ids[EXP] == "VA"


def test_source_event_id_is_recorded(app):
    learner = _saved(app, "variant_a")
    learner.record_experiment_exposure(
        EXP, SURFACE, view_id="V1", exposed_at=T0 + HOUR, source_event_id="evt-1"
    )
    assert learner.exposure_sources == {"V1": "evt-1"}
