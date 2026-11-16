"""Admin endpoints for the Experiments tab (M7): exp_007 assignment, sweep, report, reproducibility."""

import hashlib
import json
import tempfile
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from src.abuse.service import AbuseDetectionService
from src.abuse.store import AbuseSignalStore
from src.experiments.learners import list_learner_ids
from src.experiments.runner import (
    CONTROL_VARIANT,
    TREATMENT_VARIANT,
    awards_from_abuse_service,
    run_experiment,
)
from src.experiments.service import assign_and_record_eligibility
from src.experiments.store import ExperimentStore
from src.ingestion.evidence import EvidenceStore
from src.state_machine.learner import LearnerApplication
from src.notifications.store import NotificationStore
from src.notifications.streak_risk import run_streak_risk_sweep
from webapp import demo_admin, demo_backend, demo_notify, views
from webapp.demo_admin_events import json_safe

EXP_ID = "exp_007"
ASSESSMENT_ID = "fractions_checkpoint_1"
ASSESSMENT_VERSION = 1


class SweepBody(BaseModel):
    learner_id: str


def prefs_by_learner(sandbox) -> dict:
    profile = views.load_profile(sandbox.live_dir)
    return {lid: demo_notify.prefs_for(sandbox.live_dir, lid) for lid in profile["learners"]}


def _store(sandbox) -> ExperimentStore:
    return ExperimentStore(str(sandbox.live_dir / "experiments.db"))


def compute_report(sandbox) -> dict:
    live = sandbox.live_dir
    store = _store(sandbox)
    evidence = EvidenceStore(str(live / "evidence.db"))
    notif = NotificationStore(str(live / "notifications.db"))
    signals = AbuseSignalStore(":memory:")  # in memory: the report must not write to the log
    try:
        with demo_admin.open_app(sandbox) as app:
            awards = awards_from_abuse_service(AbuseDetectionService(app, evidence, signals))
            result = run_experiment(
                app, store, notif, awards,
                exp_id=EXP_ID, assessment_id=ASSESSMENT_ID, assessment_version=ASSESSMENT_VERSION,
                as_of=demo_admin.global_clock(sandbox), current_prefs=prefs_by_learner(sandbox),
            )
            return json_safe(result.to_dict())
    finally:
        signals.close()
        notif.close()
        evidence.close()
        store.close()


_reference_cache: dict = {}


def compute_reference_report() -> dict:
    """exp_007 report over the saved small seed42 benchmark population (matured outcomes).

    Builds the same persisted log as scripts/run_scale_benchmark.py (step 6) in a temp dir and runs
    the unchanged run_experiment over it at the benchmark's as-of date. Deterministic, so cached.
    """
    if "value" in _reference_cache:
        return _reference_cache["value"]
    from webapp.demo_admin_recovery import _load_script

    bench = _load_script("run_scale_benchmark")
    population, seed = 20, 42  # the small profile used by the Recovery tab
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "reference.db")
        built = bench.build_engine_repro_log(db, population, seed)
        application = LearnerApplication(
            env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
        )
        evidence, store = EvidenceStore(db), ExperimentStore(db)
        notif, signals = NotificationStore(db), AbuseSignalStore(":memory:")
        try:
            awards = awards_from_abuse_service(AbuseDetectionService(application, evidence, signals))
            result = run_experiment(
                application, store, notif, awards,
                exp_id=bench.ENGINE_EXP_ID, assessment_id=bench.ENGINE_ASSESSMENT_ID,
                as_of=bench.ENGINE_AS_OF,
            )
            report = json_safe(result.to_dict())
        finally:
            signals.close()
            notif.close()
            store.close()
            evidence.close()
            application.close()
    value = {
        "report": report, "sha256": _hash(report),
        "population": {"profile": "small", "seed": seed, "learners": built["learners"], "attempts": built["attempts"]},
        "as_of": bench.ENGINE_AS_OF.isoformat().replace("+00:00", "Z"),
    }
    _reference_cache["value"] = value
    return value


def _hash(report: dict) -> str:
    return hashlib.sha256(
        json.dumps(report, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def create_experiments_router(sandbox) -> APIRouter:
    router = APIRouter()
    live = sandbox.live_dir

    @router.post("/api/demo/admin/experiments/assign")
    def assign() -> dict:
        with demo_backend._submit_lock:
            store = _store(sandbox)
            try:
                with demo_admin.open_app(sandbox) as app:
                    summary = assign_and_record_eligibility(
                        app, store, list_learner_ids(app), prefs_by_learner(sandbox), EXP_ID,
                        demo_admin.global_clock(sandbox),
                    )
            finally:
                store.close()
            demo_admin.catch_up_projection(sandbox)
            demo_backend._bump_version(sandbox.root)
        return json_safe({
            "newly_assigned": summary.newly_assigned, "already_assigned": summary.already_assigned,
            "eligible": summary.eligible, "excluded": summary.excluded,
        })

    @router.get("/api/demo/admin/experiments/assignments")
    def assignments() -> dict:
        profile = views.load_profile(live)
        store = _store(sandbox)
        try:
            eligibility = {r["learner_id"]: r for r in store.list_eligibility(EXP_ID)}
        finally:
            store.close()
        rows = []
        arm_counts = {"treatment": 0, "control": 0}
        with demo_admin.open_app(sandbox) as app:
            for learner_id, info in profile["learners"].items():
                learner = demo_admin.get_learner(app, learner_id)
                assignment = learner.experiment_assignments.get(EXP_ID)
                variant = assignment["variant"] if assignment else None
                arm = {TREATMENT_VARIANT: "treatment", CONTROL_VARIANT: "control"}.get(variant)
                if arm:
                    arm_counts[arm] += 1
                row = eligibility.get(learner_id)
                rows.append({
                    "display_name": info["display_name"], "learner_id": learner_id,
                    "variant": variant, "arm": arm,
                    "eligible": None if row is None else bool(row["eligible"]),
                    "exclusion_reason": None if row is None or row["eligible"] else row["reason"],
                    "first_exposure": learner.first_experiment_exposure(EXP_ID),
                })
        rows.sort(key=lambda r: r["display_name"])
        return json_safe({
            "rows": rows, "assigned_count": sum(arm_counts.values()), "arm_counts": arm_counts,
        })

    @router.post("/api/demo/admin/experiments/sweep")
    def sweep(body: SweepBody) -> dict:
        demo_admin.learner_snapshot(sandbox, body.learner_id)
        now = sandbox.get_clock(body.learner_id)
        with demo_backend._submit_lock:
            notif = NotificationStore(str(live / "notifications.db"))
            store = _store(sandbox)
            try:
                dispatcher = demo_notify.build_dispatcher(live, notif, lambda: now)
                with demo_admin.open_app(sandbox) as app:
                    result = run_streak_risk_sweep(
                        app, dispatcher, [body.learner_id], prefs_by_learner(sandbox),
                        now_utc=now, experiment_id=EXP_ID, store=store,
                    )
            finally:
                store.close()
                notif.close()
            demo_admin.catch_up_projection(sandbox)
            demo_backend._bump_version(sandbox.root)
        return {
            "now": demo_admin.iso_z(now),
            "outcomes": [
                {"learner_id": o.learner_id, "reason": o.reason,
                 "fact_status": None if o.fact is None else o.fact.status.value}
                for o in result.outcomes
            ],
        }

    @router.get("/api/demo/admin/experiments/report")
    def report() -> dict:
        data = compute_report(sandbox)
        return {"report": data, "sha256": _hash(data)}

    @router.get("/api/demo/admin/experiments/reference-report")
    def reference_report() -> dict:
        return compute_reference_report()

    @router.post("/api/demo/admin/experiments/reproduce")
    def reproduce() -> dict:
        first, second = compute_report(sandbox), compute_report(sandbox)
        h1, h2 = _hash(first), _hash(second)
        return {"hash_1": h1, "hash_2": h2, "identical": h1 == h2, "valid": first["valid"]}

    return router
