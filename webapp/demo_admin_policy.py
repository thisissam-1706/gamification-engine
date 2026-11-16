"""Admin endpoints for the Policy tab (M4): remediation decisions, quests and the guardrail."""

import dataclasses
from datetime import datetime
import time
from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel

from src.interventions.config import RemediationConfig
from src.interventions.service import run_quest_expiry
from webapp import demo_admin, demo_backend, views
from webapp.demo_admin_events import json_safe

DEFAULT_ASSESSMENT = "fractions_checkpoint_1"
REMEDIATION_PREFIX = "remediation_quest:"

_CONFIG_MEANING = {
    "frequency_window": "After a remediation quest is assigned, no further quest is assigned to the same learner for this long (measured from that decision).",
    "quest_deadline": "How long the learner has to finish an assigned remediation quest before the expiry sweep marks it expired.",
    "quest_id": "The quest definition that is assigned when a first failed attempt qualifies.",
    "quest_definition_version": "Version of the quest definition registry entry (target 1, counts only remediation completions).",
    "policy_version": "Version stamped on every stored decision.",
}


class CheckpointBody(BaseModel):
    passed: bool
    assessment_id: str | None = None


class SweepBody(BaseModel):
    learner_id: str


def _fmt_value(value):
    if hasattr(value, "total_seconds"):
        days = value.total_seconds() / 86400
        return f"{days:g} days"
    return str(value)


def _policy_view(learner) -> dict:
    attempts = sorted(
        learner.checkpoint_attempts.values(),
        key=lambda a: (str(a["occurred_at"]), str(a["attempt_id"])),
        reverse=True,
    )
    decisions = sorted(
        learner.remediation_decisions.values(),
        key=lambda d: (str(d["decided_at"]), str(d["decision_id"])),
        reverse=True,
    )
    quests = []
    for assignment_id, q in learner.quest_instances.items():
        if not assignment_id.startswith(REMEDIATION_PREFIX):
            continue
        quests.append({
            "assignment_id": assignment_id,
            "quest_id": q["quest_id"],
            "status": q["status"],
            "assigned_at": q["assigned_at"],
            "deadline_at": q["deadline_at"],
            "completed_at": q.get("completed_at"),
            "progress": q.get("progress"),
        })
    quests.sort(key=lambda q: str(q["assigned_at"]), reverse=True)
    return json_safe({
        "attempts": [
            {"attempt_id": a["attempt_id"], "assessment_id": a["assessment_id"],
             "passed": a["passed"], "occurred_at": a["occurred_at"]}
            for a in attempts
        ],
        "decisions": [
            {"decision_id": d["decision_id"], "attempt_id": d["attempt_id"],
             "assessment_id": d["assessment_id"], "decision": d["action"], "reason": d["reason"],
             "assignment_id": (REMEDIATION_PREFIX + d["attempt_id"]) if d["action"] == "assign" else None,
             "decided_at": d["decided_at"], "deadline_at": d["deadline_at"]}
            for d in decisions
        ],
        "quests": quests,
    })


def create_policy_router(sandbox) -> APIRouter:
    router = APIRouter()

    @router.get("/api/demo/admin/policy/config")
    def config() -> list[dict]:
        cfg = RemediationConfig()
        return [
            {"name": f.name, "value": _fmt_value(getattr(cfg, f.name)),
             "meaning": _CONFIG_MEANING.get(f.name, "")}
            for f in dataclasses.fields(cfg)
        ]

    @router.get("/api/demo/admin/learners/{learner_id}/policy")
    def policy(learner_id: str) -> dict:
        with demo_admin.open_app(sandbox) as app:
            return _policy_view(demo_admin.get_learner(app, learner_id))

    @router.post("/api/demo/admin/learners/{learner_id}/policy/checkpoint")
    def checkpoint(learner_id: str, body: CheckpointBody) -> dict:
        demo_admin.learner_snapshot(sandbox, learner_id)  # 404 check
        # Later attempts must sort after earlier ones when the demo clock has not moved,
        # because the "first attempt" rule orders by (occurred_at, attempt_id).
        attempt_id = f"att_{int(time.time() * 1000):013d}_{uuid4().hex[:6]}"
        result = sandbox.submit_checkpoint(
            learner_id, body.assessment_id or DEFAULT_ASSESSMENT, body.passed, attempt_id=attempt_id
        )
        with demo_admin.open_app(sandbox) as app:
            decisions = _policy_view(demo_admin.get_learner(app, learner_id))["decisions"]
        latest = decisions[0] if decisions else None
        return {"result": result, "attempt_id": attempt_id, "latest_decision": latest,
                "snapshot": demo_admin.learner_snapshot(sandbox, learner_id)}

    @router.post("/api/demo/admin/learners/{learner_id}/policy/complete-remediation")
    def complete_remediation(learner_id: str) -> dict:
        demo_admin.learner_snapshot(sandbox, learner_id)
        result = sandbox.submit_lesson_completed(
            learner_id, f"remediation_lesson_{int(time.time() * 1000)}", completion_kind="remediation"
        )
        return {"result": result, "snapshot": demo_admin.learner_snapshot(sandbox, learner_id)}

    @router.post("/api/demo/admin/policy/expiry-sweep")
    def expiry_sweep(body: SweepBody) -> dict:
        demo_admin.learner_snapshot(sandbox, body.learner_id)
        now = sandbox.get_clock(body.learner_id)
        profile = views.load_profile(sandbox.live_dir)
        expired: dict[str, list[str]] = {}
        with demo_backend._submit_lock:
            with demo_admin.open_app(sandbox) as app:
                for learner_id in profile["learners"]:
                    ids = run_quest_expiry(app, learner_id, clock=lambda: now)
                    if ids:
                        expired[learner_id] = ids
            if expired:
                demo_admin.catch_up_projection(sandbox)
                demo_backend._bump_version(sandbox.root)
        return json_safe({
            "now": now,
            "learners_swept": len(profile["learners"]),
            "learners_with_expiry": len(expired),
            "expired_count": sum(len(v) for v in expired.values()),
            "expired_quest_ids": [q for v in expired.values() for q in v],
            "by_learner": {profile["learners"][k]["display_name"]: v for k, v in expired.items()},
        })

    return router
