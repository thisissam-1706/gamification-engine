"""Admin endpoints for the Notifications tab (M5).

Reads use read-only sqlite connections; writes hold demo_backend._submit_lock (these
endpoints never call Sandbox submit or clock methods) and bump the demo version.
"""

from contextlib import closing
from dataclasses import replace
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
from src.notifications.guardrails import daily_cap_usage
from src.notifications.quest_updates import build_quest_update
from src.notifications.store import NotificationStore
from webapp import demo_admin, demo_backend, demo_notify, views

_TITLES = {
    "streak_risk": ("Keep your streak alive", "You have not practised today. One lesson keeps your streak going."),
    "remediation": ("Practice quest ready", "A short practice quest is waiting for you."),
    # quest_update text is built from the learner's active quest (src/notifications/quest_updates.py).
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


class EmailAccountBody(BaseModel):
    host: str
    port: int = 587
    user: str
    password: str
    sender: str | None = None
    secure: bool = False
    web: str | None = None
    provider: str = "smtp"


def learner_email(live_dir, learner_id: str) -> str:
    """The learner's fake address (the .example domain is reserved and never delivers)."""
    info = views.load_profile(live_dir)["learners"][learner_id]
    return info["display_name"].lower().replace(" ", ".") + "@learner.example"


def destinations(live_dir) -> dict:
    account = demo_notify.load_email_account(live_dir)
    return {
        "in_app": "The learner's inbox on the Learner home page.",
        "push": "A simulated phone (no real push provider is connected; that needs Firebase/APNs and "
                "a device token). Delivered pushes appear under 'Delivered to phone and email' below.",
        "email": (
            f"Real SMTP to {account['host']}:{account['port']} ({account.get('provider', 'smtp')}). It is a "
            "fake inbox: mail is captured and shown there, never delivered to anyone."
            if account else
            "Local outbox only (no email account configured). Create a free Ethereal fake inbox below "
            "to send real emails you can open in a browser."
        ),
        "email_account": demo_notify.public_account(account),
    }


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
        now = sandbox.get_clock(learner_id)
        with closing(_ro(live)) as con:
            sent_rows = con.execute(
                "SELECT channel, dispatched_at FROM notification_dispatches WHERE learner_id = ?",
                (learner_id,)).fetchall()
        # The demo caps each channel separately (5 per day each).
        per_channel = {}
        for ch in ("in_app", "push", "email"):
            used_ch, day_start, next_reset = daily_cap_usage(
                [at for c, at in sent_rows if c == ch], now, prefs.timezone)
            per_channel[ch] = used_ch
        used = max(per_channel.values())
        return {
            "items": items, "counts": counts, "outage": outage_state(live), "quiet_hours": quiet,
            "opted_out": sorted(c.value for c in prefs.opted_out_channels),
            "clock": _iso(now),
            "cap": {"used": used, "limit": 5, "per_channel": per_channel, "day_start": _iso(day_start), "resets_at": _iso(next_reset),
                    "timezone": prefs.timezone},
            "where": destinations(live),
            "email_address": learner_email(live, learner_id),
        }

    @router.get("/api/demo/admin/learners/{learner_id}/notifications/outbox")
    def outbox(learner_id: str) -> dict:
        check_learner(learner_id)
        return {"messages": demo_notify.list_outbox(live, learner_id),
                "email_address": learner_email(live, learner_id)}

    @router.get("/api/demo/admin/email/account")
    def email_account() -> dict:
        return {"account": demo_notify.public_account(demo_notify.load_email_account(live))}

    @router.post("/api/demo/admin/email/ethereal")
    def create_ethereal() -> dict:
        """Creates a free Ethereal fake inbox (needs internet on the machine running the demo)."""
        try:
            account = demo_notify.create_ethereal_account(live)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Could not reach Ethereal to create an inbox: {exc}")
        demo_backend._bump_version(sandbox.root)
        return {"account": demo_notify.public_account(account), "password": account["pass"]}

    @router.post("/api/demo/admin/email/account")
    def set_email_account(body: EmailAccountBody) -> dict:
        account = {
            "provider": body.provider, "host": body.host, "port": body.port, "user": body.user,
            "pass": body.password, "from": body.sender or f"Streakwise Demo <{body.user}>",
            "secure": body.secure, "web": body.web,
        }
        demo_notify.save_email_account(live, account)
        demo_backend._bump_version(sandbox.root)
        return {"account": demo_notify.public_account(account)}

    @router.post("/api/demo/admin/email/mailpit")
    def use_mailpit() -> dict:
        """Points email at a local Mailpit (SMTP localhost:1025, inbox http://localhost:8025)."""
        account = demo_notify.mailpit_account()
        demo_notify.save_email_account(live, account)
        demo_backend._bump_version(sandbox.root)
        return {"account": demo_notify.public_account(account)}

    @router.post("/api/demo/admin/email/account/clear")
    def clear_email_account() -> dict:
        demo_notify.save_email_account(live, None)
        demo_backend._bump_version(sandbox.root)
        return {"account": demo_notify.public_account(demo_notify.load_email_account(live))}

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
        # The email provider needs a recipient address (payload["to"]).
        recipient = learner_email(live, learner_id)
        facts = []
        with demo_backend._submit_lock:
            store = NotificationStore(str(live / "notifications.db"))
            try:
                dispatcher = demo_notify.build_dispatcher(live, store, lambda: clock)
                learner = None
                if category == NotificationCategory.QUEST_UPDATE:
                    with demo_admin.open_app(sandbox) as app:
                        learner = demo_admin.get_learner(app, learner_id)
                for _ in range(count):
                    pid = body.proposal_id or f"demo:{uuid4().hex[:10]}"
                    if learner is not None:
                        # A quest update is only about a quest the learner can still progress.
                        proposal, why_not = build_quest_update(
                            learner, learner_id, clock, proposal_id=pid, channel=channel,
                            created_at=_iso(clock),
                        )
                        if proposal is None:
                            if not store.is_proposal_seen(pid):
                                store.record_suppression(
                                    f"supp_{uuid4().hex[:12]}", pid, learner_id, channel, category,
                                    _iso(clock), why_not,
                                )
                            facts.append({
                                "status": "suppressed", "reason": why_not, "channel": channel.value,
                                "proposal_id": pid, "attempt_count": 0, "rescheduled_for": None,
                            })
                            continue
                        proposal = replace(proposal, payload={**proposal.payload, "to": recipient})
                    else:
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
        recovered = 0
        if not body.active:
            # Provider healthy again: the recovery worker re-sends its dead letters right away.
            from webapp import demo_clock_jobs
            recovered = demo_clock_jobs.recover_dead_letters(sandbox)
        return {**outage_state(live), "recovered": recovered}

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
