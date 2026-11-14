"""Manual entry point for the exp_007 pipeline on a persisted log: assign, sweep, report.

Every subcommand takes --db PATH (an existing sqlite file holding the events and the stores) and prints
one JSON document to stdout. Every instant comes from an argument (timezone-aware ISO 8601); nothing
reads the clock, nothing is scheduled. Notification preferences come from a JSON file (--prefs),
because they are not in the event log. Errors go to stderr with exit code 2.

    python scripts/run_experiment.py assign --db DB --prefs prefs.json --assigned-at 2026-10-01T12:00:00+00:00
    python scripts/run_experiment.py sweep  --db DB --prefs prefs.json --now 2026-10-12T15:40:00+00:00
    python scripts/run_experiment.py report --db DB --as-of 2026-10-31T12:00:00+00:00 \\
        --assessment-id asmt-1 --out report.json
"""

import argparse
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.abuse.service import AbuseDetectionService  # noqa: E402
from src.abuse.store import AbuseSignalStore  # noqa: E402
from src.experiments.learners import list_learner_ids  # noqa: E402
from src.experiments.prefs import load_preferences  # noqa: E402
from src.experiments.runner import awards_from_abuse_service, run_experiment  # noqa: E402
from src.experiments.service import assign_and_record_eligibility  # noqa: E402
from src.experiments.store import ExperimentStore  # noqa: E402
from src.ingestion.evidence import EvidenceStore  # noqa: E402
from src.notifications.dispatcher import NotificationDispatcher  # noqa: E402
from src.notifications.store import NotificationStore  # noqa: E402
from src.notifications.streak_risk import run_streak_risk_sweep  # noqa: E402
from src.state_machine.learner import LearnerApplication  # noqa: E402

DEFAULT_EXP_ID = "exp_007"


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
def _engine(db: str):
    """The application and the three stores on the one sqlite file; all closed in finally."""
    if not Path(db).is_file():
        raise UsageError(f"--db does not exist: {db}")
    application = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
    )
    try:
        evidence = EvidenceStore(db)
        try:
            notification_store = NotificationStore(db)
            try:
                store = ExperimentStore(db)
                try:
                    yield SimpleNamespace(
                        application=application,
                        evidence=evidence,
                        notification_store=notification_store,
                        store=store,
                    )
                finally:
                    store.close()
            finally:
                notification_store.close()
        finally:
            evidence.close()
    finally:
        application.close()


def _print(document: dict) -> None:
    print(json.dumps(document, sort_keys=True))


def cmd_assign(args) -> int:
    assigned_at = _aware("--assigned-at", args.assigned_at)
    prefs = _load_prefs(args.prefs)
    with _engine(args.db) as engine:
        learner_ids = list_learner_ids(engine.application)
        summary = assign_and_record_eligibility(
            engine.application, engine.store, learner_ids, prefs, args.exp_id, assigned_at
        )
    _print(
        {
            "exp_id": args.exp_id,
            "assigned_at": assigned_at.isoformat(),
            "learners": len(learner_ids),
            "newly_assigned": summary.newly_assigned,
            "already_assigned": summary.already_assigned,
            "eligible": summary.eligible,
            "excluded": summary.excluded,
        }
    )
    return 0


def cmd_sweep(args) -> int:
    now = _aware("--now", args.now)
    prefs = _load_prefs(args.prefs)
    with _engine(args.db) as engine:
        dispatcher = NotificationDispatcher(store=engine.notification_store)
        result = run_streak_risk_sweep(
            engine.application,
            dispatcher,
            list_learner_ids(engine.application),
            prefs,
            now_utc=now,
            experiment_id=args.exp_id,
            store=engine.store,
        )
    _print(
        {
            "now": now.isoformat(),
            "outcomes": [
                {
                    "learner_id": outcome.learner_id,
                    "reason": outcome.reason,
                    "fact_status": None if outcome.fact is None else outcome.fact.status.value,
                }
                for outcome in result.outcomes
            ],
        }
    )
    return 0


def cmd_report(args) -> int:
    as_of = _aware("--as-of", args.as_of)
    current_prefs = _load_prefs(args.prefs) if args.prefs else None
    with _engine(args.db) as engine:
        # completion_facts() only reads events and evidence; the signal store stays in memory so the
        # report writes nothing into the log database.
        signals = AbuseSignalStore(":memory:")
        try:
            awards = awards_from_abuse_service(
                AbuseDetectionService(engine.application, engine.evidence, signals)
            )
        finally:
            signals.close()
        result = run_experiment(
            engine.application,
            engine.store,
            engine.notification_store,
            awards,
            exp_id=args.exp_id,
            assessment_id=args.assessment_id,
            assessment_version=args.assessment_version,
            as_of=as_of,
            current_prefs=current_prefs,
        )
    document = result.to_dict()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    _print({"out": args.out, "valid": document["valid"], "invalid_reasons": document["invalid_reasons"]})
    return 0  # an invalid run is still a successful report; the JSON says so


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Manual entry point for the exp_007 pipeline.")
    commands = parser.add_subparsers(dest="command", required=True)

    def common(sub, *, prefs_required: bool):
        sub.add_argument("--db", required=True, help="path of the existing sqlite log")
        sub.add_argument("--prefs", required=prefs_required, help="notification preferences JSON file")
        sub.add_argument("--exp-id", default=DEFAULT_EXP_ID)

    assign = commands.add_parser("assign", help="assign every learner and record eligibility")
    common(assign, prefs_required=True)
    assign.add_argument("--assigned-at", required=True, help="timezone-aware ISO 8601")
    assign.set_defaults(handler=cmd_assign)

    sweep = commands.add_parser("sweep", help="run the streak-risk sweep at one instant")
    common(sweep, prefs_required=True)
    sweep.add_argument("--now", required=True, help="timezone-aware ISO 8601")
    sweep.set_defaults(handler=cmd_sweep)

    report = commands.add_parser("report", help="run the experiment report and write it as JSON")
    common(report, prefs_required=False)
    report.add_argument("--as-of", required=True, help="timezone-aware ISO 8601 data cutoff")
    report.add_argument("--assessment-id", required=True)
    report.add_argument("--assessment-version", type=int, default=None)
    report.add_argument("--out", required=True, help="where to write the report JSON")
    report.set_defaults(handler=cmd_report)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        return args.handler(args)
    except UsageError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
