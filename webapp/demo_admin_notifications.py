"""Admin endpoints for the Notifications tab (M5).

Reads use read-only sqlite connections; writes hold demo_backend._submit_lock (these
endpoints never call Sandbox submit or clock methods) and bump the demo version.
"""

from contextlib import closing
from datetime import datetime
import sqlite3
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.notifications.models import (
    ChannelType,
    NotificationCategory,
    NotificationProposal,
)
from src.notifications.store import NotificationStore
from webapp import demo_admin, demo_backend, demo_notify, views

_TITLES = {
    "streak_risk": ("Keep your streak alive", "You have not practised today. One lesson keeps your streak going."),
    "remediation": ("Practice quest ready", "A short practice quest is waiting for you."),
    "quest_update": ("Quest update", "You are one step closer to finishing your quest."),
    "social_nudge": ("Your cohort is active", "Learners in your cohort are climbing the leaderboard."),
    "general": ("Hello from Streakwise", "Here is a message from the demo admin."),
}


class SendBody(BaseModel):
    channel: str
    category: str
    count: int = 1
    proposal_id: str | None = None
    repeat_same: bool = False


class OutageBody(BaseModel):
    channel: str
    active: bool


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _ro(live_dir):
    return sqlite3.connect(views._ro_uri(live_dir / "notifications.db") + "?mode=ro", uri=True)


def _rows(con, sql, params=()):
    cur = con.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def outage_state(live_dir) -> dict:
    return {
        "push": demo_notify.get_flag(live_dir, "outage_push") == "1",
        "email": demo_notify.get_flag(live_dir, "outage_email") == "1",
    }


def create_notifications_router(sandbox) -> APIRouter:
    router = APIRouter()
    live = sandbox.live_dir

    def check_learner(learner_id: str) -> None:
        demo_admin.learner_snapshot(sandbox, learner_id)

    @router.get("/api/demo/admin/learners/{learner_id}/notifications")
    def timeline(learner_id: str) -> dict:
        check_learner(learner_id)
        items: list[dict] = []
        counts = {"delivered": 0, "suppressed": 0, "deferred": 0,
                  "dead_letter_open": 0, "dead_letter_resolved": 0}
        # Create schema (and the file) if a fresh sandbox has not opened the store yet.
        NotificationStore(str(live / "notifications.db")).close()
        with closing(_ro(live)) as con:
            seen: set[str] = set()
            for r in _rows(con, "SELECT * FROM notification_dispatches WHERE learner_id = ?", (learner_id,)):
                seen.add(r["proposal_id"])
                counts["delivered"] += 1
                items.append({"time": r["dispatched_at"], "proposal_id": r["proposal_id"],
                              "channel": r["channel"], "category": r["category"],
                              "outcome": "delivered", "reason": None, "attempts": None,
                              "dlq_id": None, "resolved": None, "deliver_at": None})
            for r in _rows(con, "SELECT * FROM notification_suppressions WHERE learner_id = ?", (learner_id,)):
                seen.add(r["proposal_id"])
                counts["suppressed"] += 1
                items.append({"time": r["suppressed_at"], "proposal_id": r["proposal_id"],
                              "channel": r["channel"], "category": r["category"],
                              "outcome": "suppressed", "reason": r["reason"], "attempts": None,
                              "dlq_id": None, "resolved": None, "deliver_at": None})
            for r in _rows(con, "SELECT * FROM notification_rescheduled WHERE learner_id = ?", (learner_id,)):
                seen.add(r["proposal_id"])
                if not r["released"]:
                    counts["deferred"] += 1
                items.append({"time": r["created_at"], "proposal_id": r["proposal_id"],
                              "channel": r["channel"], "category": r["category"],
                              "outcome": "deferred",
                              "reason": "quiet_hours_rescheduled" + (" (released)" if r["released"] else ""),
                              "attempts": None, "dlq_id": None, "resolved": None,
                              "deliver_at": r["rescheduled_for"]})
            for r in _rows(con, "SELECT * FROM notification_dlq WHERE learner_id = ?", (learner_id,)):
                seen.add(r["proposal_id"])
                counts["dead_letter_resolved" if r["resolved"] else "dead_letter_open"] += 1
                items.append({"time": r["failed_at"], "proposal_id": r["proposal_id"],
                              "channel": r["channel"], "category": r["category"],
                              "outcome": "dead_letter", "reason": r["reason"],
                              "attempts": r["attempts"], "dlq_id": r["dlq_id"],
                              "resolved": bool(r["resolved"]), "deliver_at": None})
            for r in _rows(con, "SELECT * FROM notification_proposals WHERE learner_id = ?", (learner_id,)):
                if r["proposal_id"] in seen:
                    continue
                items.append({"time": r["created_at"], "proposal_id": r["proposal_id"],
                              "channel": r["channel"], "category": r["category"],
                              "outcome": "queued", "reason": r["status"], "attempts": None,
                              "dlq_id": None, "resolved": None, "deliver_at": None})
        items.sort(key=lambda i: str(i["time"]), reverse=True)
        store = NotificationStore(":memory:")
        try:
            dispatcher = demo_notify.build_dispatcher(live, store, lambda: sandbox.get_clock(learner_id))
            quiet = {"start": dispatcher.quiet_start_hour, "end": dispatcher.quiet_end_hour}
        finally:
            store.close()
        prefs = demo_notify.prefs_for(live, learner_id)
        return {
            "items": items, "counts": counts, "outage": outage_state(live), "quiet_hours": quiet,
            "opted_out": sorted(c.value for c in prefs.opted_out_channels),
            "clock": _iso(sandbox.get_clock(learner_id)),
        }

    @router.post("/api/demo/admin/learners/{learner_id}/notifications/send")
    def send(learner_id: str, body: SendBody) -> dict:
        check_learner(learner_id)
        try:
            channel = ChannelType(body.channel)
            category = NotificationCategory(body.category)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        count = max(1, min(int(body.count), 20))
        clock = sandbox.get_clock(learner_id)
        title, text = _TITLES.get(category.value, _TITLES["general"])
        prefs = demo_notify.prefs_for(live, learner_id)
        # The simulated email provider needs a recipient address (payload["to"]).
        info = views.load_profile(live)["learners"][learner_id]
        recipient = info["display_name"].lower().replace(" ", ".") + "@learner.example"
        facts = []
        with demo_backend._submit_lock:
            store = NotificationStore(str(live / "notifications.db"))
            try:
                dispatcher = demo_notify.build_dispatcher(live, store, lambda: clock)
                for _ in range(count):
                    pid = body.proposal_id or f"demo:{uuid4().hex[:10]}"
                    proposal = NotificationProposal(
                        proposal_id=pid, learner_id=learner_id, channel=channel,
                        category=category, template_id="demo_admin_v1",
                        payload={"title": title, "body": text, "to": recipient}, created_at=_iso(clock),
                    )
                    repeats = 2 if body.repeat_same else 1
                    for _ in range(repeats):
                        fact = dispatcher.dispatch(proposal, prefs=prefs, now_utc=clock)
                        facts.append({
                            "status": fact.status.value, "reason": fact.reason,
                            "channel": fact.channel, "proposal_id": fact.proposal_id,
                            "attempt_count": fact.attempt_count,
                            "rescheduled_for": fact.rescheduled_for,
                        })
            finally:
                store.close()
            demo_backend._bump_version(sandbox.root)
        return {"facts": facts}

    @router.post("/api/demo/admin/learners/{learner_id}/notifications/outage")
    def outage(learner_id: str, body: OutageBody) -> dict:
        check_learner(learner_id)
        if body.channel not in ("push", "email"):
            raise HTTPException(status_code=422, detail="channel must be push or email")
        with demo_backend._submit_lock:
            demo_notify.set_flag(live, f"outage_{body.channel}", "1" if body.active else None)
            demo_backend._bump_version(sandbox.root)
        return outage_state(live)

    @router.post("/api/demo/admin/learners/{learner_id}/notifications/replay-dlq")
    def replay_dlq(learner_id: str) -> list[dict]:
        check_learner(learner_id)
        out = []
        with demo_backend._submit_lock:
            store = NotificationStore(str(live / "notifications.db"))
            try:
                dispatcher = demo_notify.build_dispatcher(live, store, lambda: sandbox.get_clock(learner_id))
                for entry in store.get_dlq_entries(resolved=False):
                    lid = entry["learner_id"]
                    try:
                        fact = dispatcher.replay_dlq(
                            entry["dlq_id"], prefs=demo_notify.prefs_for(live, lid),
                            now_utc=sandbox.get_clock(lid),
                        )
                        out.append({"dlq_id": entry["dlq_id"], "status": fact.status.value, "reason": fact.reason})
                    except ValueError as exc:
                        out.append({"dlq_id": entry["dlq_id"], "status": "error", "reason": str(exc)})
            finally:
                store.close()
            demo_backend._bump_version(sandbox.root)
        return out

    @router.post("/api/demo/admin/learners/{learner_id}/notifications/release-deferred")
    def release_deferred(learner_id: str) -> list[dict]:
        check_learner(learner_id)
        now = sandbox.get_clock(learner_id)
        with demo_backend._submit_lock:
            store = NotificationStore(str(live / "notifications.db"))
            try:
                dispatcher = demo_notify.build_dispatcher(live, store, lambda: now)
                facts = dispatcher.release_rescheduled(
                    now_utc=now, prefs_provider=lambda lid: demo_notify.prefs_for(live, lid)
                )
            finally:
                store.close()
            demo_backend._bump_version(sandbox.root)
        return [{"proposal_id": f.proposal_id, "status": f.status.value, "reason": f.reason} for f in facts]

    return router
