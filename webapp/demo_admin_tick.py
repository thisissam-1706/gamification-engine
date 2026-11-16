"""Admin endpoint that runs the engine's scheduled jobs once (scripts/run_engine_tick.py)."""

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.abuse.store import AbuseSignalStore
from src.experiments.store import ExperimentStore
from src.ingestion.evidence import EvidenceStore
from src.leaderboard.projection import LeaderboardProjection
from src.leaderboard.store import SQLiteLeaderboardStore
from src.notifications.store import NotificationStore
from src.scale_and_recovery.rebuild import SYNTHETIC_MIN_COHORT_SIZE
from src.state_machine.learner import LearnerApplication
from webapp import demo_admin, demo_backend, demo_notify, views
from webapp.demo_admin_events import json_safe

REPO_ROOT = Path(__file__).resolve().parents[1]


class TickBody(BaseModel):
    now: str | None = None


def _load_run_engine_tick():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    spec = importlib.util.spec_from_file_location(
        "demo_script_run_engine_tick", REPO_ROOT / "scripts" / "run_engine_tick.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _headline(name: str, s: dict) -> str:
    if "error" in s:
        return "failed: " + str(s["error"])
    if name == "weekly_cycle":
        return (f"Cohorts for {s['epoch']}: {s['assignments_built']} learners placed, "
                f"{s['finalized']} cohorts finalized")
    if name == "assign":
        return (f"{s['assigned']} newly assigned to exp_007, {s['already_assigned']} already in, "
                f"{s['ineligible']} ineligible")
    if name == "poll":
        return f"{s['applied']} log entries applied to the leaderboard"
    if name == "quest_expiry":
        return f"{s['expired']} quests expired across {s['learners_checked']} learners"
    if name == "remediation":
        return (f"{s['decisions_made']} new decisions, {s['assigned']} quests assigned, "
                f"{s['notices_dispatched']} notices sent")
    if name == "streak_risk":
        skipped = s.get("skipped", {})
        return (f"{s['sent']} reminders sent, {skipped.get('not_treatment', 0)} held out as control, "
                f"{sum(skipped.values())} skipped in total")
    if name == "abuse":
        return f"{s['new_signals']} new signals, {s['existing_signals']} already known"
    if name == "reconcile":
        return f"{s['scopes']} leaderboard scopes audited, {s['repairs']} repairs"
    return ""


def create_tick_router(sandbox) -> APIRouter:
    router = APIRouter()
    live = sandbox.live_dir

    @router.post("/api/demo/admin/tick")
    def tick(body: TickBody) -> dict:
        if body.now:
            try:
                now = datetime.fromisoformat(body.now.replace("Z", "+00:00"))
            except ValueError:
                raise HTTPException(status_code=422, detail="now must be an ISO-8601 timestamp")
            if now.tzinfo is None or now.utcoffset() is None:
                raise HTTPException(status_code=422, detail="now must include a timezone")
        else:
            now = demo_admin.global_clock(sandbox)
        module = _load_run_engine_tick()
        profile = views.load_profile(live)
        prefs = {lid: demo_notify.prefs_for(live, lid) for lid in profile["learners"]}
        with demo_backend._submit_lock:
            opened = []
            try:
                app = LearnerApplication(
                    env={"PERSISTENCE_MODULE": "eventsourcing.sqlite",
                         "SQLITE_DBNAME": str(live / "events.db")}
                )
                opened.append(app.close)
                board = SQLiteLeaderboardStore(str(live / "leaderboard.db"))
                opened.append(board.close)
                notif = NotificationStore(str(live / "notifications.db"))
                opened.append(notif.close)
                evidence = EvidenceStore(str(live / "evidence.db"))
                opened.append(evidence.close)
                signals = AbuseSignalStore(str(live / "abuse.db"))
                opened.append(signals.close)
                experiments = ExperimentStore(str(live / "experiments.db"))
                opened.append(experiments.close)
                result = module.run_tick(
                    app, LeaderboardProjection(board),
                    demo_notify.build_dispatcher(live, notif, lambda: now),
                    evidence, signals, now=now, prefs_by_learner=prefs,
                    experiment_store=experiments,
                    cohort_min_size=SYNTHETIC_MIN_COHORT_SIZE, cohort_max_size=5000,
                )
            finally:
                for close in reversed(opened):
                    close()
            # Catch the demo's own projection offset up with anything the tick wrote.
            with demo_admin.open_app(sandbox) as fresh:
                demo_backend._project_new_notifications(
                    fresh, live / "leaderboard.db", live / "demo_state.db"
                )
            demo_backend._bump_version(sandbox.root)
        steps = [
            {"name": name, "ok": "error" not in result["steps"][name],
             "headline": _headline(name, result["steps"][name]), "raw": result["steps"][name]}
            for name in module.STEP_NAMES
        ]
        return json_safe({"now": demo_admin.iso_z(now.astimezone(timezone.utc)),
                          "summary": result["summary"], "steps": steps})

    return router
