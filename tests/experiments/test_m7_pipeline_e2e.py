"""exp_007 end to end on the engine's own data: real learners, real ingestion, real dispatcher, real report.

No simulator and no hand-built mastery dictionaries. One sqlite file holds everything:

1. 40 learners with Created events older than 3 days (20 whose ids hash into each arm), and a preferences
   JSON file in which a few learners opted out and two have no entry;
2. ``assign`` through the script's ``main``;
3. checkpoint attempts and surface views through ``ingest_checkpoint_attempted`` / ``ingest_surface_viewed``,
   lessons through ``ingest_lesson_completed`` (so some treatment learners have a streak at risk);
4. ``sweep`` at 21:10 IST on the day after those lessons;
5. ``report`` with ``--as-of`` after the 14-day window, checked against a hand count made from the plan below;
6. a forced invalid run on a copy of the database.

The plan (eligible learners only; the listed ineligible learners also get attempts, which must not count):
  treatment T0-T16 eligible: T0-T7 pass, T8-T10 fail, T11-T16 no attempt   -> mastered 8, failed 3, missing 6 of 17
  control   C0-C16, C18 eligible: C0-C5 pass, C6-C9 fail, others none      -> mastered 6, failed 4, missing 8 of 18
  ineligible: T17, T18 (opted out of push), C17 (opted out of streak_risk), T19 and C19 (no prefs entry)
  surface views: T0-T5 (and one refused attempt for a control learner); lessons: T0-T11 and C0-C11.
"""

import contextlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.ingestion.config import IngestConfig
from src.ingestion.evidence import EvidenceStore
from src.ingestion.service import (
    ingest_checkpoint_attempted,
    ingest_lesson_completed,
    ingest_surface_viewed,
)
from src.ingestion.validation import DEFAULT_INGEST_RULE_VERSION, expected_dedup_key
from src.experiments.store import ExperimentStore
from src.notifications.store import NotificationStore
from src.state_machine.learner import LearnerApplication
from tests.experiments.support import (
    ASSESSMENT_ID,
    ASSIGNED_AT,
    EXP_ID,
    lesson_completed_envelope,
    make_learner,
    record_violation,
    stored_event_names,
    surface_viewed_envelope,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_experiment.py"
CREATED_OLD = datetime(2026, 9, 1, tzinfo=timezone.utc)
AS_OF = ASSIGNED_AT + timedelta(days=15)  # after every learner's 14-day window
SWEEP_AT = datetime(2026, 10, 5, 15, 40, tzinfo=timezone.utc)  # 21:10 on 2026-10-05 in Asia/Kolkata
LESSON_AT = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)  # 15:30 IST the day before: streak at risk


def _script_main(argv):
    spec = importlib.util.spec_from_file_location("m7_e2e_run_experiment", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main(argv)


def _checkpoint_envelope(learner_id: str, attempt_id: str, passed: bool, occurred_at: datetime) -> dict:
    event_id = str(uuid4())
    return {
        "event_id": event_id,
        "event_type": "learning.checkpoint_attempted",
        "schema_version": 1,
        "occurred_at": occurred_at.isoformat(),
        "ingested_at": (occurred_at + timedelta(minutes=1)).isoformat(),
        "subject_id": learner_id,
        "producer": "learning-svc",
        "correlation_id": str(uuid4()),
        "causation_id": None,
        "dedup_key": expected_dedup_key(event_id, DEFAULT_INGEST_RULE_VERSION),
        "payload": {
            "attempt_id": attempt_id,
            "assessment_id": ASSESSMENT_ID,
            "assessment_version": 1,
            "passed": passed,
            "timezone": "Asia/Kolkata",
        },
        "provenance": {"source": "client"},
    }


def _ingest(function, application, evidence, envelope):
    """Through the real ingestion path, with a trusted clock five minutes after the event."""
    now = datetime.fromisoformat(envelope["occurred_at"]) + timedelta(minutes=5)
    return function(
        application, evidence, envelope, authenticated_producer="learning-svc",
        config=IngestConfig(DEFAULT_INGEST_RULE_VERSION, clock=lambda: now),
    )


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("exp007_e2e")
    db = str(tmp / "engine.db")

    # 1. learners and preferences ------------------------------------------------------------------------
    application = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
    )
    evidence = EvidenceStore(db)
    ids = {"T": [], "C": []}
    try:
        for key, variant in (("T", "variant_a"), ("C", "variant_b")):
            for nth in range(20):
                ids[key].append(
                    make_learner(application, variant, nth=nth, created=CREATED_OLD, assigned_at=None)
                )
    finally:
        evidence.close()
        application.close()
    T, C = ids["T"], ids["C"]
    prefs = {learner_id: {"timezone": "Asia/Kolkata"} for learner_id in T + C}
    for learner_id in (T[17], T[18]):
        prefs[learner_id]["opted_out_channels"] = ["push"]
    prefs[C[17]]["opted_out_categories"] = ["streak_risk"]
    del prefs[T[19]], prefs[C[19]]  # no entry: missing preferences
    prefs_path = tmp / "prefs.json"
    prefs_path.write_text(json.dumps(prefs), encoding="utf-8")

    world = SimpleNamespace(tmp=tmp, db=db, T=T, C=C, prefs_path=str(prefs_path), prefs=prefs)

    # 2. assign (through main) -------------------------------------------------------------------------------
    # (module-scoped fixture: redirect stdout and stderr by hand instead of using capsys)
    def run(*argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = _script_main([str(a) for a in argv])
        return code, out.getvalue(), err.getvalue()

    world.run = run
    world.assign = run(
        "assign", "--db", db, "--prefs", world.prefs_path, "--assigned-at", ASSIGNED_AT.isoformat()
    )

    # 3. real ingestion --------------------------------------------------------------------------------------
    application = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
    )
    evidence = EvidenceStore(db)
    statuses = []
    try:
        plan = (
            [(T[i], True) for i in range(0, 8)] + [(T[i], False) for i in range(8, 11)]
            + [(C[i], True) for i in range(0, 6)] + [(C[i], False) for i in range(6, 10)]
            + [(T[17], True), (C[17], True)]  # ineligible learners: must not count
        )
        for n, (learner_id, passed) in enumerate(plan):
            at = datetime(2026, 10, 2, 10, 0, tzinfo=timezone.utc) + timedelta(hours=n)
            statuses.append(_ingest(
                ingest_checkpoint_attempted, application, evidence,
                _checkpoint_envelope(learner_id, f"att-{n}", passed, at),
            ).status)
        for n, learner_id in enumerate(T[0:6] + [C[0]]):  # T0-T5 are exposed; C0's view must be refused
            at = datetime(2026, 10, 3, 10, 0, tzinfo=timezone.utc) + timedelta(minutes=n)
            statuses.append(_ingest(
                ingest_surface_viewed, application, evidence,
                surface_viewed_envelope(learner_id, f"view-{n}", at),
            ).status)
        for n, learner_id in enumerate(T[0:12] + C[0:12]):
            statuses.append(_ingest(
                ingest_lesson_completed, application, evidence,
                lesson_completed_envelope(learner_id, f"lesson-{n}", LESSON_AT + timedelta(minutes=n)),
            ).status)
    finally:
        evidence.close()
        application.close()
    world.statuses = statuses

    # 4. sweep -------------------------------------------------------------------------------------------------
    world.sweep = run("sweep", "--db", db, "--prefs", world.prefs_path, "--now", SWEEP_AT.isoformat())

    # 5. report, three times: twice in-process, once as a subprocess from another directory -----------------------
    common = ("report", "--db", db, "--as-of", AS_OF.isoformat(), "--assessment-id", ASSESSMENT_ID,
              "--prefs", world.prefs_path)
    world.report_argv = common
    world.report1 = run(*common, "--out", tmp / "report1.json")
    application = LearnerApplication(  # open and close the log in between: a fresh start for the next run
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
    )
    application.close()
    world.report2 = run(*common, "--out", tmp / "report2.json")
    elsewhere = tmp / "elsewhere"
    elsewhere.mkdir()
    world.report3 = subprocess.run(
        [sys.executable, str(SCRIPT), *[str(a) for a in common], "--out", str(tmp / "report3.json")],
        cwd=elsewhere, capture_output=True, text=True, timeout=300,
    )
    world.files = [tmp / f"report{i}.json" for i in (1, 2, 3)]
    return world


def _report(world) -> dict:
    return json.loads(world.files[0].read_text(encoding="utf-8"))


# 2 assign -----------------------------------------------------------------------------------------------------


def test_assign_creates_both_arms_and_excludes_some_learners(pipeline):
    code, out, err = pipeline.assign
    assert code == 0, err
    summary = json.loads(out)
    assert summary["learners"] == 40 and summary["newly_assigned"] == 40
    assert summary["eligible"] == 35 and summary["excluded"] == 5

    store = ExperimentStore(pipeline.db)
    try:
        rows = {r["learner_id"]: r for r in store.list_eligibility(EXP_ID)}
    finally:
        store.close()
    arms = {"variant_a": 0, "variant_b": 0}
    for row in rows.values():
        arms[row["variant"]] += 1
    assert arms == {"variant_a": 20, "variant_b": 20}  # the arms come from the assignment hash
    excluded = {i: r["reason"] for i, r in rows.items() if not r["eligible"]}
    assert excluded == {
        pipeline.T[17]: "excluded_opted_out", pipeline.T[18]: "excluded_opted_out",
        pipeline.C[17]: "excluded_opted_out",
        pipeline.T[19]: "missing_preferences", pipeline.C[19]: "missing_preferences",
    }


# 3 ingestion --------------------------------------------------------------------------------------------------


def test_everything_was_ingested_through_the_real_path(pipeline):
    statuses = pipeline.statuses
    # 23 attempts + 6 views of treatment learners + 24 lessons accepted; the one view of a control learner refused
    assert statuses.count("accepted") == 23 + 6 + 24
    assert [s for s in statuses if s != "accepted"] == ["rejected"]

    application = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": pipeline.db}
    )
    try:
        assert stored_event_names(application, pipeline.C[0]).count("ExperimentExposed") == 0
        assert stored_event_names(application, pipeline.T[0]).count("ExperimentExposed") == 1
    finally:
        application.close()


# 4 sweep ------------------------------------------------------------------------------------------------------


def test_sweep_dispatched_to_treatment_learners_with_a_streak_and_to_no_control_learner(pipeline):
    code, out, err = pipeline.sweep
    assert code == 0, err
    outcomes = {o["learner_id"]: o for o in json.loads(out)["outcomes"]}
    for learner_id in pipeline.T[0:12]:  # eligible treatment learners with a lesson yesterday
        assert outcomes[learner_id]["fact_status"] == "dispatched", outcomes[learner_id]
    for learner_id in pipeline.T[12:17]:  # eligible but no streak
        assert outcomes[learner_id]["fact_status"] is None and outcomes[learner_id]["reason"] == "no_streak"
    for learner_id in pipeline.C[0:17]:  # control: never sent anything, whatever their streak
        assert outcomes[learner_id]["fact_status"] is None and outcomes[learner_id]["reason"] == "not_treatment"
    assert outcomes[pipeline.T[17]]["reason"] == "ineligible:excluded_opted_out"
    assert outcomes[pipeline.T[19]]["reason"] == "missing_preferences"

    nstore = NotificationStore(pipeline.db)
    store = ExperimentStore(pipeline.db)
    try:
        dispatched = nstore.list_dispatches()
        assert sorted(r["learner_id"] for r in dispatched) == sorted(pipeline.T[0:12])
        assert {(r["category"], r["channel"]) for r in dispatched} == {("streak_risk", "push")}
        assert not {r["learner_id"] for r in dispatched} & set(pipeline.C)
        decisions = {r["learner_id"]: r["outcome"] for r in store.list_decisions(EXP_ID)}
        assert [decisions[i] for i in pipeline.T[0:12]] == ["sent"] * 12
        assert {decisions[i] for i in pipeline.C} == {"skipped"}
    finally:
        nstore.close()
        store.close()


# 5 report ---------------------------------------------------------------------------------------------------


def test_report_mastery_matches_the_hand_count(pipeline):
    code, out, err = pipeline.report1
    assert code == 0, err
    arms = _report(pipeline)["arms"]
    t, c = arms["treatment"]["mastery"], arms["control"]["mastery"]
    assert (arms["treatment"]["eligible"], arms["control"]["eligible"]) == (17, 18)
    assert (t["assigned"], t["mastered"], t["failed"], t["missing_outcome"], t["denominator"]) == (17, 8, 3, 6, 17)
    assert (c["assigned"], c["mastered"], c["failed"], c["missing_outcome"], c["denominator"]) == (18, 6, 4, 8, 18)
    assert t["rate"] == pytest.approx(8 / 17) and c["rate"] == pytest.approx(6 / 18)
    assert t["immature"] == 0 and c["immature"] == 0
    # the ineligible learners' passing attempts (T17, C17) are not in any count
    assert t["mastered"] + t["failed"] + t["missing_outcome"] == 17


def test_report_exposure_fatigue_and_contrast(pipeline):
    data = _report(pipeline)
    treatment, control = data["arms"]["treatment"], data["arms"]["control"]
    assert treatment["exposure"]["exposed"] == 6 and treatment["exposure"]["eligible"] == 17
    assert treatment["exposure"]["rate"] == pytest.approx(6 / 17) and treatment["exposure"]["rate"] > 0
    assert control["exposure"]["rate"] is None and control["exposure"]["note"]  # no surface for variant_b
    assert treatment["messages_total"] == 12 and treatment["messages_streak_risk"] == 12
    assert treatment["messages_per_learner_per_day"] > 0
    assert treatment["learner_days_observed"] == pytest.approx(17 * 14.0)
    assert control["messages_total"] == 0 and control["messages_per_learner_per_day"] == 0.0
    assert treatment["max_delivered_in_24h"] == 1 and treatment["delivered_cap_breach_learners"] == 0
    assert treatment["opt_outs"] == 0 and control["opt_outs"] == 0
    assert data["contrast"]["mastery_rate_difference"] == pytest.approx(8 / 17 - 6 / 18)
    assert data["contrast"]["mastery_ci95"]["low"] < data["contrast"]["mastery_rate_difference"] < data["contrast"]["mastery_ci95"]["high"]


def test_the_run_is_valid(pipeline):
    data = _report(pipeline)
    assert data["valid"] is True, data["invalid_reasons"]
    assert data["invalid_reasons"] == []
    assert data["srm_assigned"]["alert"] is False and data["srm_eligible"]["alert"] is False
    assert data["gaming"]["breached"] is False
    assert json.loads(pipeline.report1[1]) == {
        "out": str(pipeline.tmp / "report1.json"), "valid": True, "invalid_reasons": [],
    }


def test_running_report_again_and_after_reopening_gives_byte_identical_json(pipeline):
    assert pipeline.report2[0] == 0
    assert pipeline.report3.returncode == 0, pipeline.report3.stdout + pipeline.report3.stderr
    first, second, third = (path.read_bytes() for path in pipeline.files)
    assert first == second  # run twice in the same process
    assert first == third  # a fresh interpreter in another directory on the same file
    assert len(first) > 1000


# 6 forced invalid run -----------------------------------------------------------------------------------------


def test_a_treatment_guardrail_violation_makes_the_run_invalid_but_keeps_every_number(pipeline, tmp_path):
    copy = str(tmp_path / "engine-copy.db")
    shutil.copy(pipeline.db, copy)  # leave the shared database untouched
    nstore = NotificationStore(copy)
    try:
        record_violation(nstore, pipeline.T[0], (SWEEP_AT + timedelta(minutes=1)).isoformat())
    finally:
        nstore.close()
    out_file = tmp_path / "invalid.json"
    code, out, err = pipeline.run(
        "report", "--db", copy, "--as-of", AS_OF.isoformat(), "--assessment-id", ASSESSMENT_ID,
        "--prefs", pipeline.prefs_path, "--out", out_file,
    )
    assert code == 0, err  # an invalid run is still a successful report
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["valid"] is False
    assert "cap_breach_treatment" in data["invalid_reasons"]
    t, c = data["arms"]["treatment"]["mastery"], data["arms"]["control"]["mastery"]
    assert (t["mastered"], t["failed"], t["missing_outcome"]) == (8, 3, 6)  # numbers are still there
    assert (c["mastered"], c["failed"], c["missing_outcome"]) == (6, 4, 8)
    assert data["arms"]["treatment"]["breach_learners"] == 1
    assert data["arms"]["treatment"]["messages_total"] == 12
    printed = json.loads(out)
    assert printed["valid"] is False and "cap_breach_treatment" in printed["invalid_reasons"]
