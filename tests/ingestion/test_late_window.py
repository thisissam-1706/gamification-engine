"""Week 4 Fix 11a: the 72-hour late-event window is enforced at ingestion.

Receive time is IngestConfig.clock(), never the envelope's ingested_at. A
delivery received strictly more than late_window after its occurred_at is
rejected with outside_late_window before the aggregate is loaded. A redelivery
of an already accepted event is still answered as a duplicate.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from src.ingestion.config import DEFAULT_CONFIG, IngestConfig, utc_now
from src.ingestion.evidence import EvidenceStore
from src.ingestion.service import ingest_lesson_completed
from src.state_machine.learner import Learner, LearnerApplication
from tests.ingestion.test_ingestion_service import _envelope

OCCURRED = datetime(2026, 9, 21, 10, 0, 0, tzinfo=timezone.utc)
EVENT_ID = "00000000-0000-4000-8000-000000000b11"


class FixedClock:
    """A settable trusted clock."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _open(tmp_path) -> tuple[LearnerApplication, EvidenceStore]:
    db = str(tmp_path / "events.db")
    application = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
    )
    return application, EvidenceStore(db)


def _lesson(learner_id: str, event_id: str = EVENT_ID, ingested_at: datetime | None = None) -> dict:
    envelope = _envelope(learner_id, "cmp-late", event_id)
    envelope["occurred_at"] = "2026-09-21T10:00:00+00:00"
    envelope["ingested_at"] = (ingested_at or OCCURRED + timedelta(minutes=1)).isoformat()
    return envelope


def _ingest(application, evidence, envelope, clock, late_window=timedelta(hours=72)):
    config = IngestConfig("v1", late_window=late_window, clock=clock)
    result = ingest_lesson_completed(
        application, evidence, envelope, authenticated_producer="learning-svc", config=config
    )
    return result.status, result.reason


@pytest.fixture
def stores(tmp_path):
    application, evidence = _open(tmp_path)
    learner = Learner()
    application.save(learner)
    yield application, evidence, str(learner.id)
    evidence.close()
    application.close()


# -- config ---------------------------------------------------------------------


def test_default_config_has_72_hour_window_and_system_clock():
    assert DEFAULT_CONFIG.rule_version == "v1"
    assert DEFAULT_CONFIG.late_window == timedelta(hours=72)
    assert DEFAULT_CONFIG.clock is utc_now
    assert utc_now().tzinfo is not None


@pytest.mark.parametrize("window", [timedelta(0), timedelta(seconds=-1), 72, "72h"])
def test_late_window_must_be_positive_or_none(window):
    with pytest.raises(ValueError, match="late_window"):
        IngestConfig("v1", late_window=window)
    assert IngestConfig("v1", late_window=None).late_window is None


def test_naive_clock_is_refused(stores):
    application, evidence, learner_id = stores
    with pytest.raises(ValueError, match="timezone-aware"):
        _ingest(application, evidence, _lesson(learner_id), lambda: datetime(2026, 9, 21, 11))


# -- boundary -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "delay",
    [timedelta(hours=71, minutes=59), timedelta(hours=72)],
    ids=["71h59m", "exactly-72h"],
)
def test_inside_or_on_the_window_is_accepted(stores, delay):
    application, evidence, learner_id = stores
    clock = FixedClock(OCCURRED + delay)
    assert _ingest(application, evidence, _lesson(learner_id), clock) == ("accepted", None)
    assert application.repository.get(UUID(learner_id)).xp_total == 100
    # The trusted receive time is persisted on the provenance row.
    assert evidence.delivery_binding(EVENT_ID).received_at == (OCCURRED + delay).isoformat()


def test_one_second_past_the_window_is_rejected_without_state_change(stores):
    application, evidence, learner_id = stores
    events_before = list(application.events.get(UUID(learner_id)))
    clock = FixedClock(OCCURRED + timedelta(hours=72, seconds=1))

    assert _ingest(application, evidence, _lesson(learner_id), clock) == (
        "rejected",
        "outside_late_window",
    )

    assert application.repository.get(UUID(learner_id)).xp_total == 0
    assert list(application.events.get(UUID(learner_id))) == events_before
    assert [
        (row.kind, row.reason, row.action_id, row.event_id, row.received_at)
        for row in evidence.list(learner_id)
    ] == [
        (
            "rejected",
            "outside_late_window",
            "cmp-late",
            "00000000-0000-4000-8000-000000000b11",
            "2026-09-24T10:00:01+00:00",
        )
    ]
    assert evidence.delivery_binding(EVENT_ID) is None


# -- receive time comes from the clock -------------------------------------------


def test_envelope_ingested_at_inside_window_does_not_rescue_a_late_delivery(stores):
    application, evidence, learner_id = stores
    clock = FixedClock(OCCURRED + timedelta(hours=73))
    envelope = _lesson(learner_id, ingested_at=OCCURRED + timedelta(minutes=1))
    assert _ingest(application, evidence, envelope, clock) == ("rejected", "outside_late_window")
    assert application.repository.get(UUID(learner_id)).xp_total == 0


def test_envelope_ingested_at_outside_window_does_not_reject_a_timely_delivery(stores):
    application, evidence, learner_id = stores
    clock = FixedClock(OCCURRED + timedelta(hours=1))
    envelope = _lesson(learner_id, ingested_at=OCCURRED + timedelta(days=30))
    assert _ingest(application, evidence, envelope, clock) == ("accepted", None)
    assert application.repository.get(UUID(learner_id)).xp_total == 100
    assert evidence.delivery_binding(EVENT_ID).received_at == "2026-09-21T11:00:00+00:00"


def test_every_evidence_row_uses_the_clock(stores):
    application, evidence, learner_id = stores
    clock = FixedClock(OCCURRED + timedelta(hours=2))
    bad_key = _lesson(learner_id)
    bad_key["dedup_key"] = "unrelated"
    assert _ingest(application, evidence, bad_key, clock) == ("rejected", "dedup_key_mismatch")
    assert [row.received_at for row in evidence.list(learner_id)] == ["2026-09-21T12:00:00+00:00"]


# -- ordering -----------------------------------------------------------------------


def test_redelivery_of_accepted_event_after_window_is_still_a_duplicate(stores):
    application, evidence, learner_id = stores
    clock = FixedClock(OCCURRED + timedelta(hours=1))
    envelope = _lesson(learner_id)
    assert _ingest(application, evidence, envelope, clock) == ("accepted", None)

    clock.now = OCCURRED + timedelta(days=10)
    events_before = list(application.events.get(UUID(learner_id)))
    assert _ingest(application, evidence, dict(envelope), clock) == (
        "duplicate",
        "duplicate_event_id",
    )
    assert list(application.events.get(UUID(learner_id))) == events_before
    assert application.repository.get(UUID(learner_id)).xp_total == 100
    assert [(row.kind, row.reason, row.received_at) for row in evidence.list(learner_id)] == [
        ("duplicate", "duplicate_event_id", "2026-10-01T10:00:00+00:00")
    ]
    # The binding keeps the time of the original acceptance.
    assert evidence.delivery_binding(EVENT_ID).received_at == "2026-09-21T11:00:00+00:00"


def test_unbounded_window_accepts_any_delay(stores):
    application, evidence, learner_id = stores
    clock = FixedClock(OCCURRED + timedelta(days=365))
    assert _ingest(application, evidence, _lesson(learner_id), clock, late_window=None) == (
        "accepted",
        None,
    )
    assert application.repository.get(UUID(learner_id)).xp_total == 100
