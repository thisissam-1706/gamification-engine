"""Admin API for the demo sandbox (only mounted when DEMO_MODE=1).

# Rules for admin modules
# 1. Every write endpoint bumps the demo version with demo_backend._bump_version(sandbox.root).
# 2. demo_backend._submit_lock is a non-reentrant threading.Lock. An endpoint that calls
#    Sandbox.submit_lesson_completed, submit_checkpoint, resubmit_event, resubmit_last_lesson
#    or set_clock must NOT hold the lock itself, because those methods take it. An endpoint
#    that does not call them may hold the lock for its whole body.
# 3. Open and close every SQLite connection, store and LearnerApplication inside the request,
#    closing them in `finally`.
# 4. All admin routes exist only when DEMO_MODE=1 (they are included from the `if DEMO_MODE:`
#    block of webapp/app.py, before the static mount).
"""

from contextlib import contextmanager
from datetime import datetime
import sqlite3
from uuid import UUID

from eventsourcing.application import AggregateNotFoundError
from fastapi import APIRouter, HTTPException

from src.state_machine.learner import LearnerApplication
from webapp import demo_backend, demo_personas, views
from webapp.seed import AS_OF


@contextmanager
def open_app(sandbox):
    app = LearnerApplication(
        env={
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(sandbox.live_dir / "events.db"),
        }
    )
    try:
        yield app
    finally:
        app.close()


def parse_learner(learner_id: str) -> UUID:
    try:
        return UUID(str(learner_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=404, detail=f"No learner with id {learner_id!r}")


def get_learner(app, learner_id: str):
    """Returns the Learner aggregate or raises HTTP 404."""
    uid = parse_learner(learner_id)
    try:
        return app.repository.get(uid)
    except AggregateNotFoundError:
        raise HTTPException(status_code=404, detail=f"No learner with id {learner_id!r}")


def learner_snapshot(sandbox, learner_id: str) -> dict:
    with open_app(sandbox) as app:
        learner = get_learner(app, learner_id)
        return {
            "xp_total": learner.xp_total,
            "streak": learner.new_streak,
            "freezes": learner.freezes_remaining,
            "active_days": len(learner.activity_dates),
            "ruleset": views.learner_rule_version(learner),
        }


def catch_up_projection(sandbox) -> None:
    """Applies any event-log entries the demo leaderboard has not seen yet (call under the lock).

    Admin endpoints that write events directly (assign, expiry sweep, sweep) use this so the
    leaderboard and the demo projection offset never lag behind the log.
    """
    with open_app(sandbox) as app:
        demo_backend._project_new_notifications(
            app, sandbox.live_dir / "leaderboard.db", sandbox.live_dir / "demo_state.db"
        )


def iso_z(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def global_clock(sandbox) -> datetime:
    """Latest learner clock (running clocks included), or seed AS_OF when none is set."""
    con = sqlite3.connect(str(sandbox.live_dir / "demo_state.db"))
    try:
        ids = [r[0] for r in con.execute("SELECT learner_id FROM learner_clock")]
    except sqlite3.OperationalError:
        ids = []
    finally:
        con.close()
    if not ids:
        return demo_backend._parse_iso_utc(AS_OF)
    return max(sandbox.get_clock(learner_id) for learner_id in ids)


def create_admin_router(sandbox) -> APIRouter:
    router = APIRouter()

    @router.get("/api/demo/personas")
    def personas() -> list[dict]:
        return demo_personas.personas_payload(sandbox.live_dir)

    @router.get("/api/demo/admin/learners/{learner_id}/snapshot")
    def snapshot(learner_id: str) -> dict:
        return learner_snapshot(sandbox, learner_id)

    @router.get("/api/demo/admin/clock")
    def clock() -> dict:
        return {"now_utc": iso_z(global_clock(sandbox))}

    from webapp.demo_admin_events import create_events_router

    router.include_router(create_events_router(sandbox))

    from webapp.demo_admin_lab import create_lab_router

    router.include_router(create_lab_router(sandbox))

    from webapp.demo_admin_cohorts import create_cohorts_router

    router.include_router(create_cohorts_router(sandbox))

    from webapp.demo_admin_policy import create_policy_router

    router.include_router(create_policy_router(sandbox))

    from webapp.demo_admin_notifications import create_notifications_router

    router.include_router(create_notifications_router(sandbox))

    from webapp.demo_admin_abuse import create_abuse_router

    router.include_router(create_abuse_router(sandbox))

    from webapp.demo_admin_experiments import create_experiments_router

    router.include_router(create_experiments_router(sandbox))

    from webapp.demo_admin_recovery import create_recovery_router

    router.include_router(create_recovery_router(sandbox))

    from webapp.demo_admin_tick import create_tick_router

    router.include_router(create_tick_router(sandbox))
    return router
