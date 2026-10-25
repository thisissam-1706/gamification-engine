from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3
import time
from typing import Any, Sequence

from src.leaderboard.models import (
    CohortDefinition,
    LeaderboardEntry,
    RankedLearner,
    ReconciliationAudit,
)


class LeaderboardStore(ABC):
    """Abstract interface for leaderboard read model persistence."""

    @abstractmethod
    def transaction(self):
        """Context manager for executing operations within a single atomic transaction."""
        raise NotImplementedError

    @abstractmethod
    def apply_award_atomic(
        self,
        event_id: str,
        subject_id: str,
        epoch: str,
        xp_amount: int,
        occurred_at: str,
        global_score: int | None = None,
        global_first_reached_at: str | None = None,
        cohort_id: str | None = None,
        cohort_score: int | None = None,
        cohort_first_reached_at: str | None = None,
        watermark_offset: int = 0,
    ) -> bool:
        """Applies global + cohort score updates, tracking record, and checkpoint in one atomic transaction.

        Returns False if event_id was already processed (duplicate).
        """
        raise NotImplementedError

    @abstractmethod
    def is_event_applied(self, event_id: str, subject_id: str | None = None) -> bool:
        """Returns True if event_id has already been processed for the subject."""
        raise NotImplementedError

    @abstractmethod
    def is_offset_processed(self, offset: int) -> bool:
        """Returns True if the offset has already been recorded as processed."""
        raise NotImplementedError

    @abstractmethod
    def record_processed_offset(
        self, offset: int, cohort_id: str | None = None, occurred_at: str = ""
    ) -> int:
        """Records stream offset as processed and returns the highest contiguous committed offset."""
        raise NotImplementedError

    @abstractmethod
    def get_committed_offset(self, scope_key: str = "global") -> int:
        """Returns contiguous committed application offset."""
        raise NotImplementedError

    @abstractmethod
    def set_committed_offset(
        self, scope_key: str, offset: int, updated_at: str = ""
    ) -> None:
        """Updates contiguous committed application offset."""
        raise NotImplementedError

    @abstractmethod
    def record_reconciliation_audit(self, audit: ReconciliationAudit) -> None:
        """Persists a reconciliation audit record."""
        raise NotImplementedError

    @abstractmethod
    def get_reconciliation_audits(self) -> list[ReconciliationAudit]:
        """Returns all persisted reconciliation audit records."""
        raise NotImplementedError

    @abstractmethod
    def get_epoch_entries(
        self, scope_type: str, epoch: str
    ) -> list[LeaderboardEntry]:
        """Returns all entries for an epoch across all scopes of scope_type."""
        raise NotImplementedError

    @abstractmethod
    def get_member_cohort(self, epoch: str, subject_id: str) -> str | None:
        """Returns the cohort ID for a subject in a specific epoch, if assigned."""
        raise NotImplementedError

    @abstractmethod
    def get_member_cohorts(self, epoch: str, subject_id: str) -> list[str]:
        """Returns all cohort IDs for a subject in a specific epoch."""
        raise NotImplementedError

    @abstractmethod
    def remove_cohort_memberships(
        self, epoch: str, subject_id: str, except_cohort_id: str | None = None
    ) -> int:
        """Removes any cohort memberships for subject_id in epoch, optionally preserving except_cohort_id."""
        raise NotImplementedError

    @abstractmethod
    def save_cohort_definition(self, cohort: CohortDefinition) -> None:
        """Persists or updates a cohort definition."""
        raise NotImplementedError

    @abstractmethod
    def get_cohort_definition(self, cohort_id: str) -> CohortDefinition | None:
        """Retrieves a cohort definition by ID."""
        raise NotImplementedError

    @abstractmethod
    def get_cohort_member_count(self, cohort_id: str, epoch: str) -> int:
        """Returns the number of distinct learners assigned to cohort_id in epoch."""
        raise NotImplementedError

    @abstractmethod
    def get_applied_events(
        self, subject_id: str | None = None, epoch: str | None = None
    ) -> list[dict[str, Any]]:
        """Returns historical applied award facts, optionally filtered by subject and epoch."""
        raise NotImplementedError

    @abstractmethod
    def upsert_score(
        self,
        scope_type: str,
        scope_id: str,
        epoch: str,
        subject_id: str,
        score: int,
        first_reached_at: str,
        updated_at: str,
        watermark_offset: int = 0,
    ) -> bool:
        """Upserts a learner's score and first_reached_at timestamp."""
        raise NotImplementedError

    @abstractmethod
    def remove_member(
        self, scope_type: str, scope_id: str, epoch: str, subject_id: str
    ) -> bool:
        """Removes a learner from the specified scope and epoch."""
        raise NotImplementedError

    @abstractmethod
    def get_entry(
        self, scope_type: str, scope_id: str, epoch: str, subject_id: str
    ) -> LeaderboardEntry | None:
        """Fetches raw entry for a specific learner."""
        raise NotImplementedError

    @abstractmethod
    def get_rank(
        self, scope_type: str, scope_id: str, epoch: str, subject_id: str
    ) -> RankedLearner | None:
        """Calculates and returns the exact 1-indexed rank of a learner."""
        raise NotImplementedError

    @abstractmethod
    def get_top_k(
        self, scope_type: str, scope_id: str, epoch: str, k: int = 10
    ) -> list[RankedLearner]:
        """Returns the top K ranked learners in deterministic order."""
        raise NotImplementedError

    @abstractmethod
    def get_surrounding(
        self,
        scope_type: str,
        scope_id: str,
        epoch: str,
        subject_id: str,
        window: int = 5,
    ) -> list[RankedLearner]:
        """Returns learners surrounding the target subject within rank +/- window."""
        raise NotImplementedError

    @abstractmethod
    def get_all_entries(
        self, scope_type: str, scope_id: str, epoch: str
    ) -> list[LeaderboardEntry]:
        """Returns all entries for an epoch scope."""
        raise NotImplementedError

    @abstractmethod
    def get_watermark(self, scope_type: str, scope_id: str, epoch: str) -> int:
        """Returns highest watermark offset processed for this scope."""
        raise NotImplementedError

    @abstractmethod
    def record_completion_time(
        self, completion_id: str, learner_id: str, occurred_at: str
    ) -> None:
        """Persists the completion_id -> (learner_id, occurred_at) mapping."""
        raise NotImplementedError

    @abstractmethod
    def get_completion_time(
        self, completion_id: str, learner_id: str | None = None
    ) -> str | None:
        """Retrieves occurred_at for a given completion_id and learner_id."""
        raise NotImplementedError

    @abstractmethod
    def record_pending_award(
        self,
        event_id: str,
        subject_id: str,
        completion_id: str,
        xp_amount: int,
        status: str = "pending_time",
        watermark_offset: int = 0,
    ) -> None:
        """Parks an award that cannot yet be placed on a leaderboard (e.g. pending_time)."""
        raise NotImplementedError

    @abstractmethod
    def get_pending_awards(
        self, subject_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Returns parked pending awards, optionally filtered by subject_id."""
        raise NotImplementedError

    @abstractmethod
    def delete_pending_award(self, event_id: str, subject_id: str) -> bool:
        """Deletes a resolved or cleared pending award."""
        raise NotImplementedError

    @abstractmethod
    def get_all_epochs(self) -> list[str]:
        """Returns all distinct epochs present in the store."""
        raise NotImplementedError

    @abstractmethod
    def clear_derived(self) -> None:
        """Clears all derived projection read models and offsets, preserving reconciliation audits."""
        raise NotImplementedError

    @abstractmethod
    def wipe_all_for_tests(self) -> None:
        """Full storage wipe including durable audit records; strictly intended for testing."""
        raise NotImplementedError

    @abstractmethod
    def clear(
        self,
        scope_type: str | None = None,
        scope_id: str | None = None,
        epoch: str | None = None,
    ) -> None:
        """Clears entries matching the filter, or all derived entries if no filter provided."""
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        """Releases underlying storage resources."""
        raise NotImplementedError


class SQLiteLeaderboardStore(LeaderboardStore):
    """SQLite-backed store using compound indexing for exact deterministic tie-breaks."""

    def __init__(self, database: str = ":memory:") -> None:
        self.connection = sqlite3.connect(database, timeout=30.0)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self._transaction_depth = 0
        self._migrate_schema()
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS leaderboard_entries (
                scope_type TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                epoch TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                score INTEGER NOT NULL,
                first_reached_at TEXT NOT NULL,
                last_updated_at TEXT NOT NULL,
                watermark_offset INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (scope_type, scope_id, epoch, subject_id)
            )
            """
        )
        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_leaderboard_ordering
            ON leaderboard_entries (scope_type, scope_id, epoch, score DESC, first_reached_at ASC, subject_id ASC)
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS leaderboard_applied_events (
                event_id TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                epoch TEXT NOT NULL,
                xp_amount INTEGER NOT NULL,
                applied_at TEXT NOT NULL,
                stream_offset INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (event_id, subject_id)
            )
            """
        )
        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_applied_events_ordering
            ON leaderboard_applied_events (subject_id, epoch, applied_at ASC, stream_offset ASC, event_id ASC)
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS leaderboard_checkpoints (
                scope_key TEXT PRIMARY KEY,
                committed_offset INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS leaderboard_processed_offsets (
                stream_offset INTEGER PRIMARY KEY,
                processed_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS leaderboard_reconciliation_audits (
                audit_id TEXT PRIMARY KEY,
                scope_type TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                epoch TEXT NOT NULL,
                audited_at TEXT NOT NULL,
                canonical_count INTEGER NOT NULL,
                projection_count INTEGER NOT NULL,
                divergence_count INTEGER NOT NULL,
                inserted_count INTEGER NOT NULL,
                updated_count INTEGER NOT NULL,
                deleted_count INTEGER NOT NULL,
                corrected_subjects_json TEXT NOT NULL,
                duration_ms REAL NOT NULL,
                pinned_offset INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS leaderboard_cohort_definitions (
                cohort_id TEXT PRIMARY KEY,
                strategy TEXT NOT NULL,
                epoch TEXT NOT NULL,
                min_size INTEGER NOT NULL DEFAULT 500,
                max_size INTEGER NOT NULL DEFAULT 5000,
                status TEXT NOT NULL DEFAULT 'forming'
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS leaderboard_completion_times (
                completion_id TEXT NOT NULL,
                learner_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                PRIMARY KEY (completion_id, learner_id)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS leaderboard_pending_awards (
                event_id TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                completion_id TEXT NOT NULL,
                xp_amount INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending_time',
                watermark_offset INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (event_id, subject_id)
            )
            """
        )
        self.connection.commit()

    @contextmanager
    def transaction(self):
        """Context manager for executing operations within a single atomic transaction.

        Rolls back all changes if an exception is raised. Supports reentrant / nested calls.
        """
        self._transaction_depth += 1
        try:
            yield self.connection.cursor()
            if self._transaction_depth == 1:
                self.connection.commit()
        except Exception:
            if self._transaction_depth == 1:
                self.connection.rollback()
            raise
        finally:
            self._transaction_depth -= 1

    def _migrate_schema(self) -> None:
        """Safely migrates schema to compound primary keys and processed_offsets table."""
        cursor = self.connection.cursor()
        cursor.execute("PRAGMA table_info(leaderboard_applied_events)")
        cols = cursor.fetchall()
        if cols:
            pk_cols = {col[1] for col in cols if col[5] > 0}
            if pk_cols == {"event_id"}:
                cursor.execute(
                    """
                    CREATE TABLE leaderboard_applied_events_v2 (
                        event_id TEXT NOT NULL,
                        subject_id TEXT NOT NULL,
                        epoch TEXT NOT NULL,
                        xp_amount INTEGER NOT NULL,
                        applied_at TEXT NOT NULL,
                        stream_offset INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (event_id, subject_id)
                    )
                    """
                )
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO leaderboard_applied_events_v2 (
                        event_id, subject_id, epoch, xp_amount, applied_at, stream_offset
                    )
                    SELECT event_id, subject_id, epoch, xp_amount, applied_at, stream_offset
                    FROM leaderboard_applied_events
                    """
                )
                cursor.execute("DROP TABLE leaderboard_applied_events")
                cursor.execute(
                    "ALTER TABLE leaderboard_applied_events_v2 RENAME TO leaderboard_applied_events"
                )
                self.connection.commit()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS leaderboard_processed_offsets (
                stream_offset INTEGER PRIMARY KEY,
                processed_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='leaderboard_applied_events'"
        )
        if cursor.fetchone():
            cursor.execute(
                """
                INSERT OR IGNORE INTO leaderboard_processed_offsets (stream_offset, processed_at)
                SELECT stream_offset, applied_at
                FROM leaderboard_applied_events
                WHERE stream_offset > 0
                """
            )
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='leaderboard_checkpoints'"
        )
        if cursor.fetchone():
            cursor.execute(
                "SELECT committed_offset FROM leaderboard_checkpoints WHERE scope_key = 'global'"
            )
            row = cursor.fetchone()
            if row and row[0] > 0:
                for off in range(1, row[0] + 1):
                    cursor.execute(
                        "INSERT OR IGNORE INTO leaderboard_processed_offsets (stream_offset, processed_at) VALUES (?, '')",
                        (off,),
                    )
        cursor.execute("PRAGMA table_info(leaderboard_reconciliation_audits)")
        audit_cols = [c[1] for c in cursor.fetchall()]
        if audit_cols and "pinned_offset" not in audit_cols:
            cursor.execute(
                "ALTER TABLE leaderboard_reconciliation_audits ADD COLUMN pinned_offset INTEGER NOT NULL DEFAULT 0"
            )
        self.connection.commit()

    def apply_award_atomic(
        self,
        event_id: str,
        subject_id: str,
        epoch: str,
        xp_amount: int,
        occurred_at: str,
        global_score: int | None = None,
        global_first_reached_at: str | None = None,
        cohort_id: str | None = None,
        cohort_score: int | None = None,
        cohort_first_reached_at: str | None = None,
        watermark_offset: int = 0,
    ) -> bool:
        with self.transaction():
            cursor = self.connection.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO leaderboard_applied_events (
                        event_id, subject_id, epoch, xp_amount, applied_at, stream_offset
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (event_id, subject_id, epoch, xp_amount, occurred_at, watermark_offset),
                )
            except sqlite3.IntegrityError:
                # Duplicate event delivery -> authoritative rejection
                if watermark_offset > 0:
                    self.record_processed_offset(
                        watermark_offset, cohort_id=cohort_id, occurred_at=occurred_at
                    )
                return False

            # Recompute total score and chronological first_reached_at from store's applied events
            cursor.execute(
                """
                SELECT applied_at, xp_amount, stream_offset, event_id
                FROM leaderboard_applied_events
                WHERE subject_id = ? AND epoch = ?
                ORDER BY applied_at ASC, stream_offset ASC, event_id ASC
                """,
                (subject_id, epoch),
            )
            events = cursor.fetchall()

            total_score = sum(r[1] for r in events)
            running_sum = 0
            computed_first_reached = occurred_at
            for ev_applied_at, ev_xp, _, _ in events:
                running_sum += ev_xp
                if running_sum >= total_score:
                    computed_first_reached = ev_applied_at
                    break

            # 1. Update Global Leaderboard: Increment in SQL inside the transaction
            cursor.execute(
                """
                INSERT INTO leaderboard_entries (
                    scope_type, scope_id, epoch, subject_id, score, first_reached_at, last_updated_at, watermark_offset
                ) VALUES ('global', 'global', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope_type, scope_id, epoch, subject_id) DO UPDATE SET
                    score = leaderboard_entries.score + ?,
                    first_reached_at = ?,
                    last_updated_at = ?,
                    watermark_offset = MAX(leaderboard_entries.watermark_offset, ?)
                """,
                (
                    epoch,
                    subject_id,
                    total_score,
                    computed_first_reached,
                    occurred_at,
                    watermark_offset,
                    xp_amount,
                    computed_first_reached,
                    occurred_at,
                    watermark_offset,
                ),
            )

            # Failure injection hook for testing atomicity and rollback
            if getattr(self, "_fail_after_global_write", False):
                raise RuntimeError("Simulated failure between global and cohort writes")

            # 2. Update Cohort Leaderboard if applicable: Increment in SQL inside the transaction
            if cohort_id:
                cursor.execute(
                    """
                    INSERT INTO leaderboard_entries (
                        scope_type, scope_id, epoch, subject_id, score, first_reached_at, last_updated_at, watermark_offset
                    ) VALUES ('cohort', ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(scope_type, scope_id, epoch, subject_id) DO UPDATE SET
                        score = leaderboard_entries.score + ?,
                        first_reached_at = ?,
                        last_updated_at = ?,
                        watermark_offset = MAX(leaderboard_entries.watermark_offset, ?)
                    """,
                    (
                        cohort_id,
                        epoch,
                        subject_id,
                        total_score,
                        computed_first_reached,
                        occurred_at,
                        watermark_offset,
                        xp_amount,
                        computed_first_reached,
                        occurred_at,
                        watermark_offset,
                    ),
                )

            # 3. Update Contiguous Committed Checkpoints via processed_offsets
            if watermark_offset > 0:
                self.record_processed_offset(
                    watermark_offset, cohort_id=cohort_id, occurred_at=occurred_at
                )

            return True

    def is_event_applied(self, event_id: str, subject_id: str | None = None) -> bool:
        cursor = self.connection.cursor()
        if subject_id is not None:
            cursor.execute(
                "SELECT 1 FROM leaderboard_applied_events WHERE event_id = ? AND subject_id = ?",
                (event_id, subject_id),
            )
        else:
            cursor.execute(
                "SELECT 1 FROM leaderboard_applied_events WHERE event_id = ?",
                (event_id,),
            )
        return cursor.fetchone() is not None

    def is_offset_processed(self, offset: int) -> bool:
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT 1 FROM leaderboard_processed_offsets WHERE stream_offset = ?",
            (offset,),
        )
        return cursor.fetchone() is not None

    def record_processed_offset(
        self, offset: int, cohort_id: str | None = None, occurred_at: str = ""
    ) -> int:
        with self.transaction():
            cursor = self.connection.cursor()
            if offset > 0:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO leaderboard_processed_offsets (stream_offset, processed_at)
                    VALUES (?, ?)
                    """,
                    (offset, occurred_at),
                )

            cursor.execute(
                "SELECT committed_offset FROM leaderboard_checkpoints WHERE scope_key = 'global'"
            )
            row = cursor.fetchone()
            current_committed = row[0] if row else 0

            cursor.execute(
                """
                SELECT stream_offset FROM leaderboard_processed_offsets
                WHERE stream_offset > ?
                ORDER BY stream_offset ASC
                """,
                (current_committed,),
            )
            for (off,) in cursor.fetchall():
                if off == current_committed + 1:
                    current_committed = off
                else:
                    break

            cursor.execute(
                """
                INSERT INTO leaderboard_checkpoints (scope_key, committed_offset, updated_at)
                VALUES ('global', ?, ?)
                ON CONFLICT(scope_key) DO UPDATE SET
                    committed_offset = excluded.committed_offset,
                    updated_at = excluded.updated_at
                """,
                (current_committed, occurred_at),
            )
            if cohort_id:
                cursor.execute(
                    """
                    INSERT INTO leaderboard_checkpoints (scope_key, committed_offset, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(scope_key) DO UPDATE SET
                        committed_offset = excluded.committed_offset,
                        updated_at = excluded.updated_at
                    """,
                    (f"cohort:{cohort_id}", current_committed, occurred_at),
                )
            return current_committed

    def get_committed_offset(self, scope_key: str = "global") -> int:
        cursor = self.connection.cursor()
        cursor.execute("SELECT committed_offset FROM leaderboard_checkpoints WHERE scope_key = ?", (scope_key,))
        row = cursor.fetchone()
        return row[0] if row else 0

    def set_committed_offset(
        self, scope_key: str, offset: int, updated_at: str = ""
    ) -> None:
        cursor = self.connection.cursor()
        cursor.execute(
            """
            INSERT INTO leaderboard_checkpoints (scope_key, committed_offset, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(scope_key) DO UPDATE SET
                committed_offset = excluded.committed_offset,
                updated_at = excluded.updated_at
            """,
            (scope_key, offset, updated_at),
        )
        if self._transaction_depth == 0:
            self.connection.commit()

    def record_reconciliation_audit(self, audit: ReconciliationAudit) -> None:
        cursor = self.connection.cursor()
        audit_id = audit.audit_id or f"audit_{int(time.time() * 1000)}"
        cursor.execute(
            """
            INSERT INTO leaderboard_reconciliation_audits (
                audit_id, scope_type, scope_id, epoch, audited_at,
                canonical_count, projection_count, divergence_count,
                inserted_count, updated_count, deleted_count,
                corrected_subjects_json, duration_ms, pinned_offset
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                audit.scope_type,
                audit.scope_id,
                audit.epoch,
                audit.audited_at,
                audit.canonical_count,
                audit.projection_count,
                audit.divergence_count,
                audit.inserted_count,
                audit.updated_count,
                audit.deleted_count,
                json.dumps(list(audit.corrected_subject_ids)),
                audit.duration_ms,
                audit.pinned_offset,
            ),
        )
        if self._transaction_depth == 0:
            self.connection.commit()

    def get_reconciliation_audits(self) -> list[ReconciliationAudit]:
        cursor = self.connection.cursor()
        cursor.execute(
            """
            SELECT audit_id, scope_type, scope_id, epoch, audited_at,
                   canonical_count, projection_count, divergence_count,
                   inserted_count, updated_count, deleted_count,
                   corrected_subjects_json, duration_ms, pinned_offset
            FROM leaderboard_reconciliation_audits
            ORDER BY audited_at ASC, audit_id ASC
            """
        )
        return [
            ReconciliationAudit(
                audit_id=r[0],
                scope_type=r[1],
                scope_id=r[2],
                epoch=r[3],
                audited_at=r[4],
                canonical_count=r[5],
                projection_count=r[6],
                divergence_count=r[7],
                inserted_count=r[8],
                updated_count=r[9],
                deleted_count=r[10],
                corrected_subject_ids=tuple(json.loads(r[11])),
                duration_ms=r[12],
                pinned_offset=r[13] if len(r) > 13 else 0,
            )
            for r in cursor.fetchall()
        ]

    def get_epoch_entries(
        self, scope_type: str, epoch: str
    ) -> list[LeaderboardEntry]:
        cursor = self.connection.cursor()
        cursor.execute(
            """
            SELECT scope_type, scope_id, epoch, subject_id, score, first_reached_at, last_updated_at, watermark_offset
            FROM leaderboard_entries
            WHERE scope_type = ? AND epoch = ?
            """,
            (scope_type, epoch),
        )
        return [
            LeaderboardEntry(
                scope_type=r[0],
                scope_id=r[1],
                epoch=r[2],
                subject_id=r[3],
                score=r[4],
                first_reached_at=r[5],
                last_updated_at=r[6],
                watermark_offset=r[7],
            )
            for r in cursor.fetchall()
        ]

    def get_member_cohort(self, epoch: str, subject_id: str) -> str | None:
        cursor = self.connection.cursor()
        cursor.execute(
            """
            SELECT scope_id FROM leaderboard_entries
            WHERE scope_type = 'cohort' AND epoch = ? AND subject_id = ?
            """,
            (epoch, subject_id),
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def get_member_cohorts(self, epoch: str, subject_id: str) -> list[str]:
        cursor = self.connection.cursor()
        cursor.execute(
            """
            SELECT scope_id FROM leaderboard_entries
            WHERE scope_type = 'cohort' AND epoch = ? AND subject_id = ?
            ORDER BY scope_id ASC
            """,
            (epoch, subject_id),
        )
        return [r[0] for r in cursor.fetchall()]

    def remove_cohort_memberships(
        self, epoch: str, subject_id: str, except_cohort_id: str | None = None
    ) -> int:
        cursor = self.connection.cursor()
        if except_cohort_id is not None:
            cursor.execute(
                """
                DELETE FROM leaderboard_entries
                WHERE scope_type = 'cohort' AND epoch = ? AND subject_id = ? AND scope_id != ?
                """,
                (epoch, subject_id, except_cohort_id),
            )
        else:
            cursor.execute(
                """
                DELETE FROM leaderboard_entries
                WHERE scope_type = 'cohort' AND epoch = ? AND subject_id = ?
                """,
                (epoch, subject_id),
            )
        count = cursor.rowcount
        if self._transaction_depth == 0:
            self.connection.commit()
        return count

    def save_cohort_definition(self, cohort: CohortDefinition) -> None:
        cursor = self.connection.cursor()
        cursor.execute(
            """
            INSERT INTO leaderboard_cohort_definitions (
                cohort_id, strategy, epoch, min_size, max_size, status
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(cohort_id) DO UPDATE SET
                strategy = excluded.strategy,
                epoch = excluded.epoch,
                min_size = excluded.min_size,
                max_size = excluded.max_size,
                status = excluded.status
            """,
            (
                cohort.cohort_id,
                cohort.strategy,
                cohort.epoch,
                cohort.min_size,
                cohort.max_size,
                cohort.status,
            ),
        )
        if self._transaction_depth == 0:
            self.connection.commit()

    def get_cohort_definition(self, cohort_id: str) -> CohortDefinition | None:
        cursor = self.connection.cursor()
        cursor.execute(
            """
            SELECT cohort_id, strategy, epoch, min_size, max_size, status
            FROM leaderboard_cohort_definitions
            WHERE cohort_id = ?
            """,
            (cohort_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return CohortDefinition(
            cohort_id=row[0],
            strategy=row[1],
            epoch=row[2],
            min_size=row[3],
            max_size=row[4],
            status=row[5],
        )

    def get_cohort_member_count(self, cohort_id: str, epoch: str) -> int:
        cursor = self.connection.cursor()
        cursor.execute(
            """
            SELECT COUNT(DISTINCT subject_id) FROM leaderboard_entries
            WHERE scope_type = 'cohort' AND scope_id = ? AND epoch = ?
            """,
            (cohort_id, epoch),
        )
        row = cursor.fetchone()
        return row[0] if row else 0

    def get_applied_events(
        self, subject_id: str | None = None, epoch: str | None = None
    ) -> list[dict[str, Any]]:
        cursor = self.connection.cursor()
        query = (
            "SELECT event_id, subject_id, epoch, xp_amount, applied_at, stream_offset "
            "FROM leaderboard_applied_events WHERE 1=1"
        )
        params: list[Any] = []
        if subject_id is not None:
            query += " AND subject_id = ?"
            params.append(subject_id)
        if epoch is not None:
            query += " AND epoch = ?"
            params.append(epoch)
        query += " ORDER BY applied_at ASC, stream_offset ASC, event_id ASC"
        cursor.execute(query, tuple(params))
        return [
            {
                "event_id": r[0],
                "subject_id": r[1],
                "epoch": r[2],
                "xp_amount": r[3],
                "applied_at": r[4],
                "stream_offset": r[5],
            }
            for r in cursor.fetchall()
        ]

    def upsert_score(
        self,
        scope_type: str,
        scope_id: str,
        epoch: str,
        subject_id: str,
        score: int,
        first_reached_at: str,
        updated_at: str,
        watermark_offset: int = 0,
    ) -> bool:
        cursor = self.connection.cursor()
        cursor.execute(
            """
            INSERT INTO leaderboard_entries (
                scope_type, scope_id, epoch, subject_id, score, first_reached_at, last_updated_at, watermark_offset
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope_type, scope_id, epoch, subject_id) DO UPDATE SET
                score = excluded.score,
                first_reached_at = excluded.first_reached_at,
                last_updated_at = excluded.last_updated_at,
                watermark_offset = MAX(leaderboard_entries.watermark_offset, excluded.watermark_offset)
            """,
            (scope_type, scope_id, epoch, subject_id, score, first_reached_at, updated_at, watermark_offset),
        )
        if self._transaction_depth == 0:
            self.connection.commit()
        return True

    def remove_member(
        self, scope_type: str, scope_id: str, epoch: str, subject_id: str
    ) -> bool:
        cursor = self.connection.cursor()
        cursor.execute(
            """
            DELETE FROM leaderboard_entries
            WHERE scope_type = ? AND scope_id = ? AND epoch = ? AND subject_id = ?
            """,
            (scope_type, scope_id, epoch, subject_id),
        )
        if self._transaction_depth == 0:
            self.connection.commit()
        return cursor.rowcount > 0

    def get_entry(
        self, scope_type: str, scope_id: str, epoch: str, subject_id: str
    ) -> LeaderboardEntry | None:
        cursor = self.connection.cursor()
        cursor.execute(
            """
            SELECT scope_type, scope_id, epoch, subject_id, score, first_reached_at, last_updated_at, watermark_offset
            FROM leaderboard_entries
            WHERE scope_type = ? AND scope_id = ? AND epoch = ? AND subject_id = ?
            """,
            (scope_type, scope_id, epoch, subject_id),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return LeaderboardEntry(
            scope_type=row[0],
            scope_id=row[1],
            epoch=row[2],
            subject_id=row[3],
            score=row[4],
            first_reached_at=row[5],
            last_updated_at=row[6],
            watermark_offset=row[7],
        )

    def get_rank(
        self, scope_type: str, scope_id: str, epoch: str, subject_id: str
    ) -> RankedLearner | None:
        entry = self.get_entry(scope_type, scope_id, epoch, subject_id)
        if not entry:
            return None

        # Tie-break logic: score DESC, first_reached_at ASC, subject_id ASC
        cursor = self.connection.cursor()
        cursor.execute(
            """
            SELECT COUNT(*) + 1
            FROM leaderboard_entries
            WHERE scope_type = ? AND scope_id = ? AND epoch = ?
              AND (
                  score > ?
                  OR (score = ? AND first_reached_at < ?)
                  OR (score = ? AND first_reached_at = ? AND subject_id < ?)
              )
            """,
            (
                scope_type,
                scope_id,
                epoch,
                entry.score,
                entry.score,
                entry.first_reached_at,
                entry.score,
                entry.first_reached_at,
                entry.subject_id,
            ),
        )
        rank = cursor.fetchone()[0]
        return RankedLearner(
            rank=rank,
            subject_id=entry.subject_id,
            score=entry.score,
            first_reached_at=entry.first_reached_at,
            scope_type=scope_type,  # type: ignore
            scope_id=scope_id,
            epoch=epoch,
        )

    def get_top_k(
        self, scope_type: str, scope_id: str, epoch: str, k: int = 10
    ) -> list[RankedLearner]:
        cursor = self.connection.cursor()
        cursor.execute(
            """
            SELECT subject_id, score, first_reached_at
            FROM leaderboard_entries
            WHERE scope_type = ? AND scope_id = ? AND epoch = ?
            ORDER BY score DESC, first_reached_at ASC, subject_id ASC
            LIMIT ?
            """,
            (scope_type, scope_id, epoch, k),
        )
        rows = cursor.fetchall()
        return [
            RankedLearner(
                rank=idx + 1,
                subject_id=row[0],
                score=row[1],
                first_reached_at=row[2],
                scope_type=scope_type,  # type: ignore
                scope_id=scope_id,
                epoch=epoch,
            )
            for idx, row in enumerate(rows)
        ]

    def get_surrounding(
        self,
        scope_type: str,
        scope_id: str,
        epoch: str,
        subject_id: str,
        window: int = 5,
    ) -> list[RankedLearner]:
        target = self.get_rank(scope_type, scope_id, epoch, subject_id)
        if not target:
            return []

        cursor = self.connection.cursor()
        # Query window using CTE with ROW_NUMBER()
        cursor.execute(
            """
            WITH ranked AS (
                SELECT
                    ROW_NUMBER() OVER (
                        ORDER BY score DESC, first_reached_at ASC, subject_id ASC
                    ) AS r,
                    subject_id,
                    score,
                    first_reached_at
                FROM leaderboard_entries
                WHERE scope_type = ? AND scope_id = ? AND epoch = ?
            )
            SELECT r, subject_id, score, first_reached_at
            FROM ranked
            WHERE r BETWEEN ? AND ?
            ORDER BY r ASC
            """,
            (
                scope_type,
                scope_id,
                epoch,
                max(1, target.rank - window),
                target.rank + window,
            ),
        )
        rows = cursor.fetchall()
        return [
            RankedLearner(
                rank=row[0],
                subject_id=row[1],
                score=row[2],
                first_reached_at=row[3],
                scope_type=scope_type,  # type: ignore
                scope_id=scope_id,
                epoch=epoch,
            )
            for row in rows
        ]

    def get_all_entries(
        self, scope_type: str, scope_id: str, epoch: str
    ) -> list[LeaderboardEntry]:
        cursor = self.connection.cursor()
        cursor.execute(
            """
            SELECT scope_type, scope_id, epoch, subject_id, score, first_reached_at, last_updated_at, watermark_offset
            FROM leaderboard_entries
            WHERE scope_type = ? AND scope_id = ? AND epoch = ?
            ORDER BY score DESC, first_reached_at ASC, subject_id ASC
            """,
            (scope_type, scope_id, epoch),
        )
        return [
            LeaderboardEntry(
                scope_type=r[0],
                scope_id=r[1],
                epoch=r[2],
                subject_id=r[3],
                score=r[4],
                first_reached_at=r[5],
                last_updated_at=r[6],
                watermark_offset=r[7],
            )
            for r in cursor.fetchall()
        ]

    def get_watermark(self, scope_type: str, scope_id: str, epoch: str) -> int:
        cursor = self.connection.cursor()
        scope_key = "global" if scope_type == "global" else f"cohort:{scope_id}"
        cursor.execute(
            "SELECT committed_offset FROM leaderboard_checkpoints WHERE scope_key = ?",
            (scope_key,),
        )
        row = cursor.fetchone()
        if row and row[0] is not None:
            return row[0]
        return 0

    def clear_derived(self) -> None:
        """Clears derived tables while preserving leaderboard_reconciliation_audits."""
        cursor = self.connection.cursor()
        cursor.execute("DELETE FROM leaderboard_entries")
        cursor.execute("DELETE FROM leaderboard_applied_events")
        cursor.execute("DELETE FROM leaderboard_checkpoints")
        cursor.execute("DELETE FROM leaderboard_processed_offsets")
        cursor.execute("DELETE FROM leaderboard_completion_times")
        cursor.execute("DELETE FROM leaderboard_pending_awards")
        if self._transaction_depth == 0:
            self.connection.commit()

    def wipe_all_for_tests(self) -> None:
        """Full wipe of all tables including reconciliation audits; tests only."""
        with self.transaction():
            self.clear_derived()
            cursor = self.connection.cursor()
            cursor.execute("DELETE FROM leaderboard_reconciliation_audits")

    def clear(
        self,
        scope_type: str | None = None,
        scope_id: str | None = None,
        epoch: str | None = None,
    ) -> None:
        cursor = self.connection.cursor()
        conditions: list[str] = []
        params: list[str] = []
        if scope_type is not None:
            conditions.append("scope_type = ?")
            params.append(scope_type)
        if scope_id is not None:
            conditions.append("scope_id = ?")
            params.append(scope_id)
        if epoch is not None:
            conditions.append("epoch = ?")
            params.append(epoch)

        if conditions:
            where = " WHERE " + " AND ".join(conditions)
            cursor.execute("DELETE FROM leaderboard_entries" + where, params)
            if self._transaction_depth == 0:
                self.connection.commit()
        else:
            self.clear_derived()

    def record_completion_time(
        self, completion_id: str, learner_id: str, occurred_at: str
    ) -> None:
        cursor = self.connection.cursor()
        cursor.execute(
            """
            INSERT INTO leaderboard_completion_times (completion_id, learner_id, occurred_at)
            VALUES (?, ?, ?)
            ON CONFLICT(completion_id, learner_id) DO UPDATE SET
                occurred_at = excluded.occurred_at
            """,
            (completion_id, learner_id, occurred_at),
        )
        if self._transaction_depth == 0:
            self.connection.commit()

    def get_completion_time(
        self, completion_id: str, learner_id: str | None = None
    ) -> str | None:
        cursor = self.connection.cursor()
        if learner_id is not None:
            cursor.execute(
                "SELECT occurred_at FROM leaderboard_completion_times WHERE completion_id = ? AND learner_id = ?",
                (completion_id, learner_id),
            )
        else:
            cursor.execute(
                "SELECT occurred_at FROM leaderboard_completion_times WHERE completion_id = ?",
                (completion_id,),
            )
        row = cursor.fetchone()
        return row[0] if row else None

    def record_pending_award(
        self,
        event_id: str,
        subject_id: str,
        completion_id: str,
        xp_amount: int,
        status: str = "pending_time",
        watermark_offset: int = 0,
    ) -> None:
        cursor = self.connection.cursor()
        cursor.execute(
            """
            INSERT INTO leaderboard_pending_awards (
                event_id, subject_id, completion_id, xp_amount, status, watermark_offset
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id, subject_id) DO UPDATE SET
                completion_id = excluded.completion_id,
                xp_amount = excluded.xp_amount,
                status = excluded.status,
                watermark_offset = excluded.watermark_offset
            """,
            (event_id, subject_id, completion_id, xp_amount, status, watermark_offset),
        )
        if self._transaction_depth == 0:
            self.connection.commit()

    def get_pending_awards(
        self, subject_id: str | None = None
    ) -> list[dict[str, Any]]:
        cursor = self.connection.cursor()
        if subject_id is not None:
            cursor.execute(
                """
                SELECT event_id, subject_id, completion_id, xp_amount, status, watermark_offset
                FROM leaderboard_pending_awards WHERE subject_id = ?
                """,
                (subject_id,),
            )
        else:
            cursor.execute(
                """
                SELECT event_id, subject_id, completion_id, xp_amount, status, watermark_offset
                FROM leaderboard_pending_awards
                """
            )
        return [
            {
                "event_id": r[0],
                "subject_id": r[1],
                "completion_id": r[2],
                "xp_amount": r[3],
                "status": r[4],
                "watermark_offset": r[5],
            }
            for r in cursor.fetchall()
        ]

    def delete_pending_award(self, event_id: str, subject_id: str) -> bool:
        cursor = self.connection.cursor()
        cursor.execute(
            "DELETE FROM leaderboard_pending_awards WHERE event_id = ? AND subject_id = ?",
            (event_id, subject_id),
        )
        if self._transaction_depth == 0:
            self.connection.commit()
        return cursor.rowcount > 0

    def get_all_epochs(self) -> list[str]:
        cursor = self.connection.cursor()
        cursor.execute(
            """
            SELECT DISTINCT epoch FROM leaderboard_entries
            UNION
            SELECT DISTINCT epoch FROM leaderboard_applied_events
            """
        )
        return sorted([r[0] for r in cursor.fetchall() if r[0]])

    def close(self) -> None:
        self.connection.close()


class InMemoryLeaderboardStore(LeaderboardStore):
    """In-memory leaderboard store for zero-overhead unit testing and fast benchmarks."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str, str, str], LeaderboardEntry] = {}
        self._applied_events: set[Any] = set()
        self._applied_events_list: list[dict[str, Any]] = []
        self._processed_offsets: set[int] = set()
        self._checkpoints: dict[str, int] = {}
        self._reconciliation_audits: list[ReconciliationAudit] = []
        self._completion_times: dict[tuple[str, str], str] = {}
        self._pending_awards: list[dict[str, Any]] = []
        self._cohort_definitions: dict[str, CohortDefinition] = {}
        self._transaction_depth = 0

    @contextmanager
    def transaction(self):
        """Context manager for executing operations within a single atomic transaction.

        Rolls back all state changes if an exception is raised.
        """
        self._transaction_depth += 1
        snapshot = None
        if self._transaction_depth == 1:
            snapshot = (
                dict(self._entries),
                set(self._applied_events),
                [dict(d) for d in self._applied_events_list],
                set(self._processed_offsets),
                dict(self._checkpoints),
                list(self._reconciliation_audits),
                dict(self._completion_times),
                [dict(d) for d in self._pending_awards],
                dict(self._cohort_definitions),
            )
        try:
            yield
        except Exception:
            if snapshot is not None:
                (
                    self._entries,
                    self._applied_events,
                    self._applied_events_list,
                    self._processed_offsets,
                    self._checkpoints,
                    self._reconciliation_audits,
                    self._completion_times,
                    self._pending_awards,
                    self._cohort_definitions,
                ) = snapshot
            raise
        finally:
            self._transaction_depth -= 1

    def apply_award_atomic(
        self,
        event_id: str,
        subject_id: str,
        epoch: str,
        xp_amount: int,
        occurred_at: str,
        global_score: int | None = None,
        global_first_reached_at: str | None = None,
        cohort_id: str | None = None,
        cohort_score: int | None = None,
        cohort_first_reached_at: str | None = None,
        watermark_offset: int = 0,
    ) -> bool:
        with self.transaction():
            if (event_id, subject_id) in self._applied_events or event_id in self._applied_events:
                if watermark_offset > 0:
                    self.record_processed_offset(
                        watermark_offset, cohort_id=cohort_id, occurred_at=occurred_at
                    )
                return False
            self._applied_events.add((event_id, subject_id))
            self._applied_events.add(event_id)
            self._applied_events_list.append(
                {
                    "event_id": event_id,
                    "subject_id": subject_id,
                    "epoch": epoch,
                    "xp_amount": xp_amount,
                    "applied_at": occurred_at,
                    "stream_offset": watermark_offset,
                }
            )

            # Recompute first_reached_at and total score from store's applied events
            learner_events = [
                e for e in self._applied_events_list
                if e["subject_id"] == subject_id and e["epoch"] == epoch
            ]
            learner_events.sort(
                key=lambda e: (e["applied_at"], e.get("stream_offset", 0), e.get("event_id", ""))
            )

            total_score = sum(e["xp_amount"] for e in learner_events)
            running_sum = 0
            computed_first_reached = occurred_at
            for e in learner_events:
                running_sum += e["xp_amount"]
                if running_sum >= total_score:
                    computed_first_reached = e["applied_at"]
                    break

            # 1. Update Global Leaderboard
            self.upsert_score(
                "global",
                "global",
                epoch,
                subject_id,
                total_score,
                computed_first_reached,
                occurred_at,
                watermark_offset,
            )

            # Failure hook for testing atomicity and rollback
            if getattr(self, "_fail_after_global_write", False):
                raise RuntimeError("Simulated failure between global and cohort writes")

            # 2. Update Cohort Leaderboard if applicable
            if cohort_id:
                self.upsert_score(
                    "cohort",
                    cohort_id,
                    epoch,
                    subject_id,
                    total_score,
                    computed_first_reached,
                    occurred_at,
                    watermark_offset,
                )

            # 3. Update Contiguous Committed Checkpoints via processed_offsets
            if watermark_offset > 0:
                self.record_processed_offset(
                    watermark_offset, cohort_id=cohort_id, occurred_at=occurred_at
                )
            return True

    def is_event_applied(self, event_id: str, subject_id: str | None = None) -> bool:
        if subject_id is not None and (event_id, subject_id) in self._applied_events:
            return True
        return event_id in self._applied_events

    def is_offset_processed(self, offset: int) -> bool:
        return offset in self._processed_offsets

    def record_processed_offset(
        self, offset: int, cohort_id: str | None = None, occurred_at: str = ""
    ) -> int:
        with self.transaction():
            if offset > 0:
                self._processed_offsets.add(offset)

            current = self._checkpoints.get("global", 0)
            while (current + 1) in self._processed_offsets:
                current += 1

            self._checkpoints["global"] = current
            if cohort_id:
                self._checkpoints[f"cohort:{cohort_id}"] = current
            return current

    def get_committed_offset(self, scope_key: str = "global") -> int:
        return self._checkpoints.get(scope_key, 0)

    def set_committed_offset(
        self, scope_key: str, offset: int, updated_at: str = ""
    ) -> None:
        self._checkpoints[scope_key] = offset

    def record_reconciliation_audit(self, audit: ReconciliationAudit) -> None:
        self._reconciliation_audits.append(audit)

    def get_reconciliation_audits(self) -> list[ReconciliationAudit]:
        return list(self._reconciliation_audits)

    def get_epoch_entries(
        self, scope_type: str, epoch: str
    ) -> list[LeaderboardEntry]:
        return [
            e
            for (st, _, ep, _), e in self._entries.items()
            if st == scope_type and ep == epoch
        ]

    def get_member_cohort(self, epoch: str, subject_id: str) -> str | None:
        for (st, si, ep, sub), _ in self._entries.items():
            if st == "cohort" and ep == epoch and sub == subject_id:
                return si
        return None

    def get_member_cohorts(self, epoch: str, subject_id: str) -> list[str]:
        res = [
            si
            for (st, si, ep, sub) in self._entries
            if st == "cohort" and ep == epoch and sub == subject_id
        ]
        return sorted(res)

    def remove_cohort_memberships(
        self, epoch: str, subject_id: str, except_cohort_id: str | None = None
    ) -> int:
        keys_to_remove = [
            k for k in self._entries
            if k[0] == "cohort" and k[2] == epoch and k[3] == subject_id
            and (except_cohort_id is None or k[1] != except_cohort_id)
        ]
        for k in keys_to_remove:
            self._entries.pop(k, None)
        return len(keys_to_remove)

    def save_cohort_definition(self, cohort: CohortDefinition) -> None:
        self._cohort_definitions[cohort.cohort_id] = cohort

    def get_cohort_definition(self, cohort_id: str) -> CohortDefinition | None:
        return self._cohort_definitions.get(cohort_id)

    def get_cohort_member_count(self, cohort_id: str, epoch: str) -> int:
        members = {
            sub
            for (st, si, ep, sub) in self._entries
            if st == "cohort" and si == cohort_id and ep == epoch
        }
        return len(members)

    def get_applied_events(
        self, subject_id: str | None = None, epoch: str | None = None
    ) -> list[dict[str, Any]]:
        res = list(self._applied_events_list)
        if subject_id is not None:
            res = [e for e in res if e["subject_id"] == subject_id]
        if epoch is not None:
            res = [e for e in res if e["epoch"] == epoch]
        return sorted(
            res,
            key=lambda e: (e["applied_at"], e.get("stream_offset", 0), e.get("event_id", "")),
        )

    def upsert_score(
        self,
        scope_type: str,
        scope_id: str,
        epoch: str,
        subject_id: str,
        score: int,
        first_reached_at: str,
        updated_at: str,
        watermark_offset: int = 0,
    ) -> bool:
        key = (scope_type, scope_id, epoch, subject_id)
        prior = self._entries.get(key)
        watermark = max(prior.watermark_offset if prior else 0, watermark_offset)
        self._entries[key] = LeaderboardEntry(
            scope_type=scope_type,  # type: ignore
            scope_id=scope_id,
            epoch=epoch,
            subject_id=subject_id,
            score=score,
            first_reached_at=first_reached_at,
            last_updated_at=updated_at,
            watermark_offset=watermark,
        )
        return True

    def remove_member(
        self, scope_type: str, scope_id: str, epoch: str, subject_id: str
    ) -> bool:
        key = (scope_type, scope_id, epoch, subject_id)
        return bool(self._entries.pop(key, None))

    def get_entry(
        self, scope_type: str, scope_id: str, epoch: str, subject_id: str
    ) -> LeaderboardEntry | None:
        return self._entries.get((scope_type, scope_id, epoch, subject_id))

    def _sorted_scope_entries(
        self, scope_type: str, scope_id: str, epoch: str
    ) -> list[LeaderboardEntry]:
        items = [
            e for (st, si, ep, _), e in self._entries.items()
            if st == scope_type and si == scope_id and ep == epoch
        ]
        # Exact tie rule: (-score, first_reached_at, subject_id)
        return sorted(
            items,
            key=lambda e: (-e.score, e.first_reached_at, e.subject_id),
        )

    def get_rank(
        self, scope_type: str, scope_id: str, epoch: str, subject_id: str
    ) -> RankedLearner | None:
        sorted_list = self._sorted_scope_entries(scope_type, scope_id, epoch)
        for idx, entry in enumerate(sorted_list):
            if entry.subject_id == subject_id:
                return RankedLearner(
                    rank=idx + 1,
                    subject_id=entry.subject_id,
                    score=entry.score,
                    first_reached_at=entry.first_reached_at,
                    scope_type=scope_type,  # type: ignore
                    scope_id=scope_id,
                    epoch=epoch,
                )
        return None

    def get_top_k(
        self, scope_type: str, scope_id: str, epoch: str, k: int = 10
    ) -> list[RankedLearner]:
        sorted_list = self._sorted_scope_entries(scope_type, scope_id, epoch)
        return [
            RankedLearner(
                rank=idx + 1,
                subject_id=e.subject_id,
                score=e.score,
                first_reached_at=e.first_reached_at,
                scope_type=scope_type,  # type: ignore
                scope_id=scope_id,
                epoch=epoch,
            )
            for idx, e in enumerate(sorted_list[:k])
        ]

    def get_surrounding(
        self,
        scope_type: str,
        scope_id: str,
        epoch: str,
        subject_id: str,
        window: int = 5,
    ) -> list[RankedLearner]:
        target = self.get_rank(scope_type, scope_id, epoch, subject_id)
        if not target:
            return []
        sorted_list = self._sorted_scope_entries(scope_type, scope_id, epoch)
        start_idx = max(0, target.rank - 1 - window)
        end_idx = min(len(sorted_list), target.rank + window)
        return [
            RankedLearner(
                rank=idx + 1,
                subject_id=e.subject_id,
                score=e.score,
                first_reached_at=e.first_reached_at,
                scope_type=scope_type,  # type: ignore
                scope_id=scope_id,
                epoch=epoch,
            )
            for idx, e in enumerate(sorted_list)
            if start_idx <= idx < end_idx
        ]

    def get_all_entries(
        self, scope_type: str, scope_id: str, epoch: str
    ) -> list[LeaderboardEntry]:
        return self._sorted_scope_entries(scope_type, scope_id, epoch)

    def get_watermark(self, scope_type: str, scope_id: str, epoch: str) -> int:
        scope_key = "global" if scope_type == "global" else f"cohort:{scope_id}"
        return self._checkpoints.get(scope_key, 0)

    def clear_derived(self) -> None:
        """Clears all derived projection read models while preserving reconciliation audits."""
        self._entries.clear()
        self._applied_events.clear()
        self._applied_events_list.clear()
        self._processed_offsets.clear()
        self._checkpoints.clear()
        self._completion_times.clear()
        self._pending_awards.clear()

    def wipe_all_for_tests(self) -> None:
        """Full wipe of all storage state including audits; tests only."""
        self.clear_derived()
        self._reconciliation_audits.clear()
        self._cohort_definitions.clear()

    def clear(
        self,
        scope_type: str | None = None,
        scope_id: str | None = None,
        epoch: str | None = None,
    ) -> None:
        if scope_type is None and scope_id is None and epoch is None:
            self.clear_derived()
            return
        keys_to_remove = [
            k for k in self._entries
            if (scope_type is None or k[0] == scope_type)
            and (scope_id is None or k[1] == scope_id)
            and (epoch is None or k[2] == epoch)
        ]
        for k in keys_to_remove:
            self._entries.pop(k, None)

    def record_completion_time(
        self, completion_id: str, learner_id: str, occurred_at: str
    ) -> None:
        self._completion_times[(completion_id, learner_id)] = occurred_at

    def get_completion_time(
        self, completion_id: str, learner_id: str | None = None
    ) -> str | None:
        if learner_id is not None:
            return self._completion_times.get((completion_id, learner_id))
        for (cid, _), occ in self._completion_times.items():
            if cid == completion_id:
                return occ
        return None

    def record_pending_award(
        self,
        event_id: str,
        subject_id: str,
        completion_id: str,
        xp_amount: int,
        status: str = "pending_time",
        watermark_offset: int = 0,
    ) -> None:
        self.delete_pending_award(event_id, subject_id)
        self._pending_awards.append(
            {
                "event_id": event_id,
                "subject_id": subject_id,
                "completion_id": completion_id,
                "xp_amount": xp_amount,
                "status": status,
                "watermark_offset": watermark_offset,
            }
        )

    def get_pending_awards(
        self, subject_id: str | None = None
    ) -> list[dict[str, Any]]:
        return [
            a
            for a in self._pending_awards
            if subject_id is None or a["subject_id"] == subject_id
        ]

    def delete_pending_award(self, event_id: str, subject_id: str) -> bool:
        orig = len(self._pending_awards)
        self._pending_awards = [
            a
            for a in self._pending_awards
            if not (a["event_id"] == event_id and a["subject_id"] == subject_id)
        ]
        return len(self._pending_awards) < orig

    def get_all_epochs(self) -> list[str]:
        eps = {e.epoch for e in self._entries.values()} | {
            e["epoch"] for e in self._applied_events_list
        }
        return sorted(eps)

    def close(self) -> None:
        self._entries.clear()
