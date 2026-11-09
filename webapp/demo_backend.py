"""Writable demo sandbox managing isolated golden and live SQLite stores.

Provides:
- create_sandbox(path) -> Sandbox
- Sandbox: reset(), set_clock(), get_clock(), submit_lesson_completed(), submit_checkpoint()
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import threading
from typing import Any
from uuid import UUID, uuid4

from eventsourcing.application import AggregateNotFoundError
from src.ingestion.config import IngestConfig
from src.ingestion.evidence import EvidenceStore
from src.ingestion.service import ingest_checkpoint_attempted, ingest_lesson_completed
from src.ingestion.validation import expected_dedup_key
from src.interventions.notification_hook import run_remediation_with_notification
from src.leaderboard.projection import LeaderboardProjection
from src.leaderboard.store import SQLiteLeaderboardStore
from src.notifications.dispatcher import NotificationDispatcher
from src.notifications.models import ChannelType, LearnerPreferences
from src.notifications.store import NotificationStore
from src.scale_and_recovery.rebuild import read_all_notifications
from src.state_machine.learner import LearnerApplication
from webapp.seed import AS_OF, seed
from webapp.views import learner_rule_version

_submit_lock = threading.Lock()
_VERSION_FILENAME = "demo_version"


def _parse_iso_utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        dt = value
    if dt.tzinfo is None or dt.utcoffset() is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _checkpoint_and_clean_db(db_path: Path) -> None:
    if not db_path.exists():
        return
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists():
            sidecar.unlink()


def _copy_golden_to_live(golden_dir: Path, live_dir: Path) -> None:
    live_dir.mkdir(parents=True, exist_ok=True)
    for item in golden_dir.iterdir():
        if item.is_file() and not item.name.endswith(("-wal", "-shm")):
            shutil.copy2(item, live_dir / item.name)


def _init_demo_state(demo_state_path: Path, last_offset: int = 0) -> None:
    con = sqlite3.connect(str(demo_state_path))
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS learner_clock (
                learner_id TEXT PRIMARY KEY,
                now_utc TEXT NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS projection_offset (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                last_offset INTEGER NOT NULL
            )
            """
        )
        con.execute(
            """
            INSERT INTO projection_offset (id, last_offset)
            VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET last_offset = excluded.last_offset
            """,
            (last_offset,),
        )
        con.commit()
    finally:
        con.close()


def _get_last_offset(demo_state_path: Path) -> int:
    con = sqlite3.connect(str(demo_state_path))
    try:
        cur = con.execute("SELECT last_offset FROM projection_offset WHERE id = 1")
        row = cur.fetchone()
        return row[0] if row else 0
    finally:
        con.close()


def _set_last_offset(demo_state_path: Path, offset: int) -> None:
    con = sqlite3.connect(str(demo_state_path))
    try:
        con.execute("UPDATE projection_offset SET last_offset = ? WHERE id = 1", (offset,))
        con.commit()
    finally:
        con.close()


def _version_path(root: Path) -> Path:
    return root / _VERSION_FILENAME


def _read_version(root: Path) -> int:
    path = _version_path(root)
    try:
        return int(path.read_text(encoding="ascii").strip())
    except FileNotFoundError:
        return 0


def _write_version(root: Path, version: int) -> None:
    _version_path(root).write_text(f"{version}\n", encoding="ascii")


def _bump_version(root: Path) -> int:
    version = _read_version(root) + 1
    _write_version(root, version)
    return version


def _get_seeded_offset(golden_events_db: Path) -> int:
    app = LearnerApplication(
        env={
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(golden_events_db),
        }
    )
    try:
        notifications = read_all_notifications(app, start=1)
        return notifications[-1].id if notifications else 0
    finally:
        app.close()


def _project_new_notifications(
    app: LearnerApplication,
    leaderboard_db: Path,
    demo_state_db: Path,
) -> None:
    last_offset = _get_last_offset(demo_state_db)
    new_notifications = read_all_notifications(app, start=last_offset + 1)
    if not new_notifications:
        return
    lb_store = SQLiteLeaderboardStore(str(leaderboard_db))
    try:
        projection = LeaderboardProjection(lb_store)
        for n in new_notifications:
            projection.apply(n, offset=n.id)
        _set_last_offset(demo_state_db, new_notifications[-1].id)
    finally:
        lb_store.close()


class Sandbox:
    """Sandbox environment managing live state copied from golden seed data."""

    def __init__(self, path: Path | str) -> None:
        self.root = Path(path).resolve()
        self.golden_dir = self.root / "golden"
        self.live_dir = self.root / "live"
        self.path = self.live_dir

    @property
    def version(self) -> int:
        return _read_version(self.root)

    def reset(self) -> None:
        """Deletes everything in live, recopies golden, and recreates evidence/demo_state."""
        with _submit_lock:
            if self.live_dir.exists():
                for item in self.live_dir.iterdir():
                    if item.is_file() or item.is_symlink():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)
            _copy_golden_to_live(self.golden_dir, self.live_dir)
            evidence = EvidenceStore(str(self.live_dir / "evidence.db"))
            evidence.close()
            seeded_offset = _get_seeded_offset(self.golden_dir / "events.db")
            _init_demo_state(self.live_dir / "demo_state.db", last_offset=seeded_offset)
            _bump_version(self.root)

    def get_clock(self, learner_id: str) -> datetime:
        """Returns the simulated clock for the learner, or webapp.seed.AS_OF if unset."""
        db_path = self.live_dir / "demo_state.db"
        if not db_path.exists():
            return _parse_iso_utc(AS_OF)
        con = sqlite3.connect(str(db_path))
        try:
            cur = con.execute(
                "SELECT now_utc FROM learner_clock WHERE learner_id = ?",
                (str(learner_id),),
            )
            row = cur.fetchone()
            if row is not None:
                return _parse_iso_utc(row[0])
            return _parse_iso_utc(AS_OF)
        finally:
            con.close()

    def set_clock(self, learner_id: str, now_utc: datetime | str) -> None:
        """Stores the simulated clock for a learner in demo_state.db."""
        dt = _parse_iso_utc(now_utc)
        iso_str = dt.isoformat().replace("+00:00", "Z")
        db_path = self.live_dir / "demo_state.db"
        with _submit_lock:
            previous = self.get_clock(learner_id)
            con = sqlite3.connect(str(db_path))
            try:
                con.execute(
                    """
                    INSERT INTO learner_clock (learner_id, now_utc)
                    VALUES (?, ?)
                    ON CONFLICT(learner_id) DO UPDATE SET now_utc = excluded.now_utc
                    """,
                    (str(learner_id), iso_str),
                )
                con.commit()
            finally:
                con.close()
            if previous != dt:
                _bump_version(self.root)

    def submit_lesson_completed(
        self,
        learner_id: str,
        lesson_id: str,
        completion_kind: str = "lesson",
        event_id: str | None = None,
        occurred_at: datetime | str | None = None,
    ) -> dict[str, Any]:
        """Submits a lesson completion envelope to ingestion inside a process-wide lock."""
        with _submit_lock:
            # 1. Check learner ID validity
            try:
                learner_uuid = UUID(str(learner_id))
            except (ValueError, TypeError, AttributeError):
                return {"status": "rejected", "reason": "learner_not_found"}

            # 2. Open fresh LearnerApplication to verify learner existence and get state
            app = LearnerApplication(
                env={
                    "PERSISTENCE_MODULE": "eventsourcing.sqlite",
                    "SQLITE_DBNAME": str(self.live_dir / "events.db"),
                }
            )
            evidence = EvidenceStore(str(self.live_dir / "evidence.db"))
            try:
                try:
                    learner = app.repository.get(learner_uuid)
                except AggregateNotFoundError:
                    return {"status": "rejected", "reason": "learner_not_found"}

                # 3. Resolve rule version, clocks, and timezone
                rule_version = learner_rule_version(learner)
                demo_clock = self.get_clock(str(learner_id))
                occurred_dt = _parse_iso_utc(occurred_at) if occurred_at is not None else demo_clock

                occurred_iso = occurred_dt.isoformat().replace("+00:00", "Z")
                ingested_iso = demo_clock.isoformat().replace("+00:00", "Z")
                tz = (
                    learner.timezone_history[-1]["timezone"]
                    if learner.timezone_history
                    else "UTC"
                )

                # 4. Construct envelope
                evt_id = str(event_id) if event_id is not None else str(uuid4())
                payload: dict[str, Any] = {
                    "completion_id": f"cmp_{evt_id}",
                    "lesson_id": str(lesson_id),
                    "timezone": tz,
                }
                if completion_kind is not None:
                    payload["completion_kind"] = str(completion_kind)

                envelope: dict[str, Any] = {
                    "event_id": evt_id,
                    "event_type": "learning.lesson_completed",
                    "schema_version": 1,
                    "occurred_at": occurred_iso,
                    "ingested_at": ingested_iso,
                    "subject_id": str(learner_id),
                    "producer": "learning-svc",
                    "correlation_id": str(uuid4()),
                    "causation_id": None,
                    "dedup_key": expected_dedup_key(evt_id, rule_version),
                    "payload": payload,
                    "provenance": {"source": "client"},
                }

                # 5. Ingest event
                config = IngestConfig(rule_version=rule_version, clock=lambda: demo_clock)
                res = ingest_lesson_completed(
                    app,
                    evidence,
                    envelope,
                    authenticated_producer="learning-svc",
                    config=config,
                )

                # 6. Synchronous leaderboard projection reaction on accept
                if res.status == "accepted":
                    _project_new_notifications(
                        app,
                        self.live_dir / "leaderboard.db",
                        self.live_dir / "demo_state.db",
                    )
                    _bump_version(self.root)

                return {"status": res.status, "reason": res.reason}
            finally:
                evidence.close()
                app.close()

    def submit_checkpoint(
        self,
        learner_id: str,
        assessment_id: str,
        passed: bool,
        attempt_id: str | None = None,
        occurred_at: datetime | str | None = None,
    ) -> dict[str, Any]:
        """Submits a checkpoint attempt envelope to ingestion inside a process-wide lock."""
        with _submit_lock:
            # 1. Check learner ID validity
            try:
                learner_uuid = UUID(str(learner_id))
            except (ValueError, TypeError, AttributeError):
                return {"status": "rejected", "reason": "learner_not_found"}

            # 2. Open fresh LearnerApplication to verify learner existence and get state
            app = LearnerApplication(
                env={
                    "PERSISTENCE_MODULE": "eventsourcing.sqlite",
                    "SQLITE_DBNAME": str(self.live_dir / "events.db"),
                }
            )
            evidence = EvidenceStore(str(self.live_dir / "evidence.db"))
            try:
                try:
                    learner = app.repository.get(learner_uuid)
                except AggregateNotFoundError:
                    return {"status": "rejected", "reason": "learner_not_found"}

                # 3. Resolve rule version, clocks, and timezone
                rule_version = learner_rule_version(learner)
                demo_clock = self.get_clock(str(learner_id))
                occurred_dt = _parse_iso_utc(occurred_at) if occurred_at is not None else demo_clock

                occurred_iso = occurred_dt.isoformat().replace("+00:00", "Z")
                ingested_iso = demo_clock.isoformat().replace("+00:00", "Z")
                tz = (
                    learner.timezone_history[-1]["timezone"]
                    if learner.timezone_history
                    else "UTC"
                )

                # 4. Construct envelope
                evt_id = str(uuid4())
                att_id = str(attempt_id) if attempt_id is not None else f"att_{evt_id}"
                payload: dict[str, Any] = {
                    "attempt_id": att_id,
                    "assessment_id": str(assessment_id),
                    "assessment_version": 1,
                    "passed": bool(passed),
                    "timezone": tz,
                }

                envelope: dict[str, Any] = {
                    "event_id": evt_id,
                    "event_type": "learning.checkpoint_attempted",
                    "schema_version": 1,
                    "occurred_at": occurred_iso,
                    "ingested_at": ingested_iso,
                    "subject_id": str(learner_id),
                    "producer": "learning-svc",
                    "correlation_id": str(uuid4()),
                    "causation_id": None,
                    "dedup_key": expected_dedup_key(evt_id, rule_version),
                    "payload": payload,
                    "provenance": {"source": "client"},
                }

                # 5. Ingest event
                config = IngestConfig(rule_version=rule_version, clock=lambda: demo_clock)
                res = ingest_checkpoint_attempted(
                    app,
                    evidence,
                    envelope,
                    authenticated_producer="learning-svc",
                    config=config,
                )

                if res.status == "accepted":
                    # 6. Remediation reaction (checkpoint attempts only)
                    profile_data = json.loads((self.live_dir / "ui_profile.json").read_text())
                    learner_info = profile_data.get("learners", {}).get(str(learner_id), {})
                    push_enabled = learner_info.get("push_enabled", True)
                    opted_out = set() if push_enabled else {ChannelType.PUSH}
                    prefs = LearnerPreferences(
                        learner_id=str(learner_id),
                        timezone=tz,
                        opted_out_channels=opted_out,
                    )
                    notif_store = NotificationStore(str(self.live_dir / "notifications.db"))
                    try:
                        dispatcher = NotificationDispatcher(notif_store, clock=lambda: demo_clock)
                        run_remediation_with_notification(
                            app,
                            str(learner_id),
                            att_id,
                            clock=lambda: demo_clock,
                            dispatcher=dispatcher,
                            prefs=prefs,
                        )
                    finally:
                        notif_store.close()

                    # 7. Synchronous leaderboard projection reaction
                    _project_new_notifications(
                        app,
                        self.live_dir / "leaderboard.db",
                        self.live_dir / "demo_state.db",
                    )
                    _bump_version(self.root)

                return {"status": res.status, "reason": res.reason}
            finally:
                evidence.close()
                app.close()


def create_sandbox(path: Path | str) -> Sandbox:
    """Seeds fresh stores into <path>/golden, truncates WAL, copies to <path>/live, and creates evidence/demo_state."""
    root = Path(path).resolve()
    golden_dir = root / "golden"
    live_dir = root / "live"

    golden_dir.mkdir(parents=True, exist_ok=True)
    live_dir.mkdir(parents=True, exist_ok=True)
    if not _version_path(root).exists():
        _write_version(root, 0)

    # 1. Seed fresh stores into <path>/golden
    seed(golden_dir)

    # 2. Checkpoint and truncate WAL on each golden database, delete sidecars
    for db_name in ("events.db", "leaderboard.db", "notifications.db"):
        _checkpoint_and_clean_db(golden_dir / db_name)

    # 3. Add the demo-only available freeze to Priya's golden aggregate.
    overlay_app = LearnerApplication(
        env={
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(golden_dir / "events.db"),
        }
    )
    try:
        priya = overlay_app.repository.get(UUID("4871b5d7-1673-5416-8a37-48353d80e6a4"))
        priya.acquire_freeze("freeze:demo_overlay", activity_date="2026-10-16")
        overlay_app.save(priya)
    finally:
        overlay_app.close()

    _checkpoint_and_clean_db(golden_dir / "events.db")

    # 4. Catch up the existing leaderboard projection, including the overlay log entry.
    projection_app = LearnerApplication(
        env={
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(golden_dir / "events.db"),
        }
    )
    try:
        lb_store = SQLiteLeaderboardStore(str(golden_dir / "leaderboard.db"))
        try:
            projection = LeaderboardProjection(lb_store)
            for notification in read_all_notifications(projection_app, start=1):
                projection.apply(notification, offset=notification.id)
        finally:
            lb_store.close()
    finally:
        projection_app.close()

    _checkpoint_and_clean_db(golden_dir / "leaderboard.db")

    # 5. Determine the final notification offset after the overlay and projection catch-up.
    seeded_offset = _get_seeded_offset(golden_dir / "events.db")

    # 6. Copy golden files to <path>/live
    _copy_golden_to_live(golden_dir, live_dir)

    # 7. Create empty evidence.db with correct schema
    evidence = EvidenceStore(str(live_dir / "evidence.db"))
    evidence.close()

    # 8. Create demo_state.db with learner_clock and projection_offset tables initialized to seeded_offset
    _init_demo_state(live_dir / "demo_state.db", last_offset=seeded_offset)

    return Sandbox(root)
