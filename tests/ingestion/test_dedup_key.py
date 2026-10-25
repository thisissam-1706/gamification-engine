"""Week 4 Fix 6b: the envelope dedup_key must be "<event_id>:<rule_version>".

Week 3 review row 26: an unrelated dedup_key was accepted. Ingestion now
rejects any key other than expected_dedup_key(event_id, rule version), records
rejected evidence, and leaves the learner unchanged.
"""

from uuid import UUID, uuid4

import pytest

from src.ingestion.evidence import EvidenceStore
from src.ingestion.service import ingest_lesson_completed
from src.ingestion.validation import DEFAULT_INGEST_RULE_VERSION, expected_dedup_key
from src.state_machine.learner import Learner, LearnerApplication
from tests.ingestion.test_ingestion_service import _envelope

EVENT_ID = "00000000-0000-4000-8000-0000000006b1"


@pytest.fixture
def stores(tmp_path):
    db = str(tmp_path / "events.db")
    application = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
    )
    evidence = EvidenceStore(db)
    learner = Learner()
    application.save(learner)
    yield application, evidence, str(learner.id)
    evidence.close()
    application.close()


def _ingest(application, evidence, envelope):
    result = ingest_lesson_completed(
        application, evidence, envelope, authenticated_producer="learning-svc"
    )
    return result.status, result.reason


def test_expected_dedup_key_format():
    assert DEFAULT_INGEST_RULE_VERSION == "v1"
    assert expected_dedup_key(EVENT_ID, "v1") == f"{EVENT_ID}:v1"
    assert expected_dedup_key(EVENT_ID, "v2") == f"{EVENT_ID}:v2"


def test_correct_dedup_key_is_accepted(stores):
    application, evidence, learner_id = stores
    envelope = _envelope(learner_id, "cmp-A", EVENT_ID)
    assert envelope["dedup_key"] == f"{EVENT_ID}:v1"

    assert _ingest(application, evidence, envelope) == ("accepted", None)
    assert application.repository.get(UUID(learner_id)).xp_total == 100
    assert evidence.list(learner_id) == []


@pytest.mark.parametrize(
    "dedup_key",
    [
        "lesson_completed:cmp-A",  # the old fixture format
        f"{uuid4()}:v1",  # right format, another delivery's event_id
        EVENT_ID,  # rule version missing
        f"{EVENT_ID}:v1 ",  # not an exact match
    ],
)
def test_wrong_dedup_key_is_rejected_without_state_change(stores, dedup_key):
    application, evidence, learner_id = stores
    events_before = list(application.events.get(UUID(learner_id)))
    envelope = _envelope(learner_id, "cmp-A", EVENT_ID)
    envelope["dedup_key"] = dedup_key

    assert _ingest(application, evidence, envelope) == ("rejected", "dedup_key_mismatch")

    learner = application.repository.get(UUID(learner_id))
    assert learner.xp_total == 0
    assert learner.completed_lesson_ids == set()
    assert len(list(application.events.get(learner.id))) == len(events_before)
    rows = evidence.list(learner_id)
    assert [(row.kind, row.reason, row.action_id, row.event_id) for row in rows] == [
        ("rejected", "dedup_key_mismatch", "cmp-A", EVENT_ID)
    ]
    # A rejected delivery does not bind its event_id, so a corrected resend is accepted.
    assert evidence.delivery_binding(EVENT_ID) is None
    assert _ingest(application, evidence, _envelope(learner_id, "cmp-A", EVENT_ID)) == (
        "accepted",
        None,
    )
    assert application.repository.get(learner.id).xp_total == 100


def test_dedup_key_with_another_rule_version_is_rejected(stores):
    application, evidence, learner_id = stores
    events_before = list(application.events.get(UUID(learner_id)))
    envelope = _envelope(learner_id, "cmp-A", EVENT_ID)
    envelope["dedup_key"] = expected_dedup_key(EVENT_ID, "v2")
    assert envelope["dedup_key"] != expected_dedup_key(EVENT_ID, DEFAULT_INGEST_RULE_VERSION)

    assert _ingest(application, evidence, envelope) == ("rejected", "dedup_key_mismatch")

    learner = application.repository.get(UUID(learner_id))
    assert learner.xp_total == 0
    assert len(list(application.events.get(learner.id))) == len(events_before)
    assert [(row.kind, row.reason) for row in evidence.list(learner_id)] == [
        ("rejected", "dedup_key_mismatch")
    ]


def test_earlier_rejection_reasons_take_precedence_over_dedup_key(stores):
    application, evidence, learner_id = stores
    envelope = _envelope(learner_id, "cmp-A", EVENT_ID)
    envelope["dedup_key"] = "unrelated"
    envelope["producer"] = "assessment-svc"
    result = ingest_lesson_completed(
        application, evidence, envelope, authenticated_producer="assessment-svc"
    )
    assert (result.status, result.reason) == ("rejected", "untrusted_producer")
