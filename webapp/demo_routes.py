"""HTTP routes for the writable demo sandbox."""

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from webapp.demo_backend import Sandbox
from webapp import views


class ClockRequest(BaseModel):
    now_utc: datetime


class LessonRequest(BaseModel):
    lesson_id: str
    completion_kind: str = "lesson"


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


def create_demo_router(sandbox: Sandbox) -> APIRouter:
    router = APIRouter()

    @router.get("/api/demo/version")
    def get_version() -> dict[str, int]:
        return {"version": sandbox.version}

    @router.post("/api/demo/learners/{learner_id}/clock")
    def set_clock(learner_id: str, request: ClockRequest) -> dict[str, str]:
        _ensure_learner(sandbox, learner_id)
        if request.now_utc.tzinfo is None or request.now_utc.utcoffset() is None:
            raise HTTPException(status_code=422, detail="now_utc must include a timezone")
        sandbox.set_clock(learner_id, request.now_utc)
        return {"now_utc": sandbox.get_clock(learner_id).isoformat().replace("+00:00", "Z")}

    @router.get("/api/demo/learners/{learner_id}/clock")
    def get_clock(learner_id: str) -> dict[str, str]:
        _ensure_learner(sandbox, learner_id)
        return {"now_utc": sandbox.get_clock(learner_id).isoformat().replace("+00:00", "Z")}

    @router.post("/api/demo/learners/{learner_id}/lesson-complete")
    def submit_lesson(learner_id: str, request: LessonRequest) -> dict:
        return _result_or_404(
            sandbox.submit_lesson_completed(
                learner_id,
                request.lesson_id,
                completion_kind=request.completion_kind,
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
