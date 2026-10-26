"""Full-state rebuild and disaster recovery orchestration (AT-2, AT-15)."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
import time
from typing import Any
from uuid import UUID

from eventsourcing.application import Notification
from src.leaderboard.projection import LeaderboardProjection
from src.leaderboard.service import LeaderboardService
from src.leaderboard.store import SQLiteLeaderboardStore
from src.state_machine.learner import Learner, LearnerApplication
from src.state_machine.projection import rebuild_learner_state
from .workload import SyntheticWorkload


@dataclass(frozen=True)
class LearnerSnapshot:
    """Pre- or post-rebuild state vector for a single learner."""

    learner_id: str
    xp_total: int
    streak: int
    freezes: int
    activity_dates: tuple[str, ...]
    cohort_id: str
    cohort_score: int
    cohort_rank: int | None
    cohort_first_reached_at: str | None
    global_score: int
    global_rank: int | None
    global_first_reached_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RebuildResult:
    """Result of full-state disaster recovery and deterministic verification."""

    total_learners: int
    total_events: int
    rebuild_duration_sec: float
    throughput_events_per_sec: float
    bit_identical: bool
    pre_checksum: str
    post_checksum: str
    mismatches: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_learners": self.total_learners,
            "total_events": self.total_events,
            "rebuild_duration_sec": self.rebuild_duration_sec,
            "throughput_events_per_sec": self.throughput_events_per_sec,
            "bit_identical": self.bit_identical,
            "pre_checksum": self.pre_checksum,
            "post_checksum": self.post_checksum,
            "mismatches": self.mismatches,
        }


def read_all_notifications(app: LearnerApplication, start: int = 1) -> list[Notification]:
    """Reads all notifications from application notification log starting at given position."""
    notifications = []
    pos = start
    while True:
        batch = list(
            app.notification_log.select(
                start=pos, limit=app.notification_log.section_size
            )
        )
        if not batch:
            break
        notifications.extend(batch)
        pos = batch[-1].id + 1
    return notifications


class FullStateRebuildHarness:
    """Orchestrates multi-learner population ingestion, pre-wipe capture, wipe, and replay."""

    def __init__(self, events_db_path: str, leaderboard_db_path: str, epoch: str = "2026-W42") -> None:
        self.events_db_path = events_db_path
        self.leaderboard_db_path = leaderboard_db_path
        self.epoch = epoch
        self.app: LearnerApplication | None = None
        self.store: SQLiteLeaderboardStore | None = None
        self.proj: LeaderboardProjection | None = None
        self.service: LeaderboardService | None = None

    def initialize_live_system(self) -> None:
        """Initializes authoritative SQLite event store and leaderboard read model."""
        self.app = LearnerApplication(
            env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": self.events_db_path}
        )
        self.store = SQLiteLeaderboardStore(self.leaderboard_db_path)
        self.proj = LeaderboardProjection(self.store)
        self.service = LeaderboardService(self.store)

    def close(self) -> None:
        """Safely closes open database connections."""
        if self.store is not None:
            self.store.close()
            self.store = None
        if self.app is not None:
            self.app.close()
            self.app = None

    def ingest_workload(self, workload: SyntheticWorkload) -> dict[str, UUID]:
        """Ingests the synthetic workload driving real domain aggregates and projections."""
        if self.app is None or self.proj is None:
            raise RuntimeError("Harness must be initialized before ingesting workload.")

        learner_uuids: dict[str, UUID] = {}

        # 1. Initialize learners and cohort assignments
        for lid in workload.learner_ids:
            cohort_id = workload.cohort_assignments[lid]
            learner = Learner()
            learner.assign_cohort(cohort_id, self.epoch, assigned_at=workload.profile.base_start_iso)
            self.app.save(learner)
            learner_uuids[lid] = learner.id

        # 2. Ingest activity events
        for evt in workload.events:
            lid = evt.learner_id
            uid = learner_uuids[lid]
            learner = self.app.repository.get(uid)
            learner.complete_lesson(
                completion_id=evt.completion_id,
                lesson_id=evt.lesson_id,
                occurred_at=evt.occurred_at,
                timezone="UTC",
            )
            self.app.save(learner)

        # 3. Project authoritative notifications into the leaderboard read model
        notifications = read_all_notifications(self.app, start=1)
        for n in notifications:
            self.proj.apply(n, offset=n.id)

        return learner_uuids

    def capture_snapshot(
        self, workload: SyntheticWorkload, learner_uuids: dict[str, UUID]
    ) -> dict[str, LearnerSnapshot]:
        """Captures complete aggregate and projection state vectors across the population."""
        if self.app is None or self.service is None:
            raise RuntimeError("Harness must be open to capture snapshot.")

        snapshot: dict[str, LearnerSnapshot] = {}
        for lid in workload.learner_ids:
            uid = learner_uuids[lid]
            cohort_id = workload.cohort_assignments[lid]

            # Aggregate state via pure fold
            events = list(self.app.events.get(uid))
            fold_state = rebuild_learner_state(events)

            # Leaderboard state
            cohort_standing = self.service.get_learner_rank("cohort", cohort_id, self.epoch, lid)
            global_standing = self.service.get_learner_rank("global", "global", self.epoch, lid)

            snapshot[lid] = LearnerSnapshot(
                learner_id=lid,
                xp_total=fold_state.xp_total,
                streak=fold_state.new_streak,
                freezes=fold_state.freezes_remaining,
                activity_dates=fold_state.activity_dates,
                cohort_id=cohort_id,
                cohort_score=cohort_standing.score if cohort_standing else 0,
                cohort_rank=cohort_standing.rank if cohort_standing else None,
                cohort_first_reached_at=cohort_standing.first_reached_at if cohort_standing else None,
                global_score=global_standing.score if global_standing else 0,
                global_rank=global_standing.rank if global_standing else None,
                global_first_reached_at=global_standing.first_reached_at if global_standing else None,
            )
        return snapshot

    def simulate_projection_wipe(self) -> None:
        """Simulates total read-model failure by closing and wiping the leaderboard database."""
        if self.store is not None:
            self.store.close()
            self.store = None
            self.proj = None
            self.service = None

        if os.path.exists(self.leaderboard_db_path):
            os.remove(self.leaderboard_db_path)

        # Reopen clean empty leaderboard store
        self.store = SQLiteLeaderboardStore(self.leaderboard_db_path)
        self.proj = LeaderboardProjection(self.store)
        self.service = LeaderboardService(self.store)

    def execute_full_rebuild(
        self, workload: SyntheticWorkload, learner_uuids: dict[str, UUID]
    ) -> tuple[dict[str, LearnerSnapshot], float, int]:
        """Replays all authoritative events from offset 0 and measures recovery duration."""
        if self.app is None or self.proj is None:
            raise RuntimeError("Harness must be initialized.")

        t0 = time.perf_counter()

        # 1. Replay leaderboard projection from notification log offset 1
        notifications = read_all_notifications(self.app, start=1)
        for n in notifications:
            self.proj.apply(n, offset=n.id)

        # 2. Re-capture full snapshot post-rebuild
        post_snapshot = self.capture_snapshot(workload, learner_uuids)

        t1 = time.perf_counter()
        rebuild_duration = max(t1 - t0, 1e-6)
        return post_snapshot, rebuild_duration, len(notifications)

    def run_rebuild_verification(self, workload: SyntheticWorkload) -> RebuildResult:
        """Full lifecycle: live ingestion -> pre-wipe snapshot -> wipe -> replay -> bit-identical assert."""
        self.initialize_live_system()
        try:
            learner_uuids = self.ingest_workload(workload)
            pre_snapshot = self.capture_snapshot(workload, learner_uuids)

            # Checksum pre-wipe state
            pre_json = json.dumps({k: v.to_dict() for k, v in sorted(pre_snapshot.items())}, sort_keys=True)
            pre_hash = hashlib.sha256(pre_json.encode("utf-8")).hexdigest()

            # Wipe projection read models
            self.simulate_projection_wipe()

            # Rebuild from event log
            post_snapshot, rebuild_duration, total_notifications = self.execute_full_rebuild(workload, learner_uuids)

            # Checksum post-rebuild state
            post_json = json.dumps({k: v.to_dict() for k, v in sorted(post_snapshot.items())}, sort_keys=True)
            post_hash = hashlib.sha256(post_json.encode("utf-8")).hexdigest()

            mismatches: list[str] = []
            for lid, pre in pre_snapshot.items():
                post = post_snapshot.get(lid)
                if post is None:
                    mismatches.append(f"Missing learner {lid} in post-rebuild snapshot")
                    continue
                if pre != post:
                    mismatches.append(f"State mismatch for {lid}: pre={pre.to_dict()} vs post={post.to_dict()}")

            bit_identical = (pre_hash == post_hash) and (len(mismatches) == 0)
            throughput = total_notifications / rebuild_duration

            return RebuildResult(
                total_learners=len(workload.learner_ids),
                total_events=total_notifications,
                rebuild_duration_sec=rebuild_duration,
                throughput_events_per_sec=throughput,
                bit_identical=bit_identical,
                pre_checksum=pre_hash,
                post_checksum=post_hash,
                mismatches=mismatches,
            )
        finally:
            self.close()
