"""One tick of the engine's caller-run jobs (skeleton: every step is a stub for now).

    python scripts/run_engine_tick.py --db DB --leaderboard-db LB --prefs prefs.json \\
        --now 2026-10-12T15:40:00+00:00 --out tick.json

Nothing here schedules itself; the caller (cron) runs it. ``now`` must be timezone-aware.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Step functions the later steps will call. They are module-level names so tests can patch them.
from src.abuse.service import AbuseDetectionService  # noqa: E402,F401
from src.abuse.store import AbuseSignalStore  # noqa: E402
from src.experiments.prefs import load_preferences  # noqa: E402
from src.experiments.store import ExperimentStore  # noqa: E402
from src.ingestion.evidence import EvidenceStore  # noqa: E402
from src.leaderboard.projection import LeaderboardProjection  # noqa: E402
from src.leaderboard.store import SQLiteLeaderboardStore  # noqa: E402
from src.notifications.dispatcher import NotificationDispatcher  # noqa: E402
from src.notifications.store import NotificationStore  # noqa: E402
from src.state_machine.learner import LearnerApplication  # noqa: E402
from src.interventions.notification_hook import run_remediation_with_notification  # noqa: E402,F401
from src.interventions.service import run_quest_expiry  # noqa: E402,F401
from src.leaderboard.poller import run_until_idle  # noqa: E402,F401
from src.leaderboard.reconciliation import LeaderboardReconciler  # noqa: E402,F401
from src.leaderboard.weekly import run_weekly_cycle  # noqa: E402,F401
from src.notifications.models import ChannelType, NotificationStatus  # noqa: E402
from src.notifications.streak_risk import run_streak_risk_sweep  # noqa: E402,F401
from src.state_machine.learner import Learner, _as_datetime  # noqa: E402
from src.state_machine.notification_log import read_all_notifications  # noqa: E402

STEP_NAMES = ("weekly_cycle", "poll", "quest_expiry", "remediation", "streak_risk", "abuse", "reconcile")


def _run_step(result: dict, name: str, fn) -> None:
    """Runs ``fn()`` and stores its dict under ``result["steps"][name]``; an Exception is stored, not raised."""
    try:
        result["steps"][name] = fn()
    except Exception as error:  # noqa: BLE001 - isolating a failing step is the point
        result["steps"][name] = {"error": f"{type(error).__name__}: {error}"}


def _stub() -> dict:
    return {"ran": False}


def _all_learner_ids(application) -> list[str]:
    """Every learner with a stored Created event (read through the notification log), sorted."""
    ids: set[str] = set()
    for notification in read_all_notifications(application):
        event = application.mapper.to_domain_event(notification)
        if isinstance(event, Learner.Created):
            ids.add(str(event.originator_id))
    return sorted(ids)


def _iso_week(now: datetime) -> str:
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def _weekly_cycle_step(application, projection, learner_ids, now, min_size, max_size) -> dict:
    epoch = _iso_week(now)
    cycle = run_weekly_cycle(
        application, projection, learner_ids, epoch,
        reference_time=now, min_size=min_size, max_size=max_size,
    )
    return {
        "epoch": cycle.epoch,
        "finalized": len(cycle.finalized),
        "already_finalized": len(cycle.already_finalized),
        "not_finalized": len(cycle.not_finalized),
        "assignments_built": len(cycle.build.assignments),
    }


def _poll_step(application, projection) -> dict:
    return {"applied": run_until_idle(application, projection)}


def _reconcile_step(projection, now) -> dict:
    """Reconciles the epoch's global scope; repairs = inserted + updated + deleted.

    Cohort scopes are NOT reconciled: the reconciler deletes zero-score cohort members as orphans
    (see docs/audit/driver-step3-leaderboard.md), which would drop their cohort membership.
    """
    epoch = _iso_week(now)
    store = projection.store
    reconciler = LeaderboardReconciler(store)
    scopes = [("global", "global")]
    repairs = inserted = updated = deleted = 0
    for scope_type, scope_id in scopes:
        audit = reconciler.reconcile_scope(scope_type, scope_id, epoch, audited_at=now.isoformat())
        inserted += audit.inserted_count
        updated += audit.updated_count
        deleted += audit.deleted_count
        repairs += audit.divergence_count
    return {
        "epoch": epoch, "scopes": len(scopes), "repairs": repairs,
        "inserted": inserted, "updated": updated, "deleted": deleted,
    }


def _quest_expiry_step(application, learner_ids, now) -> dict:
    expired = 0
    for learner_id in learner_ids:
        expired += len(run_quest_expiry(application, learner_id, clock=lambda: now))
    return {"learners_checked": len(learner_ids), "expired": expired}


def _remediation_step(application, dispatcher, learner_ids, prefs_by_learner, now) -> dict:
    """A decision for every checkpoint attempt without one, oldest first; a missing prefs entry is passed as None."""
    decisions_made = assigned = dispatched = not_dispatched = 0
    for learner_id in learner_ids:
        learner = application.repository.get(UUID(learner_id))
        pending = sorted(
            (attempt_id for attempt_id in learner.checkpoint_attempts
             if f"remediation:{attempt_id}" not in learner.remediation_decisions),
            key=lambda attempt_id: (
                _as_datetime(learner.checkpoint_attempts[attempt_id]["occurred_at"]), attempt_id
            ),
        )
        for attempt_id in pending:
            decision, fact = run_remediation_with_notification(
                application, learner_id, attempt_id,
                clock=lambda: now,
                dispatcher=dispatcher,
                prefs=prefs_by_learner.get(learner_id),
                channel=ChannelType.PUSH,
            )
            decisions_made += 1
            if decision.action == "assign":
                assigned += 1
            if fact is not None:
                if fact.status == NotificationStatus.DISPATCHED:
                    dispatched += 1
                else:
                    not_dispatched += 1  # suppressed, rescheduled or failed
    return {
        "decisions_made": decisions_made,
        "assigned": assigned,
        "notices_dispatched": dispatched,
        "notices_not_dispatched": not_dispatched,
    }


def _streak_risk_step(application, dispatcher, learner_ids, prefs_by_learner, now, experiment_store) -> dict:
    sweep = run_streak_risk_sweep(
        application, dispatcher, learner_ids, prefs_by_learner,
        now_utc=now, store=experiment_store,
    )
    sent = not_dispatched = 0
    skipped: dict[str, int] = {}
    for outcome in sweep.outcomes:
        if outcome.fact is None:
            reason = outcome.reason or "unknown"
            skipped[reason] = skipped.get(reason, 0) + 1
        elif outcome.fact.status == NotificationStatus.DISPATCHED:
            sent += 1
        else:
            not_dispatched += 1  # suppressed, rescheduled, failed or a duplicate proposal
    return {"sent": sent, "not_dispatched": not_dispatched, "skipped": dict(sorted(skipped.items()))}


def _abuse_step(application, evidence, signal_store, now) -> dict:
    """Flag-only: signals are stored, no XP or learner state is touched."""
    summary = AbuseDetectionService(application, evidence, signal_store).run(detected_at=now.isoformat())
    return {
        "new_signals": summary.new_signals,
        "existing_signals": summary.existing_signals,
        "counts_by_type": dict(summary.counts_by_type),
    }


def run_tick(
    application,
    projection,
    dispatcher,
    evidence,
    signal_store,
    *,
    now: datetime,
    prefs_by_learner,
    experiment_store=None,
    cohort_min_size: int = 500,
    cohort_max_size: int = 5000,
) -> dict:
    """Runs the seven steps in order at ``now``; a step that raises is reported, the rest still run."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    result: dict = {"now": now.isoformat(), "steps": {}}
    learner_ids = _all_learner_ids(application)
    steps = {
        "weekly_cycle": lambda: _weekly_cycle_step(
            application, projection, learner_ids, now, cohort_min_size, cohort_max_size
        ),
        "quest_expiry": lambda: _quest_expiry_step(application, learner_ids, now),
        "remediation": lambda: _remediation_step(
            application, dispatcher, learner_ids, prefs_by_learner, now
        ),
        "streak_risk": lambda: _streak_risk_step(
            application, dispatcher, learner_ids, prefs_by_learner, now, experiment_store
        ),
        "abuse": lambda: _abuse_step(application, evidence, signal_store, now),
        "poll": lambda: _poll_step(application, projection),
        "reconcile": lambda: _reconcile_step(projection, now),
    }
    for name in STEP_NAMES:
        _run_step(result, name, steps.get(name, _stub))
    steps_out = result["steps"]
    remediation, streak, abuse = (steps_out[n] for n in ("remediation", "streak_risk", "abuse"))
    result["summary"] = {
        "new_decisions": remediation.get("decisions_made", 0),
        "new_signals": abuse.get("new_signals", 0),
        "new_notifications": remediation.get("notices_dispatched", 0) + streak.get("sent", 0),
    }
    return result


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run one tick of the engine's caller-run jobs.")
    parser.add_argument("--db", required=True, help="path of the existing sqlite event log")
    parser.add_argument("--leaderboard-db", required=True, help="path of the leaderboard sqlite file")
    parser.add_argument("--prefs", required=True, help="notification preferences JSON file")
    parser.add_argument("--now", required=True, help="timezone-aware ISO 8601")
    parser.add_argument("--out", default=None, help="also write the result JSON here")
    return parser.parse_args(argv)


class UsageError(Exception):
    """A problem with the arguments or inputs; reported on stderr with exit code 2."""


def _aware(flag: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise UsageError(f"{flag} must be an ISO 8601 date-time, got {value!r}") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise UsageError(
            f"{flag} must include a timezone offset (timezone-aware ISO 8601, e.g. +00:00), got {value!r}"
        )
    return parsed


def _load_prefs(path: str):
    try:
        return load_preferences(path)
    except FileNotFoundError:
        raise UsageError(f"preferences file does not exist: {path}") from None
    except ValueError as error:
        raise UsageError(str(error)) from None


@contextmanager
def _engine(db: str, leaderboard_db: str):
    """The application, the stores on the event-log file and the leaderboard; every one closed in finally."""
    if not Path(db).is_file():
        raise UsageError(f"--db does not exist: {db}")
    opened = []  # closers, in opening order; closed in reverse in the finally below
    try:
        application = LearnerApplication(
            env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
        )
        opened.append(application.close)
        evidence = EvidenceStore(db)
        opened.append(evidence.close)
        notification_store = NotificationStore(db)
        opened.append(notification_store.close)
        experiment_store = ExperimentStore(db)
        opened.append(experiment_store.close)
        signal_store = AbuseSignalStore(db)
        opened.append(signal_store.close)
        board = SQLiteLeaderboardStore(leaderboard_db)
        opened.append(board.close)
        yield SimpleNamespace(
            application=application,
            projection=LeaderboardProjection(board),
            dispatcher=NotificationDispatcher(store=notification_store),
            evidence=evidence,
            signal_store=signal_store,
            experiment_store=experiment_store,
        )
    finally:
        for close in reversed(opened):
            close()


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        now = _aware("--now", args.now)
        prefs = _load_prefs(args.prefs)
        with _engine(args.db, args.leaderboard_db) as engine:
            result = run_tick(
                engine.application, engine.projection, engine.dispatcher, engine.evidence,
                engine.signal_store,
                now=now, prefs_by_learner=prefs, experiment_store=engine.experiment_store,
            )
    except UsageError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    document = json.dumps(result, sort_keys=True)
    print(document)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(document + "\n", encoding="utf-8")
    return 1 if any("error" in step for step in result["steps"].values()) else 0


if __name__ == "__main__":
    sys.exit(main())
