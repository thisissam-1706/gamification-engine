"""Admin endpoints for the Cohorts tab (M3): membership, ranks, consistency, latency."""

import time

from fastapi import APIRouter

from src.state_machine.notification_log import read_all_notifications
from webapp import demo_admin, demo_backend, views


def _percentiles(samples: list[float]) -> dict:
    ordered = sorted(samples)

    def pick(q: float) -> float:
        index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
        return round(ordered[index], 3)

    return {"p50": pick(0.50), "p95": pick(0.95), "p99": pick(0.99), "max": round(ordered[-1], 3)}


def _learner_cohorts(sandbox, epoch: str) -> dict[str, dict]:
    """learner_id -> the cohort membership the learner's CohortAssigned event stored."""
    out: dict[str, dict] = {}
    profile = views.load_profile(sandbox.live_dir)
    with demo_admin.open_app(sandbox) as app:
        for learner_id in profile["learners"]:
            learner = demo_admin.get_learner(app, learner_id)
            out[learner_id] = dict(learner.cohort_memberships.get(epoch) or {})
    return out


def _rank_values(service, epoch: str, learner_id: str, cohort_id: str | None) -> dict:
    cohort = service.get_learner_rank("cohort", cohort_id, epoch, learner_id) if cohort_id else None
    glob = service.get_learner_rank("global", "global", epoch, learner_id)
    return {
        "cohort_rank": cohort.rank if cohort else None,
        "cohort_score": cohort.score if cohort else None,
        "global_rank": glob.rank if glob else None,
        "global_score": glob.score if glob else None,
        "first_reached_at": (cohort or glob).first_reached_at if (cohort or glob) else None,
    }


def create_cohorts_router(sandbox) -> APIRouter:
    router = APIRouter()

    @router.get("/api/demo/admin/cohorts")
    def cohorts() -> dict:
        profile = views.load_profile(sandbox.live_dir)
        epoch = profile["epoch"]
        memberships = _learner_cohorts(sandbox, epoch)
        by_cohort: dict[str, list[dict]] = {}
        with views._open_leaderboard(sandbox.live_dir) as service:
            for learner_id, info in profile["learners"].items():
                member = memberships.get(learner_id) or {}
                cohort_id = member.get("cohort_id")
                row = {
                    "display_name": info["display_name"],
                    "learner_id": learner_id,
                    "cohort_id": cohort_id,
                    "cohort_name": profile["cohort_names"].get(cohort_id, cohort_id),
                    "constraints": {
                        k: v for k, v in member.items() if k not in ("cohort_id",)
                    },
                }
                row.update(_rank_values(service, epoch, learner_id, cohort_id))
                by_cohort.setdefault(cohort_id or "unassigned", []).append(row)
        result = []
        for cohort_id, members in sorted(by_cohort.items(), key=lambda kv: str(kv[0])):
            members.sort(key=lambda m: (m["cohort_rank"] is None, m["cohort_rank"] or 0))
            result.append({
                "cohort_id": cohort_id,
                "name": profile["cohort_names"].get(cohort_id, cohort_id),
                "member_count": len(members),
                "members": members,
            })
        return {"epoch": epoch, "cohorts": result}

    @router.get("/api/demo/admin/cohorts/consistency")
    def consistency() -> dict:
        offset = demo_backend._get_last_offset(sandbox.live_dir / "demo_state.db")
        with demo_admin.open_app(sandbox) as app:
            notifications = read_all_notifications(app, start=1)
        head = notifications[-1].id if notifications else 0
        return {
            "projection_offset": offset,
            "log_head": head,
            "lag": head - offset,
            "mode": "synchronous in demo",
            "semantics": [
                "Rank order: highest XP this week first. A tie goes to the learner who reached that XP first, then to the lower learner id, so the order is always the same for the same events.",
                "A write is visible on the next read in this demo: after each accepted event the leaderboard is updated straight away, in the same request. Production targets p99 under 2 s from ingestion to visibility.",
                "Every update and its offset commit in one transaction, and a repeated delivery of the same event is ignored, so replaying events never double-counts XP.",
                "After a crash the projection resumes from its last committed offset and applies the log entries after it; entries it already applied are skipped.",
                "A rebuild replays the whole event log into an empty leaderboard and gives the same ranks and scores, because the log is the source of truth and the leaderboard is derived.",
            ],
        }

    @router.post("/api/demo/admin/cohorts/latency-probe")
    def latency_probe() -> dict:
        profile = views.load_profile(sandbox.live_dir)
        epoch = profile["epoch"]
        memberships = _learner_cohorts(sandbox, epoch)
        ids = list(profile["learners"])
        cohort_ids = sorted({m.get("cohort_id") for m in memberships.values() if m.get("cohort_id")})
        rank_ms: list[float] = []
        top_ms: list[float] = []
        with views._open_leaderboard(sandbox.live_dir) as service:
            for i in range(200):
                lid = ids[i % len(ids)]
                cohort_id = memberships[lid].get("cohort_id")
                scope = ("cohort", cohort_id) if (i % 2 == 0 and cohort_id) else ("global", "global")
                t = time.perf_counter()
                service.get_learner_rank(scope[0], scope[1], epoch, lid)
                rank_ms.append((time.perf_counter() - t) * 1000.0)
            for i in range(200):
                if i % 2 == 0 and cohort_ids:
                    scope = ("cohort", cohort_ids[(i // 2) % len(cohort_ids)])
                else:
                    scope = ("global", "global")
                t = time.perf_counter()
                service.get_top_k(scope[0], scope[1], epoch, 10)
                top_ms.append((time.perf_counter() - t) * 1000.0)
        return {"queries": 400, "rank_ms": _percentiles(rank_ms), "top_ms": _percentiles(top_ms)}

    @router.post("/api/demo/admin/learners/{learner_id}/cohorts/measure-update")
    def measure_update(learner_id: str) -> dict:
        demo_admin.learner_snapshot(sandbox, learner_id)  # 404 check
        profile = views.load_profile(sandbox.live_dir)
        epoch = profile["epoch"]

        def read() -> dict:
            cohort_id = (_learner_cohorts_one(sandbox, learner_id, epoch) or {}).get("cohort_id")
            with views._open_leaderboard(sandbox.live_dir) as service:
                values = _rank_values(service, epoch, learner_id, cohort_id)
            values["xp_total"] = demo_admin.learner_snapshot(sandbox, learner_id)["xp_total"]
            return values

        before = read()
        result = sandbox.submit_lesson_completed(learner_id, f"lab_rank_{int(time.time() * 1000)}")
        after = read()
        return {"result": result, "timings_ms": result.get("timings_ms"), "before": before, "after": after}

    return router


def _learner_cohorts_one(sandbox, learner_id: str, epoch: str) -> dict:
    with demo_admin.open_app(sandbox) as app:
        learner = demo_admin.get_learner(app, learner_id)
        return dict(learner.cohort_memberships.get(epoch) or {})
