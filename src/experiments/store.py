"""Durable, first-write-wins storage for exp_007 eligibility and decision records."""

from __future__ import annotations

import sqlite3

# Seconds a connection waits for another process's write lock before failing.
BUSY_TIMEOUT_SECONDS = 30.0

ELIGIBILITY_COLUMNS = (
    "exp_id",
    "learner_id",
    "variant",
    "assigned_at",
    "eligible",
    "reason",
    "opted_out_push",
    "opted_out_streak_risk",
    "timezone",
    "tenure_days",
)

DECISION_COLUMNS = (
    "exp_id",
    "learner_id",
    "variant",
    "decided_at",
    "outcome",
    "reason",
    "proposal_id",
    "fact_event_id",
    "fact_status",
)


class ExperimentStore:
    """Eligibility rows keyed by (exp_id, learner_id); the first record is kept."""

    def __init__(self, database: str) -> None:
        self.connection = sqlite3.connect(database, timeout=BUSY_TIMEOUT_SECONDS)
        # Take the write lock before reading the schema so concurrent openers queue up.
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS experiment_eligibility (
                    exp_id TEXT NOT NULL,
                    learner_id TEXT NOT NULL,
                    variant TEXT NOT NULL,
                    assigned_at TEXT NOT NULL,
                    eligible INTEGER NOT NULL CHECK(eligible IN (0, 1)),
                    reason TEXT,
                    opted_out_push INTEGER,
                    opted_out_streak_risk INTEGER,
                    timezone TEXT,
                    tenure_days INTEGER,
                    PRIMARY KEY (exp_id, learner_id)
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS experiment_decisions (
                    exp_id TEXT NOT NULL,
                    learner_id TEXT NOT NULL,
                    variant TEXT NOT NULL,
                    decided_at TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    reason TEXT,
                    proposal_id TEXT,
                    fact_event_id TEXT,
                    fact_status TEXT,
                    PRIMARY KEY (exp_id, learner_id, decided_at)
                )
                """
            )
        except BaseException:
            self.connection.rollback()
            self.connection.close()
            raise
        self.connection.commit()

    def record_eligibility(self, row: dict) -> bool:
        """Inserts the row unless one exists for (exp_id, learner_id); True if inserted."""
        try:
            cursor = self.connection.execute(
                f"INSERT OR IGNORE INTO experiment_eligibility ({', '.join(ELIGIBILITY_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(ELIGIBILITY_COLUMNS))})",
                tuple(row[column] for column in ELIGIBILITY_COLUMNS),
            )
        except BaseException:
            self.connection.rollback()
            raise
        self.connection.commit()
        return cursor.rowcount == 1

    def record_decision(self, row: dict) -> bool:
        """Inserts the row unless one exists for the primary key; True if inserted."""
        try:
            cursor = self.connection.execute(
                f"INSERT OR IGNORE INTO experiment_decisions ({', '.join(DECISION_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(DECISION_COLUMNS))})",
                tuple(row[column] for column in DECISION_COLUMNS),
            )
        except BaseException:
            self.connection.rollback()
            raise
        self.connection.commit()
        return cursor.rowcount == 1

    def is_eligible(self, exp_id: str, learner_id: str) -> bool | None:
        """The stored eligibility of a learner, or None if no row exists."""
        found = self.connection.execute(
            "SELECT eligible FROM experiment_eligibility WHERE exp_id = ? AND learner_id = ?",
            (exp_id, learner_id),
        ).fetchone()
        return None if found is None else bool(found[0])

    def get_eligibility(self, exp_id: str, learner_id: str) -> tuple[bool, str | None] | None:
        """The stored (eligible, reason) of a learner, or None if no row exists."""
        found = self.connection.execute(
            "SELECT eligible, reason FROM experiment_eligibility WHERE exp_id = ? AND learner_id = ?",
            (exp_id, learner_id),
        ).fetchone()
        return None if found is None else (bool(found[0]), found[1])

    def list_decisions(self, exp_id: str, learner_id: str | None = None) -> list[dict]:
        """Decision rows of an experiment (optionally one learner), ordered by learner and time."""
        query = f"SELECT {', '.join(DECISION_COLUMNS)} FROM experiment_decisions WHERE exp_id = ?"
        params: tuple[str, ...] = (exp_id,)
        if learner_id is not None:
            query += " AND learner_id = ?"
            params += (learner_id,)
        query += " ORDER BY learner_id, decided_at"
        return [dict(zip(DECISION_COLUMNS, row)) for row in self.connection.execute(query, params)]

    def list_eligibility(self, exp_id: str) -> list[dict]:
        """Stored eligibility rows of an experiment, ordered by learner id."""
        query = (
            f"SELECT {', '.join(ELIGIBILITY_COLUMNS)} FROM experiment_eligibility "
            "WHERE exp_id = ? ORDER BY learner_id"
        )
        return [dict(zip(ELIGIBILITY_COLUMNS, row)) for row in self.connection.execute(query, (exp_id,))]

    def close(self) -> None:
        self.connection.close()
