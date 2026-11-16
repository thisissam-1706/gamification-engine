"""Admin read endpoints for the Events tab: event history and ingestion decisions."""

from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter

from src.ingestion.evidence import EvidenceStore
from webapp import demo_admin

_INTERNAL = {"originator_id", "originator_version", "originator_topic", "timestamp"}
_PROVENANCE = ("correlation_id", "causation_id", "source_event_id", "rule_version")


def json_safe(value):
    """Recursively converts values to JSON-safe data."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted((json_safe(v) for v in value), key=str)
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return str(value)


def event_to_dict(event) -> dict:
    attrs = {k: v for k, v in vars(event).items() if k not in _INTERNAL}
    stamp = getattr(event, "timestamp", None)
    item = {
        "seq": event.originator_version,
        "type": type(event).__name__,
        "timestamp": stamp.isoformat() if stamp is not None else None,
        "fields": json_safe(attrs),
    }
    for name in _PROVENANCE:
        item[name] = json_safe(attrs.get(name))
    return item


def create_events_router(sandbox) -> APIRouter:
    router = APIRouter()

    @router.get("/api/demo/admin/learners/{learner_id}/events")
    def events(learner_id: str, limit: int = 60) -> dict:
        uid = demo_admin.parse_learner(learner_id)
        with demo_admin.open_app(sandbox) as app:
            demo_admin.get_learner(app, learner_id)
            stream = list(app.events.get(uid))
        limit = max(1, min(int(limit), 1000))
        newest = stream[-limit:][::-1]
        return {"total": len(stream), "events": [event_to_dict(e) for e in newest]}

    @router.get("/api/demo/admin/learners/{learner_id}/ingestion")
    def ingestion(learner_id: str) -> dict:
        demo_admin.parse_learner(learner_id)
        with demo_admin.open_app(sandbox) as app:
            demo_admin.get_learner(app, learner_id)
        store = EvidenceStore(str(sandbox.live_dir / "evidence.db"))
        try:
            records = store.list(learner_id)
        finally:
            store.close()
        records = sorted(records, key=lambda r: r.id, reverse=True)
        by_reason: dict[str, int] = {}
        for r in records:
            by_reason[r.reason] = by_reason.get(r.reason, 0) + 1
        return {
            "records": [
                {"id": r.id, "kind": r.kind, "reason": r.reason, "action_id": r.action_id,
                 "event_id": r.event_id, "original_ref": r.original_ref,
                 "received_at": r.received_at}
                for r in records
            ],
            "by_reason": by_reason,
        }

    return router
