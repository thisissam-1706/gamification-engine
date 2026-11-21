"""Admin endpoints for the Recovery tab (M8): leaderboard wipe and rebuild, dead letters, benchmark."""

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.leaderboard.projection import LeaderboardProjection
from src.leaderboard.store import SQLiteLeaderboardStore
from src.notifications.store import NotificationStore
from src.scale_and_recovery.rebuild import read_all_notifications
from webapp import demo_admin, demo_backend, demo_notify, views

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = REPO_ROOT / "docs" / "evidence"

# Not part of the checksum: leaderboard_reconciliation_audits is an audit log written by the
# reconciler (engine tick), not derived from events, so a rebuild cannot recreate it. Every other
# column, including last_updated_at / applied_at / processed_at, is derived from event times and
# offsets and must match after a rebuild (verified empirically by the wipe-and-rebuild smoke check).
EXCLUDED_TABLES: set[str] = {"leaderboard_reconciliation_audits"}
EXCLUDED_COLUMNS: dict[str, set[str]] = {}


class BenchmarkBody(BaseModel):
    profile: str = "small"


def _ro(path: Path):
    return sqlite3.connect(views._ro_uri(path) + "?mode=ro", uri=True)


def leaderboard_fingerprint(db_path: Path) -> dict:
    """sha256 over every table of the leaderboard database (minus the documented exclusions)."""
    digest = hashlib.sha256()
    tables: dict[str, int] = {}
    with closing(_ro(Path(db_path))) as con:
        names = sorted(
            r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        )
        for name in names:
            if name in EXCLUDED_TABLES:
                continue
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{name}")')]
            keep = [c for c in cols if c not in EXCLUDED_COLUMNS.get(name, set())]
            if not keep:
                continue
            quoted = ", ".join(f'"{c}"' for c in keep)
            rows = con.execute(f'SELECT {quoted} FROM "{name}" ORDER BY {quoted}').fetchall()
            tables[name] = len(rows)
            digest.update(name.encode("utf-8"))
            digest.update(json.dumps(rows, sort_keys=True, default=str).encode("utf-8"))
    return {"hash": digest.hexdigest(), "tables": tables, "rows": sum(tables.values())}


def do_wipe(sandbox) -> dict:
    live = sandbox.live_dir
    lb = live / "leaderboard.db"
    with demo_backend._submit_lock:
        demo_admin.catch_up_projection(sandbox)  # compare a read model that is current with the log
        before = leaderboard_fingerprint(lb)
        demo_notify.set_flag(live, "recovery_pre_hash", before["hash"])
        for suffix in ("", "-wal", "-shm"):
            path = lb.with_name(lb.name + suffix)
            if path.exists():
                path.unlink()
        SQLiteLeaderboardStore(str(lb)).close()  # recreate the empty schema
        demo_backend._set_last_offset(live / "demo_state.db", 0)
        demo_backend._bump_version(sandbox.root)
    return {"before": before, "wiped": True}


def do_rebuild(sandbox) -> dict:
    live = sandbox.live_dir
    lb = live / "leaderboard.db"
    with demo_backend._submit_lock:
        store = SQLiteLeaderboardStore(str(lb))
        try:
            with demo_admin.open_app(sandbox) as app:
                projection = LeaderboardProjection(store)
                t0 = time.perf_counter()
                notifications = read_all_notifications(app, start=1)
                for n in notifications:
                    projection.apply(n, offset=n.id)
                duration = max(time.perf_counter() - t0, 1e-6)
        finally:
            store.close()
        last = notifications[-1].id if notifications else 0
        demo_backend._set_last_offset(live / "demo_state.db", last)
        after = leaderboard_fingerprint(lb)
        before_hash = demo_notify.get_flag(live, "recovery_pre_hash")
        demo_backend._bump_version(sandbox.root)
    return {
        "events_replayed": len(notifications), "duration_ms": round(duration * 1000, 2),
        "events_per_sec": round(len(notifications) / duration, 1), "after": after,
        "before_hash": before_hash, "identical": before_hash is not None and before_hash == after["hash"],
    }


def _load_script(name: str):
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    spec = importlib.util.spec_from_file_location(f"demo_script_{name}", REPO_ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _reports() -> list[dict]:
    if not EVIDENCE_DIR.is_dir():
        return []
    out = []
    for path in sorted(EVIDENCE_DIR.iterdir()):
        if path.is_file() and path.name.startswith("scale") and path.name.endswith(".json"):
            stat = path.stat()
            out.append({
                "name": path.name, "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })
    return out


def create_recovery_router(sandbox) -> APIRouter:
    router = APIRouter()
    live = sandbox.live_dir

    @router.post("/api/demo/admin/recovery/wipe-leaderboard")
    def wipe() -> dict:
        return do_wipe(sandbox)

    @router.post("/api/demo/admin/recovery/rebuild-leaderboard")
    def rebuild() -> dict:
        return do_rebuild(sandbox)

    @router.post("/api/demo/admin/recovery/wipe-and-rebuild")
    def wipe_and_rebuild() -> dict:
        return {"wipe": do_wipe(sandbox), "rebuild": do_rebuild(sandbox)}

    @router.get("/api/demo/admin/recovery/dead-letters")
    def dead_letters() -> dict:
        NotificationStore(str(live / "notifications.db")).close()
        profile = views.load_profile(live)
        names = {lid: i["display_name"] for lid, i in profile["learners"].items()}
        with closing(_ro(live / "notifications.db")) as con:
            rows = con.execute(
                "SELECT learner_id, channel, reason, attempts, failed_at, resolved FROM notification_dlq "
                "ORDER BY failed_at DESC"
            ).fetchall()
        return {
            "open": sum(1 for r in rows if not r[5]), "resolved": sum(1 for r in rows if r[5]),
            "entries": [
                {"learner": names.get(r[0], r[0][:8]), "channel": r[1], "reason": r[2],
                 "attempts": r[3], "failed_at": r[4], "resolved": bool(r[5])}
                for r in rows[:10]
            ],
        }

    @router.post("/api/demo/admin/recovery/benchmark")
    def benchmark(body: BenchmarkBody) -> dict:
        if body.profile != "small":
            raise HTTPException(status_code=422, detail="Only the small profile runs live; open saved reports for larger ones")
        module = _load_script("run_scale_benchmark")
        return module.run_benchmark(profile_name="small", seed=42)

    @router.get("/api/demo/admin/recovery/reports")
    def reports() -> list[dict]:
        return _reports()

    @router.get("/api/demo/admin/recovery/reports/{name}")
    def report(name: str):
        match = next((r for r in _reports() if r["name"] == name), None)
        if match is None:
            raise HTTPException(status_code=404, detail="No such report")
        return json.loads((EVIDENCE_DIR / match["name"]).read_text(encoding="utf-8"))

    return router
