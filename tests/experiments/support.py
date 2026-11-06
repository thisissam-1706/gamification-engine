"""Shared helpers for the AT-12/AT-13 experiment acceptance tests."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5

from src.experiments.assignment import assign
from src.ingestion.config import IngestConfig
from src.ingestion.evidence import EvidenceStore
from src.ingestion.validation import DEFAULT_INGEST_RULE_VERSION, expected_dedup_key
from src.state_machine.learner import Learner, LearnerApplication

EXP_ID = "exp_007"
TREATMENT_SURFACE = "streak_risk_reminder"
ASSIGNED_AT = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def uuid_for_variant(variant: str, exp_id: str = EXP_ID, nth: int = 0) -> UUID:
    """The nth uuid5 learner id that assign() puts in `variant`; none is hardcoded."""
    seen = 0
    for i in range(10_000):
        candidate = uuid5(NAMESPACE_URL, f"learner-{i}")
        if assign(str(candidate), exp_id)[0] == variant:
            if seen == nth:
                return candidate
            seen += 1
    raise AssertionError(f"no uuid found for {variant}")


@contextmanager
def open_persisted(tmp_path: Path):
    """A SQLite-backed LearnerApplication and EvidenceStore on one file in tmp_path.

    Calling it again with the same tmp_path opens fresh objects on the same files.
    """
    db = str(tmp_path / "events.db")
    application = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
    )
    evidence = EvidenceStore(db)
    try:
        yield application, evidence
    finally:
        evidence.close()
        application.close()


def create_learner(application: LearnerApplication, variant: str, exp_id: str = EXP_ID) -> str:
    """Saves a new learner whose id falls into `variant`; returns its id."""
    learner = Learner._create(Learner.Created, id=uuid_for_variant(variant, exp_id))
    application.save(learner)
    return str(learner.id)


def clocked(now: datetime) -> IngestConfig:
    """The default 72 h window with a fixed trusted clock."""
    return IngestConfig(DEFAULT_INGEST_RULE_VERSION, clock=lambda: now)


def _envelope(event_type: str, learner_id: str, occurred_at: datetime, payload: dict) -> dict:
    event_id = str(uuid4())
    return {
        "event_id": event_id,
        "event_type": event_type,
        "schema_version": 1,
        "occurred_at": occurred_at.isoformat(),
        "ingested_at": (occurred_at + timedelta(minutes=1)).isoformat(),
        "subject_id": learner_id,
        "producer": "learning-svc",
        "correlation_id": str(uuid4()),
        "causation_id": None,
        "dedup_key": expected_dedup_key(event_id, DEFAULT_INGEST_RULE_VERSION),
        "payload": payload,
        "provenance": {"source": "client"},
    }


def surface_viewed_envelope(
    learner_id: str,
    view_id: str,
    occurred_at: datetime,
    *,
    exp_id: str = EXP_ID,
    exposure_surface: str = TREATMENT_SURFACE,
) -> dict:
    return _envelope(
        "learning.surface_viewed",
        learner_id,
        occurred_at,
        {
            "view_id": view_id,
            "exp_id": exp_id,
            "exposure_surface": exposure_surface,
            "timezone": "Asia/Kolkata",
        },
    )


def lesson_completed_envelope(learner_id: str, completion_id: str, occurred_at: datetime) -> dict:
    return _envelope(
        "learning.lesson_completed",
        learner_id,
        occurred_at,
        {"completion_id": completion_id, "lesson_id": "lesson-1", "timezone": "Asia/Kolkata"},
    )


def ingest_surface_viewed(application, evidence, envelope):
    from src.ingestion.service import ingest_surface_viewed as ingest

    now = datetime.fromisoformat(envelope["occurred_at"]) + timedelta(minutes=5)
    return ingest(
        application, evidence, envelope,
        authenticated_producer="learning-svc", config=clocked(now),
    )


def ingest_lesson_completed(application, evidence, envelope):
    from src.ingestion.service import ingest_lesson_completed as ingest

    now = datetime.fromisoformat(envelope["occurred_at"]) + timedelta(minutes=5)
    return ingest(
        application, evidence, envelope,
        authenticated_producer="learning-svc", config=clocked(now),
    )


def stored_event_names(application: LearnerApplication, learner_id: str) -> list[str]:
    return [type(e).__name__ for e in application.events.get(UUID(learner_id))]


def exposure_state(application: LearnerApplication, learner_id: str) -> dict:
    learner = application.repository.get(UUID(learner_id))
    return {
        "assignments": {k: dict(v) for k, v in learner.experiment_assignments.items()},
        "exposures": {k: dict(v) for k, v in learner.experiment_exposures.items()},
        "first": dict(learner.first_exposure_view_ids),
        "first_exposure": learner.first_experiment_exposure(EXP_ID),
    }
