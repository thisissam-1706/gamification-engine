"""Durable, idempotent storage for abuse signals (flag-only; never confirmed here)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import json
import sqlite3

from src.abuse.detectors import Signal

# Seconds a connection waits for another process's write lock before failing.
BUSY_TIMEOUT_SECONDS = 30.0

_COLUMNS = (
    "signal_id",
    "learner_id",
    "signal_type",
    "status",
    "rule_id",
    "rule_version",
    "rule_digest",
    "evidence_refs",
    "window_start",
    "window_end",
    "detected_at",
)


@dataclass(frozen=True)
class SignalRecord:
    signal_id: str
    learner_id: str
    signal_type: str
    status: str
    rule_id: str
    rule_version: str
    rule_digest: str
    evidence_refs: tuple[str, ...]
    window_start: str
    window_end: str
    detected_at: str


class AbuseSignalStore:
    """Signals keyed by their deterministic signal_id; re-running never duplicates."""

    def __init__(self, database: str) -> None:
        self.connection = sqlite3.connect(database, timeout=BUSY_TIMEOUT_SECONDS)
        # Take the write lock before reading the schema so concurrent openers queue up.
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS abuse_signals (
                    signal_id TEXT PRIMARY KEY,
                    learner_id TEXT NOT NULL,
                    signal_type TEXT NOT NULL CHECK(signal_type IN
                        ('replay', 'impossible_timing', 'scripted_farming', 'duplicate_exploit')),
                    -- 'confirmed' needs a future, explicitly authorized action and a migration.
                    status TEXT NOT NULL CHECK(status = 'suspected'),
                    rule_id TEXT NOT NULL,
                    rule_version TEXT NOT NULL,
                    rule_digest TEXT NOT NULL,
                    evidence_refs TEXT NOT NULL,
                    window_start TEXT NOT NULL,
                    window_end TEXT NOT NULL,
                    detected_at TEXT NOT NULL
                )
                """
            )
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS abuse_signals_learner ON abuse_signals (learner_id)"
            )
        except BaseException:
            self.connection.rollback()
            self.connection.close()
            raise
        self.connection.commit()

    def save_many(self, signals: Iterable[Signal], detected_at: str) -> tuple[int, int]:
        """Inserts signals not yet stored; returns (new, already_present).

        ``detected_at`` is supplied by the caller (never read from a clock) and is
        kept from the first insert.
        """
        new = existing = 0
        try:
            for signal in signals:
                cursor = self.connection.execute(
                    f"INSERT OR IGNORE INTO abuse_signals ({', '.join(_COLUMNS)}) "
                    f"VALUES ({', '.join('?' * len(_COLUMNS))})",
                    (
                        signal.signal_id,
                        signal.learner_id,
                        signal.signal_type,
                        signal.status,
                        signal.rule_id,
                        signal.rule_version,
                        signal.rule_digest,
                        json.dumps(list(signal.evidence_refs), separators=(",", ":")),
                        signal.detected_window[0],
                        signal.detected_window[1],
                        detected_at,
                    ),
                )
                if cursor.rowcount == 1:
                    new += 1
                else:
                    existing += 1
        except BaseException:
            self.connection.rollback()
            raise
        self.connection.commit()
        return new, existing

    def list(self, learner_id: str | None = None) -> list[SignalRecord]:
        query = f"SELECT {', '.join(_COLUMNS)} FROM abuse_signals"
        params: tuple[str, ...] = ()
        if learner_id is not None:
            query += " WHERE learner_id = ?"
            params = (learner_id,)
        query += " ORDER BY learner_id, signal_type, rule_id, signal_id"
        records = []
        for row in self.connection.execute(query, params):
            values = dict(zip(_COLUMNS, row))
            values["evidence_refs"] = tuple(json.loads(values["evidence_refs"]))
            records.append(SignalRecord(**values))
        return records

    def count(self) -> int:
        return self.connection.execute("SELECT COUNT(*) FROM abuse_signals").fetchone()[0]

    def close(self) -> None:
        self.connection.close()
