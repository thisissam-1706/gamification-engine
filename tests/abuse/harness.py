"""Drives the REAL ingestion path on a temporary SQLite database with an injected clock."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from src.ingestion.config import IngestConfig
from src.ingestion.evidence import EvidenceStore
from src.ingestion.service import IngestionResult, ingest_lesson_completed
from src.ingestion.validation import expected_dedup_key
from src.state_machine.learner import Learner, LearnerApplication

UTC = timezone.utc
EPOCH = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)  # a Monday, ISO week 2026-W42


def sqlite_env(db: str) -> dict[str, str]:
    return {"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}


class Harness:
    """One database holding events and ingestion evidence; time only moves when told."""

    def __init__(self, db: str, rule_version: str = "v1") -> None:
        self.db = db
        self.rule_version = rule_version
        self.application = LearnerApplication(env=sqlite_env(db))
        self.evidence = EvidenceStore(db)
        self.now = EPOCH
        self.config = IngestConfig(rule_version, clock=lambda: self.now)
        self._event_counter = 0
        self._completion_counter = 0

    def new_learner(self) -> str:
        learner = Learner()
        self.application.save(learner)
        return str(learner.id)

    def next_event_id(self) -> str:
        self._event_counter += 1
        return f"00000000-0000-4000-8000-{self._event_counter:012d}"

    def next_completion_id(self) -> str:
        self._completion_counter += 1
        return f"cmp-{self._completion_counter:05d}"

    def envelope(
        self,
        learner_id: str,
        completion_id: str,
        event_id: str,
        occurred_at: datetime,
        *,
        lesson_id: str = "lesson-1",
        kind: str | None = None,
        payload_extra: dict | None = None,
    ) -> dict:
        payload = {"completion_id": completion_id, "lesson_id": lesson_id, "timezone": "UTC"}
        if kind is not None:
            payload["completion_kind"] = kind
        payload.update(payload_extra or {})
        return {
            "event_id": event_id,
            "event_type": "learning.lesson_completed",
            "schema_version": 1,
            "occurred_at": occurred_at.isoformat(),
            "ingested_at": (occurred_at + timedelta(seconds=1)).isoformat(),
            "subject_id": learner_id,
            "producer": "learning-svc",
            "correlation_id": f"corr-{event_id}",
            "causation_id": None,
            "dedup_key": expected_dedup_key(event_id, self.rule_version),
            "payload": payload,
            "provenance": {"source": "client"},
        }

    def deliver(self, envelope: dict, received_at: datetime | None = None) -> IngestionResult:
        """Ingests one envelope; the trusted receive time defaults to occurred_at + 1 s."""
        occurred = datetime.fromisoformat(envelope["occurred_at"])
        self.now = received_at or occurred + timedelta(seconds=1)
        return ingest_lesson_completed(
            self.application,
            self.evidence,
            envelope,
            authenticated_producer="learning-svc",
            config=self.config,
        )

    def complete(
        self,
        learner_id: str,
        occurred_at: datetime,
        *,
        lesson_id: str = "lesson-1",
        kind: str | None = None,
        completion_id: str | None = None,
        event_id: str | None = None,
        received_at: datetime | None = None,
    ) -> IngestionResult:
        envelope = self.envelope(
            learner_id,
            completion_id or self.next_completion_id(),
            event_id or self.next_event_id(),
            occurred_at,
            lesson_id=lesson_id,
            kind=kind,
        )
        return self.deliver(envelope, received_at)

    def learner(self, learner_id: str) -> Learner:
        return self.application.repository.get(UUID(learner_id))

    def notifications(self) -> list:
        """Every stored notification, read page by page from the notification log."""
        log = self.application.notification_log
        out, position = [], 1
        while True:
            batch = list(log.select(start=position, limit=log.section_size))
            if not batch:
                return out
            out.extend(batch)
            position = batch[-1].id + 1

    def reopen(self) -> None:
        """Drops every object and reopens the same database file (a full rebuild)."""
        self.evidence.close()
        self.application.close()
        self.application = LearnerApplication(env=sqlite_env(self.db))
        self.evidence = EvidenceStore(self.db)

    def close(self) -> None:
        self.evidence.close()
        self.application.close()
