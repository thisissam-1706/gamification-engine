"""M2 lab endpoints: duplicates, late events, rebuild-and-compare, rule-version switch.

These endpoints never hold demo_backend._submit_lock: the Sandbox methods they call take it.
"""

from datetime import timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.state_machine.projection import rebuild_learner_state
from src.state_machine.rules import RULESETS
from webapp import demo_admin


class LateRequest(BaseModel):
    hours_late: float


class RulesetRequest(BaseModel):
    version: str | None = None


def create_lab_router(sandbox) -> APIRouter:
    router = APIRouter()
    base = "/api/demo/admin/learners/{learner_id}/lab"

    def wrap(learner_id: str, call) -> dict:
        before = demo_admin.learner_snapshot(sandbox, learner_id)
        result = call()
        after = demo_admin.learner_snapshot(sandbox, learner_id)
        return {"result": result, "before": before, "after": after}

    @router.post(base + "/fresh-lesson")
    def fresh_lesson(learner_id: str) -> dict:
        demo_admin.learner_snapshot(sandbox, learner_id)  # 404 check
        n = sandbox.sent_event_count(learner_id) + 1
        return wrap(learner_id, lambda: sandbox.submit_lesson_completed(learner_id, f"lab_lesson_{n}"))

    @router.post(base + "/duplicate")
    def duplicate(learner_id: str) -> dict:
        demo_admin.learner_snapshot(sandbox, learner_id)
        return wrap(learner_id, lambda: sandbox.resubmit_last_lesson(learner_id))

    @router.post(base + "/late")
    def late(learner_id: str, request: LateRequest) -> dict:
        demo_admin.learner_snapshot(sandbox, learner_id)
        n = sandbox.sent_event_count(learner_id) + 1
        occurred = sandbox.get_clock(learner_id) - timedelta(hours=request.hours_late)
        payload = wrap(
            learner_id,
            lambda: sandbox.submit_lesson_completed(
                learner_id, f"lab_late_{n}", occurred_at=occurred
            ),
        )
        payload["hours_late"] = request.hours_late
        return payload

    @router.post(base + "/rebuild-compare")
    def rebuild_compare(learner_id: str) -> dict:
        uid = demo_admin.parse_learner(learner_id)
        with demo_admin.open_app(sandbox) as app:
            learner = demo_admin.get_learner(app, learner_id)
            events = list(app.events.get(uid))
            replay = rebuild_learner_state(events)
            live_quests = sorted(
                {str(q["quest_id"]) for q in learner.quest_instances.values()
                 if q.get("status") == "completed"}
            )
            rows = [
                ("xp_total", learner.xp_total, replay.xp_total),
                ("new_streak", learner.new_streak, replay.new_streak),
                ("freezes_remaining", learner.freezes_remaining, replay.freezes_remaining),
                ("activity_dates", sorted(learner.activity_dates), sorted(replay.activity_dates)),
                ("completed_quests", live_quests, sorted(replay.completed_quests)),
                ("badges", sorted(learner.badges_issued), sorted(replay.badges_issued or {})),
            ]
            out = [{"field": f, "live": live, "rebuilt": rebuilt, "match": live == rebuilt}
                   for f, live, rebuilt in rows]
            return {
                "rows": out,
                "identical": all(r["match"] for r in out),
                "events_replayed": len(events),
            }

    @router.get(base + "/ruleset")
    def get_ruleset_state(learner_id: str) -> dict:
        with demo_admin.open_app(sandbox) as app:
            learner = demo_admin.get_learner(app, learner_id)
            effective = sandbox._effective_rule_version(learner, learner_id)
        return {
            "override": sandbox.get_ruleset_override(learner_id),
            "effective": effective,
            "available": sorted(RULESETS),
        }

    @router.post(base + "/ruleset")
    def set_ruleset(learner_id: str, request: RulesetRequest) -> dict:
        demo_admin.learner_snapshot(sandbox, learner_id)
        if request.version is not None and request.version not in RULESETS:
            raise HTTPException(status_code=422, detail=f"Unknown rule version {request.version!r}")
        sandbox.set_ruleset_override(learner_id, request.version)
        return get_ruleset_state(learner_id)

    return router
