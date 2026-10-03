"""Durable records for deliveries that must not change learner state."""

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
from typing import Any


@dataclass(frozen=True)
class EvidenceRecord:
    id: int
    kind: str
    learner_id: str
    action_id: str | None
    event_id: str | None
    reason: str
    original_ref: str | None
    received_at: str


class EvidenceStore:
    """Stores duplicate and rejected evidence beside the event database."""

    def __init__(self, database: str) -> None:
        self.connection = sqlite3.connect(database)
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ingestion_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK(kind IN ('duplicate', 'rejected')),
                learner_id TEXT NOT NULL,
                action_id TEXT,
                event_id TEXT,
                reason TEXT NOT NULL,
                original_ref TEXT,
                received_at TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS accepted_deliveries (
                learner_id TEXT NOT NULL,
                action_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                PRIMARY KEY (learner_id, action_id)
            )
            """
        )
        self.connection.commit()

    def record(
        self,
        kind: str,
        learner_id: str,
        reason: str,
        *,
        action_id: str | None = None,
        event_id: str | None = None,
        original_ref: str | None = None,
        received_at: str | None = None,
    ) -> EvidenceRecord:
        timestamp = received_at or datetime.now(timezone.utc).isoformat()
        cursor = self.connection.execute(
            """
            INSERT INTO ingestion_evidence
            (kind, learner_id, action_id, event_id, reason, original_ref, received_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (kind, learner_id, action_id, event_id, reason, original_ref, timestamp),
        )
        self.connection.commit()
        return EvidenceRecord(
            cursor.lastrowid,
            kind,
            learner_id,
            action_id,
            event_id,
            reason,
            original_ref,
            timestamp,
        )

    def list(self, learner_id: str | None = None) -> list[EvidenceRecord]:
        query = "SELECT id, kind, learner_id, action_id, event_id, reason, original_ref, received_at FROM ingestion_evidence"
        params: tuple[Any, ...] = ()
        if learner_id is not None:
            query += " WHERE learner_id = ?"
            params = (learner_id,)
        query += " ORDER BY id"
        return [EvidenceRecord(*row) for row in self.connection.execute(query, params)]

    def remember_delivery(self, learner_id: str, action_id: str, event_id: str) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO accepted_deliveries (learner_id, action_id, event_id)
            VALUES (?, ?, ?)
            """,
            (learner_id, action_id, event_id),
        )
        self.connection.commit()

    def original_event_id(self, learner_id: str, action_id: str) -> str | None:
        row = self.connection.execute(
            "SELECT event_id FROM accepted_deliveries WHERE learner_id = ? AND action_id = ?",
            (learner_id, action_id),
        ).fetchone()
        return row[0] if row else None

    def close(self) -> None:
        self.connection.close()
