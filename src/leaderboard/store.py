"""Pluggable leaderboard storage implementations supporting deterministic tie-breaking."""

from abc import ABC, abstractmethod
import sqlite3
from typing import Sequence

from src.leaderboard.models import LeaderboardEntry, RankedLearner


class LeaderboardStore(ABC):
    """Abstract interface for leaderboard read model persistence."""

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
    def clear(
        self,
        scope_type: str | None = None,
        scope_id: str | None = None,
        epoch: str | None = None,
    ) -> None:
        """Clears entries matching the filter, or all entries."""
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        """Releases underlying storage resources."""
        raise NotImplementedError


class SQLiteLeaderboardStore(LeaderboardStore):
    """SQLite-backed store using compound indexing for exact deterministic tie-breaks."""

    def __init__(self, database: str = ":memory:") -> None:
        self.connection = sqlite3.connect(database)
        self.connection.execute("PRAGMA journal_mode=WAL")
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
        self.connection.commit()

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
        cursor.execute(
            """
            SELECT COALESCE(MAX(watermark_offset), 0)
            FROM leaderboard_entries
            WHERE scope_type = ? AND scope_id = ? AND epoch = ?
            """,
            (scope_type, scope_id, epoch),
        )
        row = cursor.fetchone()
        return row[0] if row else 0

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
        else:
            cursor.execute("DELETE FROM leaderboard_entries")
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()


class InMemoryLeaderboardStore(LeaderboardStore):
    """In-memory leaderboard store for zero-overhead unit testing and fast benchmarks."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str, str, str], LeaderboardEntry] = {}

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
        entries = self._sorted_scope_entries(scope_type, scope_id, epoch)
        return max((e.watermark_offset for e in entries), default=0)

    def clear(
        self,
        scope_type: str | None = None,
        scope_id: str | None = None,
        epoch: str | None = None,
    ) -> None:
        if scope_type is None and scope_id is None and epoch is None:
            self._entries.clear()
            return
        keys_to_remove = [
            k for k in self._entries
            if (scope_type is None or k[0] == scope_type)
            and (scope_id is None or k[1] == scope_id)
            and (epoch is None or k[2] == epoch)
        ]
        for k in keys_to_remove:
            self._entries.pop(k, None)

    def close(self) -> None:
        self._entries.clear()
