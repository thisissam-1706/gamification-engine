"""AT-13 step 3: exposure edge cases (order, ties, replay, provenance, refusals, isolation)."""

from datetime import datetime, timedelta, timezone
import uuid

import pytest

from src.experiments import exposure
from src.experiments.assignment import assign
from src.experiments.exposure import ExposureRefused
from src.state_machine.learner import Learner, LearnerApplication

EXP = "exp_007"
SURFACE = "streak_risk_reminder"
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)


def _uuid_for_variant(variant: str, nth: int = 0, exp_id: str = EXP) -> uuid.UUID:
    seen = 0
    for i in range(10_000):
        candidate = uuid.uuid5(uuid.NAMESPACE_URL, f"learner-{i}")
        if assign(str(candidate), exp_id)[0] == variant:
            if seen == nth:
                return candidate
            seen += 1
    raise AssertionError("no uuid found")


def _env(db) -> dict[str, str]:
    return {"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": str(db)}


@pytest.fixture
def app(tmp_path):
    application = LearnerApplication(env=_env(tmp_path / "events.db"))
    yield application
    application.close()


def _assigned(nth: int = 0) -> Learner:
    learner = Learner._create(Learner.Created, id=_uuid_for_variant("variant_a", nth))
    assert learner.assign_experiment(EXP, assigned_at=T0)
    return learner


def _expose(learner, view_id, offset_hours, **kwargs):
    return learner.record_experiment_exposure(
        EXP, SURFACE, view_id=view_id, exposed_at=T0 + offset_hours * HOUR, **kwargs
    )


def _live_state(learner: Learner) -> dict:
    return {
        "assignments": {k: dict(v) for k, v in learner.experiment_assignments.items()},
        "exposures": {k: dict(v) for k, v in learner.experiment_exposures.items()},
        "first": dict(learner.first_exposure_view_ids),
        "sources": dict(learner.exposure_sources),
    }


def test_first_exposure_is_independent_of_arrival_order():
    early_last, early_first = _assigned(0), _assigned(1)
    _expose(early_last, "VLATE", 2)
    _expose(early_last, "VEARLY", 1)
    _expose(early_first, "VEARLY", 1)
    _expose(early_first, "VLATE", 2)
    for learner in (early_last, early_first):
        first = learner.first_experiment_exposure(EXP)
        assert first["view_id"] == "VEARLY"
        assert first["exposed_at"] == (T0 + HOUR).isoformat()
    assert early_last.first_experiment_exposure(EXP) == early_first.first_experiment_exposure(EXP)


@pytest.mark.parametrize("order", [("VA", "VB"), ("VB", "VA")])
def test_tie_on_exposed_at_picks_smaller_view_id(order):
    learner = _assigned()
    for view_id in order:
        _expose(learner, view_id, 1)
    assert learner.first_exposure_view_ids[EXP] == "VA"


def test_replay_on_fresh_application_equals_live_state(tmp_path):
    db = tmp_path / "events.db"
    first_app = LearnerApplication(env=_env(db))
    try:
        learner = _assigned()
        _expose(learner, "V2", 2, source_event_id="evt-2")
        _expose(learner, "V1", 1, source_event_id="evt-1")
        first_app.save(learner)
        live = _live_state(learner)
        learner_id = learner.id
    finally:
        first_app.close()

    second_app = LearnerApplication(env=_env(db))
    try:
        replayed = _live_state(second_app.repository.get(learner_id))
    finally:
        second_app.close()

    assert replayed == live
    assert replayed["first"] == {EXP: "V1"}
    assert set(replayed["exposures"]) == {"V1", "V2"}
    assert replayed["assignments"][EXP]["variant"] == "variant_a"
    # 4: exposure_sources is filled and survives reload.
    assert replayed["sources"] == {"V1": "evt-1", "V2": "evt-2"}


def test_exposure_without_source_event_id_has_no_source_entry():
    learner = _assigned()
    _expose(learner, "V1", 1)
    assert learner.exposure_sources == {}


def test_late_assignment_does_not_accept_an_earlier_refused_exposure(app):
    learner = Learner._create(Learner.Created, id=_uuid_for_variant("variant_a"))
    app.save(learner)
    learner = app.repository.get(learner.id)

    with pytest.raises(ExposureRefused) as info:
        _expose(learner, "V1", 1)
    assert info.value.reason == "no_assignment"
    assert learner.experiment_exposures == {}
    assert learner.first_exposure_view_ids == {}
    assert learner.pending_events == []

    assert learner.assign_experiment(EXP, assigned_at=T0)
    app.save(learner)
    reloaded = app.repository.get(learner.id)
    assert reloaded.experiment_exposures == {}
    assert reloaded.first_experiment_exposure(EXP) is None
    names = [type(e).__name__ for e in app.events.get(learner.id)]
    assert "ExperimentExposed" not in names

    # The same view_id is still free: the refusal left no trace, so a retry is accepted.
    assert _expose(reloaded, "V1", 1) is True


def test_two_experiments_do_not_interfere(monkeypatch):
    other_exp = "exp_test_other"
    learner_id = _uuid_for_variant("variant_a")
    other_variant, _ = assign(str(learner_id), other_exp)
    monkeypatch.setitem(
        exposure.EXPOSURE_SURFACES, other_exp, {other_variant: frozenset({"other_surface"})}
    )

    learner = Learner._create(Learner.Created, id=learner_id)
    learner.assign_experiment(EXP, assigned_at=T0)
    learner.assign_experiment(other_exp, assigned_at=T0 + 3 * HOUR)

    _expose(learner, "V-main", 5)
    learner.record_experiment_exposure(
        other_exp, "other_surface", view_id="V-other", exposed_at=T0 + 4 * HOUR
    )

    assert learner.first_exposure_view_ids == {EXP: "V-main", other_exp: "V-other"}
    assert learner.first_experiment_exposure(EXP)["variant"] == "variant_a"
    assert learner.first_experiment_exposure(other_exp)["variant"] == other_variant
    assert learner.first_experiment_exposure(other_exp)["assignment_ref"] == (
        f"experiment_assignment:{other_exp}"
    )
    # Surfaces and assignment times are per experiment.
    with pytest.raises(ExposureRefused) as info:
        learner.record_experiment_exposure(
            other_exp, SURFACE, view_id="V-x", exposed_at=T0 + 4 * HOUR
        )
    assert info.value.reason == "not_treatment_surface"
    with pytest.raises(ExposureRefused) as info:
        learner.record_experiment_exposure(
            other_exp, "other_surface", view_id="V-y", exposed_at=T0 + HOUR
        )
    assert info.value.reason == "exposure_before_assignment"
