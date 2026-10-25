"""Durable records for deliveries that must not change learner state."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
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


# Source envelope provenance kept on each delivery_ids row. All nullable:
# rows migrated or backfilled from the aggregate never saw the envelope.
PROVENANCE_COLUMNS = {
    "producer": "TEXT",
    "event_type": "TEXT",
    "schema_version": "INTEGER",
    "occurred_at": "TEXT",
    "ingested_at": "TEXT",
    "correlation_id": "TEXT",
    "causation_id": "TEXT",
    "dedup_key": "TEXT",
    "provenance_json": "TEXT",
    "rule_version": "TEXT",
}
# Engine-side facts about the accepted decision, not taken from the envelope.
RULE_COLUMNS = {
    "rule_digest": "TEXT",
}
# Trusted ingress facts: received_at comes from IngestConfig.clock, never the envelope.
INGRESS_COLUMNS = {
    "received_at": "TEXT",
}
DELIVERY_COLUMNS = {**PROVENANCE_COLUMNS, **RULE_COLUMNS, **INGRESS_COLUMNS}

# Seconds a connection waits for another process's write lock before failing.
BUSY_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class DeliveryBinding:
    event_id: str
    learner_id: str
    action_id: str
    digest: str | None
    producer: str | None = None
    event_type: str | None = None
    schema_version: int | None = None
    occurred_at: str | None = None
    ingested_at: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    dedup_key: str | None = None
    provenance_json: str | None = None
    rule_version: str | None = None
    rule_digest: str | None = None
    received_at: str | None = None


class EvidenceStore:
    """Stores duplicate and rejected evidence beside the event database."""

    def __init__(self, database: str) -> None:
        # A second opener waits for the lock (up to the timeout) instead of failing.
        self.connection = sqlite3.connect(database, timeout=BUSY_TIMEOUT_SECONDS)
        # One IMMEDIATE transaction takes the write lock before the schema is read,
        # so concurrent openers migrate one after another, never interleaved.
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self._create_and_migrate_schema()
        except BaseException:
            self.connection.rollback()
            self.connection.close()
            raise
        self.connection.commit()

    def _create_and_migrate_schema(self) -> None:
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
        self.connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ingestion_evidence_duplicate_delivery
            ON ingestion_evidence (kind, learner_id, action_id, event_id, reason)
            WHERE kind = 'duplicate'
            """
        )
        # Binds each transport event_id to the one action it first delivered.
        # digest is NULL only for rows migrated from accepted_deliveries, whose
        # original envelope bytes were never stored.
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS delivery_ids (
                event_id TEXT PRIMARY KEY,
                learner_id TEXT NOT NULL,
                action_id TEXT NOT NULL,
                digest TEXT
            )
            """
        )
        # Read inside the transaction: the column set cannot change under us.
        existing = {
            row[1] for row in self.connection.execute("PRAGMA table_info(delivery_ids)")
        }
        for column, column_type in DELIVERY_COLUMNS.items():
            if column in existing:
                continue
            try:
                self.connection.execute(
                    f"ALTER TABLE delivery_ids ADD COLUMN {column} {column_type}"
                )
            except sqlite3.OperationalError as exc:
                # Defensive: another writer added it, so it is already migrated.
                if "duplicate column name" not in str(exc):
                    raise
        self.connection.execute(
            """
            INSERT OR IGNORE INTO delivery_ids (event_id, learner_id, action_id, digest)
            SELECT event_id, learner_id, action_id, NULL FROM accepted_deliveries
            """
        )

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
            INSERT OR IGNORE INTO ingestion_evidence
            (kind, learner_id, action_id, event_id, reason, original_ref, received_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (kind, learner_id, action_id, event_id, reason, original_ref, timestamp),
        )
        self.connection.commit()
        if cursor.lastrowid is None or cursor.rowcount == 0:
            row = self.connection.execute(
                """
                SELECT id, kind, learner_id, action_id, event_id, reason,
                       original_ref, received_at
                FROM ingestion_evidence
                WHERE kind = ? AND learner_id = ? AND action_id IS ?
                  AND event_id IS ? AND reason = ?
                """,
                (kind, learner_id, action_id, event_id, reason),
            ).fetchone()
            if row is None:
                raise RuntimeError("failed to record ingestion evidence")
            return EvidenceRecord(*row)
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

    def list_mentioning(self, event_id: str) -> list[EvidenceRecord]:
        """Rows recorded for this event_id or pointing back at it as original_ref."""
        return [
            EvidenceRecord(*row)
            for row in self.connection.execute(
                """
                SELECT id, kind, learner_id, action_id, event_id, reason,
                       original_ref, received_at
                FROM ingestion_evidence
                WHERE event_id = ? OR original_ref = ?
                ORDER BY id
                """,
                (event_id, event_id),
            )
        ]

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

    def bind_delivery(
        self,
        event_id: str,
        learner_id: str,
        action_id: str,
        digest: str | None,
        *,
        producer: str | None = None,
        event_type: str | None = None,
        schema_version: int | None = None,
        occurred_at: str | None = None,
        ingested_at: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        dedup_key: str | None = None,
        provenance: Any = None,
        rule_version: str | None = None,
        rule_digest: str | None = None,
        received_at: str | None = None,
    ) -> None:
        provenance_json = (
            json.dumps(provenance, sort_keys=True, separators=(",", ":"))
            if provenance is not None
            else None
        )
        self.connection.execute(
            f"""
            INSERT OR IGNORE INTO delivery_ids
            (event_id, learner_id, action_id, digest, {", ".join(DELIVERY_COLUMNS)})
            VALUES ({", ".join("?" * (4 + len(DELIVERY_COLUMNS)))})
            """,
            (
                event_id,
                learner_id,
                action_id,
                digest,
                producer,
                event_type,
                schema_version,
                occurred_at,
                ingested_at,
                correlation_id,
                causation_id,
                dedup_key,
                provenance_json,
                rule_version,
                rule_digest,
                received_at,
            ),
        )
        self.connection.commit()

    def fill_delivery_digest(self, event_id: str, digest: str) -> None:
        self.connection.execute(
            "UPDATE delivery_ids SET digest = ? WHERE event_id = ? AND digest IS NULL",
            (digest, event_id),
        )
        self.connection.commit()

    def delivery_binding(self, event_id: str) -> DeliveryBinding | None:
        row = self.connection.execute(
            f"""
            SELECT event_id, learner_id, action_id, digest, {", ".join(DELIVERY_COLUMNS)}
            FROM delivery_ids WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()
        return DeliveryBinding(*row) if row else None

    def close(self) -> None:
        self.connection.close()
