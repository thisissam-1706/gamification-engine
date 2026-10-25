"""SQLite persistence for notifications, outbox dispatches, suppressions, and DLQ."""

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Sequence

from src.notifications.models import (
    ChannelType,
    NotificationCategory,
    NotificationProposal,
    NotificationStatus,
)


class NotificationStore:
    """Manages SQLite storage for notification lifecycle, fatigue tracking, and DLQ."""

    def __init__(self, database: str = ":memory:") -> None:
        self.connection = sqlite3.connect(database)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        cursor = self.connection.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_proposals (
                proposal_id TEXT PRIMARY KEY,
                learner_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                category TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_dispatches (
                dispatch_id TEXT PRIMARY KEY,
                proposal_id TEXT NOT NULL,
                learner_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                category TEXT NOT NULL,
                dispatched_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_dispatches_learner_channel
            ON notification_dispatches (learner_id, channel, dispatched_at)
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_suppressions (
                suppression_id TEXT PRIMARY KEY,
                proposal_id TEXT NOT NULL,
                learner_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                category TEXT NOT NULL,
                suppressed_at TEXT NOT NULL,
                reason TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_dlq (
                dlq_id TEXT PRIMARY KEY,
                proposal_id TEXT NOT NULL,
                learner_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                category TEXT NOT NULL,
                failed_at TEXT NOT NULL,
                reason TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                resolved INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_rescheduled (
                proposal_id TEXT PRIMARY KEY,
                learner_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                category TEXT NOT NULL,
                rescheduled_for TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                released INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self.connection.commit()

    def get_proposal_status(self, proposal_id: str) -> NotificationStatus | None:
        """Returns the current status of a proposal, or None if not found."""
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT status FROM notification_proposals WHERE proposal_id = ?",
            (proposal_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        try:
            return NotificationStatus(row[0])
        except ValueError:
            return None

    def is_proposal_seen(self, proposal_id: str) -> bool:
        """Returns True if the proposal has already been registered with a terminal status or queued."""
        status = self.get_proposal_status(proposal_id)
        if status is None:
            return False
        return status in (
            NotificationStatus.DISPATCHED,
            NotificationStatus.SUPPRESSED,
            NotificationStatus.FAILED,
            NotificationStatus.QUEUED,
        )

    def register_proposal(
        self,
        proposal: NotificationProposal,
        status: NotificationStatus = NotificationStatus.IN_PROGRESS,
        created_at: str | None = None,
    ) -> bool:
        """Registers a proposal. Returns False if already seen (deduplication)."""
        cursor = self.connection.cursor()
        try:
            with self.connection:
                cursor.execute(
                    """
                    INSERT INTO notification_proposals (
                        proposal_id, learner_id, channel, category, created_at, status
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        proposal.proposal_id,
                        proposal.learner_id,
                        proposal.channel.value if isinstance(proposal.channel, ChannelType) else str(proposal.channel),
                        proposal.category.value if isinstance(proposal.category, NotificationCategory) else str(proposal.category),
                        created_at or proposal.created_at or "",
                        status.value if isinstance(status, NotificationStatus) else str(status),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def update_proposal_status(self, proposal_id: str, status: NotificationStatus | str) -> None:
        """Updates the status of an existing proposal atomically."""
        stat_val = status.value if isinstance(status, NotificationStatus) else str(status)
        with self.connection:
            cursor = self.connection.cursor()
            cursor.execute(
                "UPDATE notification_proposals SET status = ? WHERE proposal_id = ?",
                (stat_val, proposal_id),
            )

    def record_dispatch(
        self,
        dispatch_id: str,
        proposal_id: str,
        learner_id: str,
        channel: ChannelType,
        category: NotificationCategory,
        dispatched_at: str,
        payload: Any,
    ) -> None:
        with self.connection:
            cursor = self.connection.cursor()
            cursor.execute(
                """
                INSERT INTO notification_dispatches (
                    dispatch_id, proposal_id, learner_id, channel, category, dispatched_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dispatch_id,
                    proposal_id,
                    learner_id,
                    channel.value if isinstance(channel, ChannelType) else str(channel),
                    category.value if isinstance(category, NotificationCategory) else str(category),
                    dispatched_at,
                    json.dumps(payload),
                ),
            )
            cursor.execute(
                "UPDATE notification_proposals SET status = ? WHERE proposal_id = ?",
                (NotificationStatus.DISPATCHED.value, proposal_id),
            )
            if cursor.rowcount == 0:
                cursor.execute(
                    """
                    INSERT INTO notification_proposals (
                        proposal_id, learner_id, channel, category, created_at, status
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        proposal_id,
                        learner_id,
                        channel.value if isinstance(channel, ChannelType) else str(channel),
                        category.value if isinstance(category, NotificationCategory) else str(category),
                        dispatched_at,
                        NotificationStatus.DISPATCHED.value,
                    ),
                )

    def record_suppression(
        self,
        suppression_id: str,
        proposal_id: str,
        learner_id: str,
        channel: ChannelType,
        category: NotificationCategory,
        suppressed_at: str,
        reason: str,
    ) -> None:
        with self.connection:
            cursor = self.connection.cursor()
            cursor.execute(
                """
                INSERT INTO notification_suppressions (
                    suppression_id, proposal_id, learner_id, channel, category, suppressed_at, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    suppression_id,
                    proposal_id,
                    learner_id,
                    channel.value if isinstance(channel, ChannelType) else str(channel),
                    category.value if isinstance(category, NotificationCategory) else str(category),
                    suppressed_at,
                    reason,
                ),
            )
            cursor.execute(
                "UPDATE notification_proposals SET status = ? WHERE proposal_id = ?",
                (NotificationStatus.SUPPRESSED.value, proposal_id),
            )
            if cursor.rowcount == 0:
                cursor.execute(
                    """
                    INSERT INTO notification_proposals (
                        proposal_id, learner_id, channel, category, created_at, status
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        proposal_id,
                        learner_id,
                        channel.value if isinstance(channel, ChannelType) else str(channel),
                        category.value if isinstance(category, NotificationCategory) else str(category),
                        suppressed_at,
                        NotificationStatus.SUPPRESSED.value,
                    ),
                )

    def record_dlq(
        self,
        dlq_id: str,
        proposal_id: str,
        learner_id: str,
        channel: ChannelType,
        category: NotificationCategory,
        failed_at: str,
        reason: str,
        attempts: int,
        payload: Any,
    ) -> None:
        with self.connection:
            cursor = self.connection.cursor()
            cursor.execute(
                """
                INSERT INTO notification_dlq (
                    dlq_id, proposal_id, learner_id, channel, category, failed_at, reason, attempts, payload_json, resolved
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    dlq_id,
                    proposal_id,
                    learner_id,
                    channel.value if isinstance(channel, ChannelType) else str(channel),
                    category.value if isinstance(category, NotificationCategory) else str(category),
                    failed_at,
                    reason,
                    attempts,
                    json.dumps(payload),
                ),
            )
            cursor.execute(
                "UPDATE notification_proposals SET status = ? WHERE proposal_id = ?",
                (NotificationStatus.FAILED.value, proposal_id),
            )
            if cursor.rowcount == 0:
                cursor.execute(
                    """
                    INSERT INTO notification_proposals (
                        proposal_id, learner_id, channel, category, created_at, status
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        proposal_id,
                        learner_id,
                        channel.value if isinstance(channel, ChannelType) else str(channel),
                        category.value if isinstance(category, NotificationCategory) else str(category),
                        failed_at,
                        NotificationStatus.FAILED.value,
                    ),
                )

    def resolve_dlq(self, dlq_id: str) -> bool:
        """Marks a DLQ entry as resolved after successful replay."""
        with self.connection:
            cursor = self.connection.cursor()
            cursor.execute("UPDATE notification_dlq SET resolved = 1 WHERE dlq_id = ?", (dlq_id,))
            return cursor.rowcount > 0

    def get_dlq_entry(self, dlq_id: str) -> dict[str, Any] | None:
        """Fetches a single DLQ entry by ID."""
        cursor = self.connection.cursor()
        cursor.execute(
            """
            SELECT dlq_id, proposal_id, learner_id, channel, category, failed_at, reason, attempts, payload_json, resolved
            FROM notification_dlq
            WHERE dlq_id = ?
            """,
            (dlq_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "dlq_id": row[0],
            "proposal_id": row[1],
            "learner_id": row[2],
            "channel": row[3],
            "category": row[4],
            "failed_at": row[5],
            "reason": row[6],
            "attempts": row[7],
            "payload": json.loads(row[8]),
            "resolved": bool(row[9]),
        }

    def record_rescheduled(
        self,
        proposal_id: str,
        learner_id: str,
        channel: ChannelType,
        category: NotificationCategory,
        rescheduled_for: str,
        created_at: str,
        payload: Any,
    ) -> None:
        with self.connection:
            cursor = self.connection.cursor()
            cursor.execute(
                """
                INSERT INTO notification_rescheduled (
                    proposal_id, learner_id, channel, category, rescheduled_for, created_at, payload_json, released
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(proposal_id) DO UPDATE SET
                    rescheduled_for = excluded.rescheduled_for
                """,
                (
                    proposal_id,
                    learner_id,
                    channel.value if isinstance(channel, ChannelType) else str(channel),
                    category.value if isinstance(category, NotificationCategory) else str(category),
                    rescheduled_for,
                    created_at,
                    json.dumps(payload),
                ),
            )
            cursor.execute(
                "UPDATE notification_proposals SET status = ? WHERE proposal_id = ?",
                (NotificationStatus.QUEUED.value, proposal_id),
            )
            if cursor.rowcount == 0:
                cursor.execute(
                    """
                    INSERT INTO notification_proposals (
                        proposal_id, learner_id, channel, category, created_at, status
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        proposal_id,
                        learner_id,
                        channel.value if isinstance(channel, ChannelType) else str(channel),
                        category.value if isinstance(category, NotificationCategory) else str(category),
                        created_at,
                        NotificationStatus.QUEUED.value,
                    ),
                )

    def get_rescheduled_entry(self, proposal_id: str) -> dict[str, Any] | None:
        """Fetches a single rescheduled entry by proposal_id."""
        cursor = self.connection.cursor()
        cursor.execute(
            """
            SELECT proposal_id, learner_id, channel, category, rescheduled_for, created_at, payload_json, released
            FROM notification_rescheduled
            WHERE proposal_id = ?
            """,
            (proposal_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "proposal_id": row[0],
            "learner_id": row[1],
            "channel": row[2],
            "category": row[3],
            "rescheduled_for": row[4],
            "created_at": row[5],
            "payload": json.loads(row[6]),
            "released": bool(row[7]),
        }

    def get_suppression_entries(self, proposal_id: str | None = None) -> list[dict[str, Any]]:
        """Fetches suppression entries, optionally filtered by proposal_id."""
        cursor = self.connection.cursor()
        query = (
            "SELECT suppression_id, proposal_id, learner_id, channel, category, suppressed_at, reason "
            "FROM notification_suppressions"
        )
        params: list[Any] = []
        if proposal_id is not None:
            query += " WHERE proposal_id = ?"
            params.append(proposal_id)
        cursor.execute(query, tuple(params))
        return [
            {
                "suppression_id": row[0],
                "proposal_id": row[1],
                "learner_id": row[2],
                "channel": row[3],
                "category": row[4],
                "suppressed_at": row[5],
                "reason": row[6],
            }
            for row in cursor.fetchall()
        ]

    def get_due_rescheduled(self, now_iso: str) -> list[dict[str, Any]]:
        """Returns rescheduled rows whose target time has arrived and are not yet released."""
        cursor = self.connection.cursor()
        cursor.execute(
            """
            SELECT proposal_id, learner_id, channel, category, rescheduled_for, created_at, payload_json
            FROM notification_rescheduled
            WHERE released = 0 AND rescheduled_for <= ?
            ORDER BY rescheduled_for ASC
            """,
            (now_iso,),
        )
        return [
            {
                "proposal_id": row[0],
                "learner_id": row[1],
                "channel": row[2],
                "category": row[3],
                "rescheduled_for": row[4],
                "created_at": row[5],
                "payload": json.loads(row[6]),
            }
            for row in cursor.fetchall()
        ]

    def mark_rescheduled_released(self, proposal_id: str) -> None:
        """Marks a rescheduled row as released to prevent duplicate dispatch."""
        with self.connection:
            cursor = self.connection.cursor()
            cursor.execute(
                "UPDATE notification_rescheduled SET released = 1 WHERE proposal_id = ?",
                (proposal_id,),
            )

    def get_dispatch_history_since(
        self,
        learner_id: str,
        since_iso: str,
        channel: ChannelType | None = None,
    ) -> list[str]:
        """Returns dispatched_at timestamps for a learner across all channels (or specific channel) since cutoff."""
        cursor = self.connection.cursor()
        if channel is not None:
            cursor.execute(
                """
                SELECT dispatched_at
                FROM notification_dispatches
                WHERE learner_id = ? AND channel = ? AND dispatched_at >= ?
                ORDER BY dispatched_at ASC
                """,
                (learner_id, channel.value, since_iso),
            )
        else:
            cursor.execute(
                """
                SELECT dispatched_at
                FROM notification_dispatches
                WHERE learner_id = ? AND dispatched_at >= ?
                ORDER BY dispatched_at ASC
                """,
                (learner_id, since_iso),
            )
        return [row[0] for row in cursor.fetchall()]

    def get_dlq_entries(self, resolved: bool | None = None) -> list[dict[str, Any]]:
        cursor = self.connection.cursor()
        query = (
            "SELECT dlq_id, proposal_id, learner_id, channel, category, failed_at, reason, attempts, payload_json, resolved "
            "FROM notification_dlq"
        )
        params: list[Any] = []
        if resolved is not None:
            query += " WHERE resolved = ?"
            params.append(1 if resolved else 0)
        cursor.execute(query, tuple(params))
        return [
            {
                "dlq_id": row[0],
                "proposal_id": row[1],
                "learner_id": row[2],
                "channel": row[3],
                "category": row[4],
                "failed_at": row[5],
                "reason": row[6],
                "attempts": row[7],
                "payload": json.loads(row[8]),
                "resolved": bool(row[9]),
            }
            for row in cursor.fetchall()
        ]

    def close(self) -> None:
        self.connection.close()
