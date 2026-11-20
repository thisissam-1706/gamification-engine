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


# Domain-time fields, most specific first. The eventsourcing ``timestamp`` is the server wall
# clock at save time, which has nothing to do with the demo clock, so it is never shown.
_TIME_FIELDS = (
    "occurred_at", "decided_at", "completed_at", "expired_at", "awarded_at", "exposed_at",
    "assigned_at",
)


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day).isoformat() + "+00:00"
    text = str(value)
    if len(text) == 10:  # a bare YYYY-MM-DD activity date
        return text + "T00:00:00+00:00"
    return text


def _own_time(event) -> str | None:
    if type(event).__name__ == "FreezeAcquired":  # a standalone fact dated by its activity day
        return _iso(getattr(event, "activity_date", None))
    for name in _TIME_FIELDS:
        value = getattr(event, name, None)
        if value:
            return _iso(value)
    return None


def event_to_dict(event, time: str | None = None, time_source: str | None = None) -> dict:
    attrs = {k: v for k, v in vars(event).items() if k not in _INTERNAL}
    item = {
        "seq": event.originator_version,
        "type": type(event).__name__,
        "time": time if time is not None else _own_time(event),
        "time_source": time_source or "event",
        "fields": json_safe(attrs),
    }
    for name in _PROVENANCE:
        item[name] = json_safe(attrs.get(name))
    return item


def stream_to_dicts(stream) -> list[dict]:
    """Event dicts in stream order, each with the demo-clock time of the fact it records.

    Facts without their own time (XPAwarded, StreakUpdated, QuestProgressed, ...) are emitted in
    the same decision as the fact before them, so they take that fact's time.
    """
    out = []
    carried: str | None = None
    for event in stream:
        own = _own_time(event)
        if own is not None:
            carried = own
            out.append(event_to_dict(event, own, "event"))
            continue
        if type(event).__name__ == "Created":
            created = _iso(getattr(event, "timestamp", None))
            carried = carried or created
            out.append(event_to_dict(event, created, "created"))
            continue
        if carried is None and getattr(event, "activity_date", None):
            carried = _iso(event.activity_date)
        out.append(event_to_dict(event, carried, "same_decision"))
    return out


def create_events_router(sandbox) -> APIRouter:
    router = APIRouter()

    @router.get("/api/demo/admin/learners/{learner_id}/events")
    def events(learner_id: str, limit: int = 60) -> dict:
        uid = demo_admin.parse_learner(learner_id)
        with demo_admin.open_app(sandbox) as app:
            demo_admin.get_learner(app, learner_id)
            stream = list(app.events.get(uid))
        limit = max(1, min(int(limit), 1000))
        newest = stream_to_dicts(stream)[-limit:][::-1]
        return {"total": len(stream), "events": newest}

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
