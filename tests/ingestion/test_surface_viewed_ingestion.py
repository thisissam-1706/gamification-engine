"""AT-13 step 5: ingestion of learning.surface_viewed, v1 -> Learner.record_experiment_exposure."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

import pytest
from eventsourcing.application import AggregateNotFoundError

from src.experiments.assignment import assign
from src.ingestion.config import IngestConfig
from src.ingestion.evidence import EvidenceStore
from src.ingestion.validation import DEFAULT_INGEST_RULE_VERSION, expected_dedup_key
from src.state_machine.learner import Learner, LearnerApplication

EXP = "exp_007"
SURFACE = "streak_risk_reminder"
ASSIGNED_AT = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
OCCURRED = ASSIGNED_AT + timedelta(hours=1)


def _uuid_for_variant(variant: str) -> UUID:
    for i in range(10_000):
        candidate = uuid5(NAMESPACE_URL, f"learner-{i}")
        if assign(str(candidate), EXP)[0] == variant:
            return candidate
    raise AssertionError(variant)


def _open(db: str) -> tuple[LearnerApplication, EvidenceStore]:
    application = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
    )
    return application, EvidenceStore(db)


@pytest.fixture
def env(tmp_path):
    application, evidence = _open(str(tmp_path / "events.db"))
    yield application, evidence
    evidence.close()
    application.close()


def _learner(application, variant: str, *, assigned: bool = True) -> str:
    learner = Learner._create(Learner.Created, id=_uuid_for_variant(variant))
    if assigned:
        learner.assign_experiment(EXP, assigned_at=ASSIGNED_AT)
    application.save(learner)
    return str(learner.id)


def _envelope(learner_id, view_id="view-1", *, occurred_at=OCCURRED, event_id=None, **payload):
    event_id = event_id or str(uuid4())
    body = {
        "view_id": view_id,
        "exp_id": EXP,
        "exposure_surface": SURFACE,
        "timezone": "Asia/Kolkata",
    }
    body.update(payload)
    return {
        "event_id": event_id,
        "event_type": "learning.surface_viewed",
        "schema_version": 1,
        "occurred_at": occurred_at.isoformat(),
        "ingested_at": (occurred_at + timedelta(minutes=1)).isoformat(),
        "subject_id": learner_id,
        "producer": "learning-svc",
        "correlation_id": str(uuid4()),
        "causation_id": None,
        "dedup_key": expected_dedup_key(event_id, DEFAULT_INGEST_RULE_VERSION),
        "payload": body,
        "provenance": {"source": "client"},
    }


def _config(envelope=None, *, age=timedelta(minutes=5)):
    """Default 72 h window with a fixed trusted clock `age` after occurred_at."""
    now = datetime.fromisoformat(envelope["occurred_at"]) + age if envelope else OCCURRED
    return IngestConfig(DEFAULT_INGEST_RULE_VERSION, clock=lambda: now)


def _ingest(application, evidence, envelope, *, producer="learning-svc", age=timedelta(minutes=5)):
    from src.ingestion.service import ingest_surface_viewed

    return ingest_surface_viewed(
        application,
        evidence,
        envelope,
        authenticated_producer=producer,
        config=_config(envelope, age=age),
    )


def _events(application, learner_id, name=None):
    events = list(application.events.get(UUID(learner_id)))
    return events if name is None else [e for e in events if type(e).__name__ == name]


def _assert_rejected(application, evidence, learner_id, result, reason, before):
    assert (result.status, result.reason) == ("rejected", reason)
    assert [(r.kind, r.reason) for r in evidence.list(learner_id)] == [("rejected", reason)]
    assert len(_events(application, learner_id)) == before
    assert _events(application, learner_id, "ExperimentExposed") == []


def test_valid_view_is_accepted_and_stored_once(env):
    application, evidence = env
    learner_id = _learner(application, "variant_a")
    envelope = _envelope(learner_id)

    result = _ingest(application, evidence, envelope)

    assert (result.status, result.reason) == ("accepted", None)
    exposed = _events(application, learner_id, "ExperimentExposed")
    assert len(exposed) == 1
    stored = application.repository.get(UUID(learner_id)).experiment_exposures["view-1"]
    assert stored["variant"] == "variant_a"
    assert stored["assignment_ref"] == "experiment_assignment:exp_007"
    assert stored["exposure_surface"] == SURFACE
    assert stored["exposed_at"] == OCCURRED.isoformat()
    assert application.repository.get(UUID(learner_id)).exposure_sources == {
        "view-1": envelope["event_id"]
    }
    assert evidence.list(learner_id) == []


def test_same_envelope_again_is_duplicate_view_id(env):
    application, evidence = env
    learner_id = _learner(application, "variant_a")
    envelope = _envelope(learner_id)
    assert _ingest(application, evidence, envelope).status == "accepted"

    again = _ingest(application, evidence, envelope)
    assert again.status == "duplicate"

    # Same view_id under a new event_id is the duplicate_view_id case.
    second = _ingest(application, evidence, _envelope(learner_id))
    assert (second.status, second.reason) == ("duplicate", "duplicate_view_id")
    assert len(_events(application, learner_id, "ExperimentExposed")) == 1
    assert "duplicate_view_id" in [r.reason for r in evidence.list(learner_id)]


def test_no_assignment_is_rejected_and_saves_nothing(env):
    application, evidence = env
    learner_id = _learner(application, "variant_a", assigned=False)
    before = len(_events(application, learner_id))

    result = _ingest(application, evidence, _envelope(learner_id))

    _assert_rejected(application, evidence, learner_id, result, "no_assignment", before)
    assert application.repository.get(UUID(learner_id)).version == before


def test_unknown_learner_creates_and_saves_nothing(env):
    application, evidence = env
    unknown = str(uuid4())
    with pytest.raises(RuntimeError, match="must exist"):
        _ingest(application, evidence, _envelope(unknown))
    with pytest.raises(AggregateNotFoundError):
        application.repository.get(UUID(unknown))
    assert list(application.events.get(UUID(unknown))) == []


def test_variant_b_has_no_treatment_surface(env):
    application, evidence = env
    learner_id = _learner(application, "variant_b")
    before = len(_events(application, learner_id))
    result = _ingest(application, evidence, _envelope(learner_id))
    _assert_rejected(
        application, evidence, learner_id, result, "no_treatment_surface_for_variant", before
    )


def test_wrong_surface_is_rejected(env):
    application, evidence = env
    learner_id = _learner(application, "variant_a")
    before = len(_events(application, learner_id))
    result = _ingest(
        application, evidence, _envelope(learner_id, exposure_surface="home_banner")
    )
    _assert_rejected(application, evidence, learner_id, result, "not_treatment_surface", before)


@pytest.mark.parametrize(
    "extra", [{"variant": "variant_a"}, {"assignment_ref": "experiment_assignment:exp_007"}]
)
def test_derived_fields_in_payload_are_schema_invalid(env, extra):
    application, evidence = env
    learner_id = _learner(application, "variant_a")
    before = len(_events(application, learner_id))
    result = _ingest(application, evidence, _envelope(learner_id, **extra))
    _assert_rejected(application, evidence, learner_id, result, "schema_invalid", before)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda e: e.update(producer="assessment-svc"),
        lambda e: e.update(producer="gamification-engine"),
        lambda e: e.update(event_type="learning.lesson_completed"),
        lambda e: e.update(event_type="learning.unknown_event"),
    ],
)
def test_wrong_producer_or_event_type_is_untrusted(env, mutate):
    application, evidence = env
    learner_id = _learner(application, "variant_a")
    before = len(_events(application, learner_id))
    envelope = _envelope(learner_id)
    mutate(envelope)
    result = _ingest(application, evidence, envelope, producer=envelope["producer"])
    _assert_rejected(application, evidence, learner_id, result, "untrusted_producer", before)


def test_spoofed_authenticated_producer_is_untrusted(env):
    application, evidence = env
    learner_id = _learner(application, "variant_a")
    before = len(_events(application, learner_id))
    result = _ingest(application, evidence, _envelope(learner_id), producer="other-svc")
    _assert_rejected(application, evidence, learner_id, result, "untrusted_producer", before)


def test_view_older_than_late_window_is_rejected(env):
    application, evidence = env
    learner_id = _learner(application, "variant_a")
    before = len(_events(application, learner_id))
    result = _ingest(
        application, evidence, _envelope(learner_id), age=timedelta(hours=72, minutes=1)
    )
    _assert_rejected(application, evidence, learner_id, result, "outside_late_window", before)


def test_view_before_assignment_is_rejected(env):
    application, evidence = env
    learner_id = _learner(application, "variant_a")
    before = len(_events(application, learner_id))
    envelope = _envelope(learner_id, occurred_at=ASSIGNED_AT - timedelta(hours=1))
    result = _ingest(application, evidence, envelope)
    _assert_rejected(
        application, evidence, learner_id, result, "exposure_before_assignment", before
    )
