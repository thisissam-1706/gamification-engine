"""Writable demo sandbox managing isolated golden and live SQLite stores.

Provides:
- create_sandbox(path) -> Sandbox
- Sandbox: reset(), set_clock(), get_clock(), submit_lesson_completed(), submit_checkpoint()
"""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time
from typing import Any
from uuid import UUID, uuid4

from eventsourcing.application import AggregateNotFoundError
from src.abuse.service import AbuseDetectionService
from src.abuse.store import AbuseSignalStore
from src.abuse.xp_hold import store_hold_lookup
from src.ingestion.config import IngestConfig
from src.ingestion.evidence import EvidenceStore
from src.ingestion.service import ingest_checkpoint_attempted, ingest_lesson_completed
from src.ingestion.validation import expected_dedup_key
from src.interventions.notification_hook import run_remediation_with_notification
from src.leaderboard.projection import LeaderboardProjection
from src.leaderboard.store import SQLiteLeaderboardStore
from src.notifications.store import NotificationStore
from src.scale_and_recovery.rebuild import read_all_notifications
from src.state_machine.rules import RULESETS, register_decay_rulesets
from src.state_machine.learner import LearnerApplication
from webapp import demo_notify
from webapp.seed import AS_OF, seed
from webapp.seed import learner_uuid as seed_learner_uuid

# The demo opts in to the packaged decay rule sets (v2, v3) when a sandbox is opened or created
# (Sandbox.__init__, create_sandbox), not at import time: importing this module must not change
# the process-wide rule registry, or every other caller (and the test suite) would see v2/v3.

# New demo rewards use v3 (repeat decay with a 5 XP floor) unless a learner is switched in the admin.
DEMO_DEFAULT_RULE_VERSION = "v3"

_submit_lock = threading.Lock()
_VERSION_FILENAME = "demo_version"
_CLOCK_RUN_DDL = (
    "CREATE TABLE IF NOT EXISTS learner_clock_run ("
    "learner_id TEXT PRIMARY KEY, anchored_at REAL NOT NULL, frozen INTEGER NOT NULL DEFAULT 0)"
)


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


def _ensure_demo_tables(db_path: Path) -> None:
    """Creates the admin demo tables in demo_state.db (idempotent)."""
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS demo_sent_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                learner_id TEXT,
                kind TEXT,
                event_id TEXT,
                envelope_json TEXT
            )
            """
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS learner_ruleset (learner_id TEXT PRIMARY KEY, version TEXT)"
        )
        con.execute("CREATE TABLE IF NOT EXISTS demo_flags (key TEXT PRIMARY KEY, value TEXT)")
        con.commit()
    finally:
        con.close()


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
    _ensure_demo_tables(demo_state_path)


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


def _live_abuse_pass(app: LearnerApplication, evidence: EvidenceStore, live_dir: Path,
                     learner_id: str) -> dict[str, Any]:
    """Runs the flag-only abuse pass for one learner right after a submission.

    Demo stand-in for the engine tick's abuse step, so a flag appears as soon as a pattern is
    crossed. It only reads facts and evidence and stores signals; XP, streaks and the event log
    are never touched (ADR 0003). A failure here never affects the submission.
    """
    try:
        signals = AbuseSignalStore(str(live_dir / "abuse.db"))
        try:
            before = {r.signal_id for r in signals.list(learner_id)}
            summary = AbuseDetectionService(app, evidence, signals).run(learner_ids=[learner_id])
            new = [s for s in summary.signals if s.signal_id not in before]
        finally:
            signals.close()
        return {"new_signals": len(new), "new_types": sorted({s.signal_type for s in new}),
                "new_rules": sorted({s.rule_id for s in new})}
    except Exception as exc:  # detection must never break the demo submission
        return {"new_signals": 0, "new_types": [], "new_rules": [], "error": str(exc)}


class Sandbox:
    """Sandbox environment managing live state copied from golden seed data."""

    def __init__(self, path: Path | str) -> None:
        register_decay_rulesets()  # idempotent; the demo rewards use v3 and the admin can switch to v2
        self.root = Path(path).resolve()
        self.golden_dir = self.root / "golden"
        self.live_dir = self.root / "live"
        self.path = self.live_dir
        # Demo server (scripts/start_demo.sh) sets DEMO_LIVE_CLOCK=1 so learner clocks tick in
        # real time by default. Everything else (tests, library use) keeps the frozen clock.
        self.live_default = os.environ.get("DEMO_LIVE_CLOCK") == "1"

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

    def _read_clock(self, learner_id: str) -> tuple[datetime, bool]:
        """Returns (now, frozen) for the learner.

        The clock is stored as a base instant plus the wall-clock moment it was anchored. While
        running (not frozen) it ticks forward in real time from the base; while frozen it stays
        at the base. A learner with no stored clock starts at webapp.seed.AS_OF (running only when DEMO_LIVE_CLOCK=1).
        """
        db_path = self.live_dir / "demo_state.db"
        if not db_path.exists():
            return _parse_iso_utc(AS_OF), True
        con = sqlite3.connect(str(db_path), timeout=10)
        try:
            con.execute(_CLOCK_RUN_DDL)
            row = con.execute(
                "SELECT now_utc FROM learner_clock WHERE learner_id = ?", (str(learner_id),)
            ).fetchone()
            base = _parse_iso_utc(row[0]) if row is not None else _parse_iso_utc(AS_OF)
            run = con.execute(
                "SELECT anchored_at, frozen FROM learner_clock_run WHERE learner_id = ?",
                (str(learner_id),),
            ).fetchone()
            if run is None:
                con.execute(
                    "INSERT OR IGNORE INTO learner_clock_run (learner_id, anchored_at, frozen) "
                    "VALUES (?, ?, ?)",
                    (str(learner_id), time.time(), 0 if self.live_default else 1),
                )
                con.commit()
                run = con.execute(
                    "SELECT anchored_at, frozen FROM learner_clock_run WHERE learner_id = ?",
                    (str(learner_id),),
                ).fetchone()
            anchored_at, frozen = float(run[0]), bool(run[1])
        finally:
            con.close()
        if frozen:
            return base, True
        return base + timedelta(seconds=max(0.0, time.time() - anchored_at)), False

    def get_clock(self, learner_id: str) -> datetime:
        """Returns the learner's simulated clock (running in real time unless frozen)."""
        return self._read_clock(learner_id)[0]

    def get_clock_state(self, learner_id: str) -> dict[str, Any]:
        now, frozen = self._read_clock(learner_id)
        return {"now_utc": now.isoformat().replace("+00:00", "Z"), "frozen": frozen}

    def set_frozen(self, learner_id: str, frozen: bool) -> None:
        """Freezes the clock at its current instant, or resumes it ticking from there."""
        with _submit_lock:
            now, _ = self._read_clock(learner_id)
            iso_str = now.isoformat().replace("+00:00", "Z")
            con = sqlite3.connect(str(self.live_dir / "demo_state.db"), timeout=10)
            try:
                con.execute(_CLOCK_RUN_DDL)
                con.execute(
                    """
                    INSERT INTO learner_clock (learner_id, now_utc) VALUES (?, ?)
                    ON CONFLICT(learner_id) DO UPDATE SET now_utc = excluded.now_utc
                    """,
                    (str(learner_id), iso_str),
                )
                con.execute(
                    """
                    INSERT INTO learner_clock_run (learner_id, anchored_at, frozen) VALUES (?, ?, ?)
                    ON CONFLICT(learner_id) DO UPDATE SET
                        anchored_at = excluded.anchored_at, frozen = excluded.frozen
                    """,
                    (str(learner_id), time.time(), 1 if frozen else 0),
                )
                con.commit()
            finally:
                con.close()
            _bump_version(self.root)

    def set_clock(self, learner_id: str, now_utc: datetime | str) -> None:
        """Stores the simulated clock for a learner in demo_state.db."""
        dt = _parse_iso_utc(now_utc)
        iso_str = dt.isoformat().replace("+00:00", "Z")
        db_path = self.live_dir / "demo_state.db"
        with _submit_lock:
            previous = self.get_clock(learner_id)
            con = sqlite3.connect(str(db_path))
            try:
                con.execute(_CLOCK_RUN_DDL)
                con.execute(
                    """
                    INSERT INTO learner_clock (learner_id, now_utc)
                    VALUES (?, ?)
                    ON CONFLICT(learner_id) DO UPDATE SET now_utc = excluded.now_utc
                    """,
                    (str(learner_id), iso_str),
                )
                # Re-anchor to now, keeping the frozen/running state the learner already had.
                con.execute(
                    """
                    INSERT INTO learner_clock_run (learner_id, anchored_at, frozen) VALUES (?, ?, ?)
                    ON CONFLICT(learner_id) DO UPDATE SET anchored_at = excluded.anchored_at
                    """,
                    (str(learner_id), time.time(), 0 if self.live_default else 1),
                )
                con.commit()
            finally:
                con.close()
            if previous != dt:
                _bump_version(self.root)

    # ---- admin demo helpers (additive)

    def _store_sent_event(self, learner_id: str, kind: str, event_id: str, envelope: dict) -> None:
        db_path = self.live_dir / "demo_state.db"
        _ensure_demo_tables(db_path)
        con = sqlite3.connect(str(db_path))
        try:
            con.execute(
                "INSERT INTO demo_sent_events (learner_id, kind, event_id, envelope_json) "
                "VALUES (?, ?, ?, ?)",
                (learner_id, kind, event_id, json.dumps(envelope, sort_keys=True)),
            )
            con.commit()
        finally:
            con.close()

    def _xp_hold(self):
        """XP hold for lesson rewards while a recent scripted-farming flag exists (xp_hold.py)."""
        return store_hold_lookup(lambda: AbuseSignalStore(str(self.live_dir / "abuse.db")))

    @staticmethod
    def _withheld(app: Any, learner_id: str, res: Any, envelope: dict) -> str | None:
        """The withheld_reason stored on this submission's XP reward, if any."""
        if res.status != "accepted":
            return None
        try:
            recent = list(app.events.get(UUID(str(learner_id))))[-6:]
        except Exception:
            return None
        for event in reversed(recent):
            if type(event).__name__ == "XPAwarded":
                return getattr(event, "withheld_reason", None)
        return None

    def _effective_rule_version(self, learner: Any, learner_id: str) -> str:
        """Rule version for this learner's next reward: the admin override, else the demo default.

        The demo defaults to v3 (repeat decay: a new lesson earns 100 XP, each repeat of the same
        lesson less, never below 5). v1 (flat 100 XP) and v2 (decay down to 0) are switchable. Rewards already stored keep the
        version they were decided under, so switching never changes past XP.
        """
        override = self._read_ruleset_override(str(learner_id))
        return override if override is not None else DEMO_DEFAULT_RULE_VERSION

    def _read_ruleset_override(self, learner_id: str) -> str | None:
        db_path = self.live_dir / "demo_state.db"
        _ensure_demo_tables(db_path)
        con = sqlite3.connect(str(db_path))
        try:
            row = con.execute(
                "SELECT version FROM learner_ruleset WHERE learner_id = ?", (learner_id,)
            ).fetchone()
            return row[0] if row else None
        finally:
            con.close()

    def get_ruleset_override(self, learner_id: str) -> str | None:
        return self._read_ruleset_override(str(learner_id))

    def set_ruleset_override(self, learner_id: str, version: str | None) -> None:
        """Sets (or with None clears) the rule version used for the learner's new events."""
        if version is not None and version not in RULESETS:
            raise ValueError(f"Unknown rule version: {version}")
        db_path = self.live_dir / "demo_state.db"
        with _submit_lock:
            previous = self._read_ruleset_override(str(learner_id))
            con = sqlite3.connect(str(db_path))
            try:
                if version is None:
                    con.execute("DELETE FROM learner_ruleset WHERE learner_id = ?", (str(learner_id),))
                else:
                    con.execute(
                        "INSERT INTO learner_ruleset (learner_id, version) VALUES (?, ?) "
                        "ON CONFLICT(learner_id) DO UPDATE SET version = excluded.version",
                        (str(learner_id), version),
                    )
                con.commit()
            finally:
                con.close()
            if previous != version:
                _bump_version(self.root)

    def _stored_envelope(self, learner_id: str, event_id: str | None) -> dict | None:
        db_path = self.live_dir / "demo_state.db"
        _ensure_demo_tables(db_path)
        con = sqlite3.connect(str(db_path))
        try:
            if event_id is None:
                row = con.execute(
                    "SELECT envelope_json FROM demo_sent_events WHERE learner_id = ? AND kind = 'lesson' "
                    "ORDER BY seq DESC LIMIT 1",
                    (str(learner_id),),
                ).fetchone()
            else:
                row = con.execute(
                    "SELECT envelope_json FROM demo_sent_events WHERE learner_id = ? AND event_id = ? "
                    "ORDER BY seq DESC LIMIT 1",
                    (str(learner_id), str(event_id)),
                ).fetchone()
            return json.loads(row[0]) if row else None
        finally:
            con.close()

    def sent_event_count(self, learner_id: str) -> int:
        db_path = self.live_dir / "demo_state.db"
        _ensure_demo_tables(db_path)
        con = sqlite3.connect(str(db_path))
        try:
            return con.execute(
                "SELECT COUNT(*) FROM demo_sent_events WHERE learner_id = ?", (str(learner_id),)
            ).fetchone()[0]
        finally:
            con.close()

    def resubmit_event(self, learner_id: str, event_id: str) -> dict[str, Any]:
        """Re-ingests the stored envelope of a previously sent lesson event, unchanged."""
        return self._resubmit(str(learner_id), str(event_id))

    def resubmit_last_lesson(self, learner_id: str) -> dict[str, Any]:
        """Re-ingests the newest stored lesson envelope for the learner, unchanged."""
        return self._resubmit(str(learner_id), None)

    def _resubmit(self, learner_id: str, event_id: str | None) -> dict[str, Any]:
        with _submit_lock:
            envelope = self._stored_envelope(learner_id, event_id)
            if envelope is None:
                return {"status": "rejected", "reason": "no_event_to_resubmit", "event_id": event_id}
            try:
                learner_uuid = UUID(learner_id)
            except (ValueError, TypeError, AttributeError):
                return {"status": "rejected", "reason": "learner_not_found", "event_id": event_id}
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
                    return {"status": "rejected", "reason": "learner_not_found", "event_id": event_id}
                demo_clock = self.get_clock(learner_id)
                rule_version = self._effective_rule_version(learner, learner_id)
                hold = self._xp_hold()
                config = IngestConfig(rule_version=rule_version, clock=lambda: demo_clock, xp_hold=hold)
                res = ingest_lesson_completed(
                    app, evidence, envelope, authenticated_producer="learning-svc", config=config
                )
                if res.status == "accepted":
                    _project_new_notifications(
                        app, self.live_dir / "leaderboard.db", self.live_dir / "demo_state.db"
                    )
                abuse = _live_abuse_pass(app, evidence, self.live_dir, learner_id)
                if res.status == "accepted" or abuse["new_signals"]:
                    _bump_version(self.root)
                return {"status": res.status, "reason": res.reason, "event_id": envelope["event_id"],
                        "abuse": abuse, "xp_withheld": self._withheld(app, learner_id, res, envelope)}
            finally:
                evidence.close()
                app.close()

    def submit_lesson_completed(
        self,
        learner_id: str,
        lesson_id: str,
        completion_kind: str = "lesson",
        event_id: str | None = None,
        occurred_at: datetime | str | None = None,
        completion_id: str | None = None,
        correlation_id: str | None = None,
        questions_correct: int | None = None,
        questions_total: int | None = None,
    ) -> dict[str, Any]:
        """Submits a lesson completion envelope to ingestion inside a process-wide lock.

        ``questions_correct`` / ``questions_total`` carry the lesson page's score; more than half
        wrong earns no XP (src/state_machine/pass_mark.py).
        """
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
                rule_version = self._effective_rule_version(learner, learner_id)
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
                    "completion_id": str(completion_id) if completion_id is not None else f"cmp_{evt_id}",
                    "lesson_id": str(lesson_id),
                    "timezone": tz,
                }
                if completion_kind is not None:
                    payload["completion_kind"] = str(completion_kind)
                if questions_correct is not None and questions_total is not None:
                    payload["questions_correct"] = int(questions_correct)
                    payload["questions_total"] = int(questions_total)

                envelope: dict[str, Any] = {
                    "event_id": evt_id,
                    "event_type": "learning.lesson_completed",
                    "schema_version": 1,
                    "occurred_at": occurred_iso,
                    "ingested_at": ingested_iso,
                    "subject_id": str(learner_id),
                    "producer": "learning-svc",
                    "correlation_id": str(correlation_id) if correlation_id is not None else str(uuid4()),
                    "causation_id": None,
                    "dedup_key": expected_dedup_key(evt_id, rule_version),
                    "payload": payload,
                    "provenance": {"source": "client"},
                }

                # 5. Ingest event
                hold = self._xp_hold()
                config = IngestConfig(rule_version=rule_version, clock=lambda: demo_clock, xp_hold=hold)
                t_ingest = time.perf_counter()
                res = ingest_lesson_completed(
                    app,
                    evidence,
                    envelope,
                    authenticated_producer="learning-svc",
                    config=config,
                )
                ingest_ms = (time.perf_counter() - t_ingest) * 1000.0
                project_ms = 0.0
                # Keep the exact envelope so it can be resubmitted byte for byte.
                self._store_sent_event(str(learner_id), "lesson", evt_id, envelope)

                # 6. Synchronous leaderboard projection reaction on accept
                if res.status == "accepted":
                    t_project = time.perf_counter()
                    _project_new_notifications(
                        app,
                        self.live_dir / "leaderboard.db",
                        self.live_dir / "demo_state.db",
                    )
                    project_ms = (time.perf_counter() - t_project) * 1000.0
                    _bump_version(self.root)
                abuse = _live_abuse_pass(app, evidence, self.live_dir, str(learner_id))
                if abuse["new_signals"]:
                    _bump_version(self.root)

                return {
                    "status": res.status,
                    "reason": res.reason,
                    "event_id": evt_id,
                    "timings_ms": {"ingest": round(ingest_ms, 2), "project": round(project_ms, 2)},
                    "abuse": abuse,
                    "xp_withheld": self._withheld(app, str(learner_id), res, envelope),
                }
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
                rule_version = self._effective_rule_version(learner, learner_id)
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
                    prefs = demo_notify.prefs_for(self.live_dir, str(learner_id), tz)
                    notif_store = NotificationStore(str(self.live_dir / "notifications.db"))
                    try:
                        dispatcher = demo_notify.build_dispatcher(
                            self.live_dir, notif_store, lambda: demo_clock
                        )
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
    register_decay_rulesets()  # idempotent; see Sandbox.__init__
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
        priya = overlay_app.repository.get(seed_learner_uuid(1))  # Priya Nair, the main persona
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
