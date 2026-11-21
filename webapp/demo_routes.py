"""HTTP routes for the writable demo sandbox."""

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import logging
import threading
import time

from webapp.demo_backend import Sandbox
from webapp import demo_backend, demo_clock_jobs, demo_notify, views
from src.notifications.store import NotificationStore

log = logging.getLogger(__name__)


class ClockRequest(BaseModel):
    now_utc: datetime


class LessonRequest(BaseModel):
    lesson_id: str
    completion_kind: str = "lesson"
    event_id: str | None = None
    occurred_at: str | None = None
    completion_id: str | None = None
    # The lesson page's score; more than half wrong earns no XP.
    questions_correct: int | None = None
    questions_total: int | None = None


class FreezeRequest(BaseModel):
    frozen: bool


class CheckpointRequest(BaseModel):
    assessment_id: str
    passed: bool


def _ensure_learner(sandbox: Sandbox, learner_id: str) -> None:
    profile = views.load_profile(sandbox.live_dir)
    if learner_id not in profile["learners"]:
        raise HTTPException(status_code=404, detail=f"No learner with id {learner_id!r}")


def _result_or_404(result: dict) -> dict:
    if result.get("status") == "rejected" and result.get("reason") == "learner_not_found":
        raise HTTPException(status_code=404, detail="Learner not found")
    return result


class _LearnerScopedStore:
    """Notification store view that only reports one learner's due deferred items.

    Each demo learner has their own clock, so a clock move must only release that learner's
    deferred messages and never another learner's.
    """

    def __init__(self, store, learner_id: str) -> None:
        self._store = store
        self._learner_id = str(learner_id)

    def get_due_rescheduled(self, now_iso):
        return [i for i in self._store.get_due_rescheduled(now_iso)
                if str(i["learner_id"]) == self._learner_id]

    def __getattr__(self, name):
        return getattr(self._store, name)


def _release_due_deferred(sandbox: Sandbox, learner_id: str) -> None:
    """Stand-in for the scheduler: deliver this learner's deferred messages that are now due."""
    now = sandbox.get_clock(learner_id)
    with demo_backend._submit_lock:
        store = NotificationStore(str(sandbox.live_dir / "notifications.db"))
        try:
            dispatcher = demo_notify.build_dispatcher(
                sandbox.live_dir, _LearnerScopedStore(store, learner_id), lambda: now
            )
            facts = dispatcher.release_rescheduled(
                now_utc=now,
                prefs_provider=lambda lid: demo_notify.prefs_for(sandbox.live_dir, lid),
            )
        finally:
            store.close()
        if facts:
            demo_backend._bump_version(sandbox.root)


def _run_clock_jobs(sandbox: Sandbox, learner_id: str, quiet: bool = False) -> None:
    """Everything the scheduler would do for this learner as of their current demo clock."""
    jobs = (_release_due_deferred, demo_clock_jobs.send_due_streak_reminders,
            demo_clock_jobs.recover_dead_letters)
    for job in jobs:
        try:
            job(sandbox, learner_id)
        except Exception:  # one failing job must not stop the others or block the clock
            (log.debug if quiet else log.warning)(
                "demo: clock job %s failed", job.__name__, exc_info=True)


def _release_loop(sandbox: Sandbox) -> None:
    """Scheduler stand-in: as running clocks pass a deferred message's time, deliver it."""
    while True:
        time.sleep(3)
        try:
            learner_ids = list(views.load_profile(sandbox.live_dir)["learners"])
            for learner_id in learner_ids:
                if not sandbox.get_clock_state(learner_id)["frozen"]:
                    _run_clock_jobs(sandbox, learner_id, quiet=True)
        except Exception:  # sandbox may be mid-reset; try again on the next pass
            log.debug("demo: release loop pass skipped", exc_info=True)


def create_demo_router(sandbox: Sandbox) -> APIRouter:
    router = APIRouter()
    if sandbox.live_default:
        threading.Thread(target=_release_loop, args=(sandbox,), daemon=True, name="demo-release").start()

    @router.get("/api/demo/version")
    def get_version() -> dict[str, int]:
        return {"version": sandbox.version}

    @router.post("/api/demo/learners/{learner_id}/clock")
    def set_clock(learner_id: str, request: ClockRequest) -> dict:
        _ensure_learner(sandbox, learner_id)
        if request.now_utc.tzinfo is None or request.now_utc.utcoffset() is None:
            raise HTTPException(status_code=422, detail="now_utc must include a timezone")
        sandbox.set_clock(learner_id, request.now_utc)
        _run_clock_jobs(sandbox, learner_id)
        return sandbox.get_clock_state(learner_id)

    @router.get("/api/demo/learners/{learner_id}/clock")
    def get_clock(learner_id: str) -> dict:
        _ensure_learner(sandbox, learner_id)
        return sandbox.get_clock_state(learner_id)

    @router.post("/api/demo/learners/{learner_id}/clock/freeze")
    def freeze_clock(learner_id: str, request: FreezeRequest) -> dict:
        _ensure_learner(sandbox, learner_id)
        sandbox.set_frozen(learner_id, request.frozen)
        return sandbox.get_clock_state(learner_id)

    @router.post("/api/demo/learners/{learner_id}/lesson-complete")
    def submit_lesson(learner_id: str, request: LessonRequest) -> dict:
        return _result_or_404(
            sandbox.submit_lesson_completed(
                learner_id,
                request.lesson_id,
                completion_kind=request.completion_kind,
                event_id=request.event_id,
                occurred_at=request.occurred_at,
                completion_id=request.completion_id,
                questions_correct=request.questions_correct,
                questions_total=request.questions_total,
            )
        )

    @router.post("/api/demo/learners/{learner_id}/checkpoint")
    def submit_checkpoint(learner_id: str, request: CheckpointRequest) -> dict:
        return _result_or_404(
            sandbox.submit_checkpoint(
                learner_id,
                request.assessment_id,
                request.passed,
            )
        )

    @router.post("/api/demo/reset")
    def reset() -> dict[str, str]:
        sandbox.reset()
        return {"status": "reset"}

    return router
