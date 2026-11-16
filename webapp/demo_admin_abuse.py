"""Admin endpoints for the Abuse tab (M6): attack simulator, detection pass, flags, rules.

Attack endpoints only call Sandbox.submit_lesson_completed / Sandbox.resubmit_event (which take
demo_backend._submit_lock themselves), so they never hold the lock. The scan holds it.
"""

from datetime import timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.abuse.rules import DEFAULT_ABUSE_RULES_VERSION, get_abuse_rules
from src.abuse.service import AbuseDetectionService
from src.abuse.store import AbuseSignalStore
from src.ingestion.evidence import EvidenceStore
from src.state_machine.notification_log import read_all_notifications
from webapp import demo_admin, demo_backend, views
from webapp.demo_admin_events import json_safe

# Attack shapes. Each number is chosen to exceed the threshold of the default abuse rule set
# (abuse_rules.json, version v2) for its own pattern only, and to stay under the others.
# impossible_timing: rule short_gaps_required=3 gaps below min_gap_seconds (20 s for lessons)
#   inside 10 min. 6 lessons 5 s apart give 5 short gaps. Under scripted_farming metronome
#   (needs 12 events) and burst (needs more than 20).
IT_COUNT, IT_GAP_SECONDS = 6, 5
# scripted_farming: metronome rule needs min_events=12 with gap CV <= 0.05 and mean gap >= 1 s.
#   14 lessons exactly 90 s apart (CV 0). 90 s is above the 20 s minimum gap, so impossible_timing
#   stays quiet; 14 distinct lesson ids never trigger the repeat rule.
SF_COUNT, SF_GAP_SECONDS = 14, 90
# replay: rule needs min_distinct_replayed_events=5 distinct redelivered event ids inside 60 min.
#   6 accepted lessons (5 min apart, so timing stays quiet) each re-sent once unchanged.
RP_COUNT, RP_GAP_SECONDS = 6, 300
# duplicate_exploit: rule needs min_distinct_resubmitted_completions=3 inside 60 min. 4 lessons
#   with fixed completion ids, then the same completion ids re-sent under 4 new event ids.
DX_COUNT, DX_GAP_SECONDS = 4, 300

# Each pattern sits in its own 3-hour block before the demo clock, so running two different
# attacks on the same learner never interleaves their timestamps (which would trip the other
# pattern's timing rules). Repeating the SAME attack without a reset can add extra flags.
BLOCK_SECONDS = {"impossible_timing": 0, "scripted_farming": 3 * 3600,
                 "replay": 6 * 3600, "duplicate_exploit": 9 * 3600}

PATTERNS = ("replay", "impossible_timing", "scripted_farming", "duplicate_exploit")


class AttackBody(BaseModel):
    pattern: str


class ScanBody(BaseModel):
    learner_id: str | None = None


def _tally(results: list[dict]) -> tuple[int, dict]:
    accepted = sum(1 for r in results if r["status"] == "accepted")
    rejected: dict[str, int] = {}
    for r in results:
        if r["status"] != "accepted":
            rejected[r["reason"]] = rejected.get(r["reason"], 0) + 1
    return accepted, rejected


def _xp_by_learner(sandbox, learner_ids) -> dict[str, int]:
    out = {}
    with demo_admin.open_app(sandbox) as app:
        for lid in learner_ids:
            out[lid] = demo_admin.get_learner(app, lid).xp_total
    return out


def create_abuse_router(sandbox) -> APIRouter:
    router = APIRouter()
    live = sandbox.live_dir

    @router.post("/api/demo/admin/learners/{learner_id}/abuse/attack")
    def attack(learner_id: str, body: AttackBody) -> dict:
        if body.pattern not in PATTERNS:
            raise HTTPException(status_code=422, detail=f"Unknown pattern {body.pattern!r}")
        before = demo_admin.learner_snapshot(sandbox, learner_id)
        clock = sandbox.get_clock(learner_id)
        results: list[dict] = []

        block = BLOCK_SECONDS[body.pattern]

        def lesson(lesson_id, offset_seconds, **kw):
            res = sandbox.submit_lesson_completed(
                learner_id, lesson_id,
                occurred_at=clock - timedelta(seconds=block + offset_seconds), **kw
            )
            results.append(res)
            return res

        if body.pattern == "impossible_timing":
            for i in range(1, IT_COUNT + 1):
                lesson(f"attack_it_{i}", (IT_COUNT - i) * IT_GAP_SECONDS)
        elif body.pattern == "scripted_farming":
            for i in range(1, SF_COUNT + 1):
                lesson(f"attack_sf_{i}", (SF_COUNT - i) * SF_GAP_SECONDS)
        elif body.pattern == "replay":
            sent = [lesson(f"attack_rp_{i}", (RP_COUNT - i) * RP_GAP_SECONDS) for i in range(1, RP_COUNT + 1)]
            for res in sent:
                results.append(sandbox.resubmit_event(learner_id, res["event_id"]))
        else:  # duplicate_exploit
            for i in range(1, DX_COUNT + 1):
                lesson(f"attack_dx_{i}", (DX_COUNT - i) * DX_GAP_SECONDS, completion_id=f"attack_dx_{i}")
            for i in range(1, DX_COUNT + 1):
                lesson(f"attack_dx_{i}", (DX_COUNT - i) * 10, completion_id=f"attack_dx_{i}")
        accepted, rejected = _tally(results)
        demo_backend._bump_version(sandbox.root)
        return {
            "pattern": body.pattern, "events_sent": len(results), "accepted": accepted,
            "rejected_by_reason": rejected, "before": before,
            "after": demo_admin.learner_snapshot(sandbox, learner_id),
        }

    @router.post("/api/demo/admin/abuse/scan")
    def scan(body: ScanBody) -> dict:
        if body.learner_id is not None:
            demo_admin.learner_snapshot(sandbox, body.learner_id)
        profile = views.load_profile(live)
        wanted = [body.learner_id] if body.learner_id else list(profile["learners"])
        with demo_backend._submit_lock:
            evidence = EvidenceStore(str(live / "evidence.db"))
            signals = AbuseSignalStore(str(live / "abuse.db"))
            try:
                with demo_admin.open_app(sandbox) as app:
                    xp_before = _xp_by_learner(sandbox, wanted)
                    log_before = len(read_all_notifications(app, start=1))
                    service = AbuseDetectionService(app, evidence, signals)
                    summary = service.run(learner_ids=[body.learner_id] if body.learner_id else None)
                    xp_after = _xp_by_learner(sandbox, wanted)
                    log_after = len(read_all_notifications(app, start=1))
            finally:
                signals.close()
                evidence.close()
            if summary.new_signals:
                demo_backend._bump_version(sandbox.root)
        return {
            "summary": {
                "counts_by_type": summary.counts_by_type, "learners_scanned": summary.learners_scanned,
                "events_read": summary.events_read, "new_signals": summary.new_signals,
                "existing_signals": summary.existing_signals, "rules_version": summary.rules_version,
            },
            "scores_changed": xp_before != xp_after,
            "event_log_before": log_before, "event_log_after": log_after,
            "xp_before": xp_before, "xp_after": xp_after,
        }

    @router.get("/api/demo/admin/abuse/signals")
    def signals(learner_id: str | None = None) -> dict:
        profile = views.load_profile(live)
        names = {lid: i["display_name"] for lid, i in profile["learners"].items()}
        evidence = EvidenceStore(str(live / "evidence.db"))
        store = AbuseSignalStore(str(live / "abuse.db"))
        try:
            records = store.list(learner_id or None)
            ev_by_id = {r.id: r for r in evidence.list()}
            with demo_admin.open_app(sandbox) as app:
                facts, _, _ = AbuseDetectionService(app, evidence, store).completion_facts()
        finally:
            store.close()
            evidence.close()
        by_completion = {f.completion_id: f for f in facts}
        by_event = {f.event_id: f for f in facts if f.event_id}
        out = []
        for rec in records:
            items = []
            for ref in rec.evidence_refs:
                kind, _, value = ref.partition(":")
                item = {"ref": ref, "resolved": False}
                if kind == "evidence" and value.isdigit() and int(value) in ev_by_id:
                    r = ev_by_id[int(value)]
                    fact = by_event.get(r.original_ref) if r.original_ref else None
                    item.update(resolved=True, time=r.received_at, reason=r.reason,
                                event_id=r.event_id, lesson_id=fact.lesson_id if fact else None)
                elif kind in ("completion", "event"):
                    fact = by_completion.get(value) if kind == "completion" else by_event.get(value)
                    if fact is not None:
                        item.update(resolved=True, time=fact.occurred_at, reason="accepted_completion",
                                    event_id=fact.event_id, lesson_id=fact.lesson_id)
                items.append(item)
            out.append({
                "signal_id": rec.signal_id, "learner_id": rec.learner_id,
                "display_name": names.get(rec.learner_id, rec.learner_id[:8]),
                "signal_type": rec.signal_type, "status": rec.status, "rule_id": rec.rule_id,
                "rule_version": rec.rule_version, "window_start": rec.window_start,
                "window_end": rec.window_end, "detected_at": rec.detected_at,
                "evidence_refs": list(rec.evidence_refs), "evidence": items,
            })
        return json_safe({"signals": out})

    @router.get("/api/demo/admin/abuse/rules")
    def rules() -> dict:
        r = get_abuse_rules(DEFAULT_ABUSE_RULES_VERSION)
        return {"version": r.version, "sha256": r.digest, "rules": r.content()}

    return router
