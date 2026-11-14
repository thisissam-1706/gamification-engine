"""M7 entry points: preference file loader, learner listing and scripts/run_experiment.py.

Written before the implementation. Assumed new code:

* ``src/experiments/prefs.py``: ``load_preferences(path) -> dict[str, LearnerPreferences]`` from JSON
  ``{"<learner_id>": {"timezone": ..., "opted_out_channels": [...], "opted_out_categories": [...]}}``.
  Missing optional keys default to UTC and empty sets. Anything wrong raises ValueError whose message
  names the learner id and the offending value (unknown channel or category, bad timezone, non-object
  entry, a non-list set, an unknown key); invalid JSON or a non-object top level raises ValueError too.
  Nothing is skipped silently.
* ``src/experiments/learners.py``: ``list_learner_ids(application) -> list[str]``: every learner with a
  stored Created event, sorted, from the event log.
* ``scripts/run_experiment.py`` with ``main(argv=None) -> int`` and subcommands (each takes ``--db PATH``;
  stdout is ONE JSON document; ``python scripts/run_experiment.py ...`` works from any directory):
  - ``assign --prefs FILE --assigned-at ISO [--exp-id exp_007]`` -> {"exp_id", "assigned_at", "learners",
    "newly_assigned", "already_assigned", "eligible", "excluded"}; naive --assigned-at: exit 2, message on stderr.
  - ``sweep --prefs FILE --now ISO`` -> {"now", "outcomes": [{"learner_id", "reason", "fact_status"}]} (reason is
    null when something was handed to the dispatcher; fact_status is null when nothing was); naive --now: exit 2.
  - ``report --as-of ISO --assessment-id ID [--assessment-version N] [--prefs FILE] --out FILE`` writes
    ``ExperimentRunResult.to_dict()`` to the file and prints {"out", "valid", "invalid_reasons"}; exit 0 even
    when the run is invalid. Awards come from the abuse service on the same database.
  - An unreadable prefs file (ValueError from the loader): exit 2, the message on stderr (assumption).
* One sqlite file ``--db`` holds the events and every store (as in ``verify_engine_reproduction``).
"""

import importlib
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.experiments.runner import awards_from_abuse_service, run_experiment
from src.experiments.store import ExperimentStore
from src.abuse.service import AbuseDetectionService
from src.abuse.store import AbuseSignalStore
from src.ingestion.evidence import EvidenceStore
from src.notifications.models import ChannelType, LearnerPreferences, NotificationCategory
from src.notifications.store import NotificationStore
from src.state_machine.learner import LearnerApplication
from src.state_machine.notification_log import read_all_notifications
from tests.experiments.support import (
    ASSESSMENT_ID,
    ASSIGNED_AT,
    EXP_ID,
    make_learner,
    record_attempt,
    record_violation,
    stored_event_names,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_experiment.py"
AS_OF_30 = ASSIGNED_AT + timedelta(days=30)
NOW = datetime(2026, 10, 12, 15, 40, tzinfo=timezone.utc)  # 21:10 in Asia/Kolkata
CREATED_OLD = datetime(2026, 9, 1, tzinfo=timezone.utc)
CREATED_YOUNG = ASSIGNED_AT - timedelta(days=1)


# lazy access, so that each test fails on its own while the code is missing ------------------------------


def _prefs_module():
    return importlib.import_module("src.experiments.prefs")


def _learners_module():
    return importlib.import_module("src.experiments.learners")


class _Script:
    """Loads scripts/run_experiment.py when ``main`` is first called (so a missing script fails the test, not the setup)."""

    def main(self, argv):
        spec = importlib.util.spec_from_file_location("m7_run_experiment_script", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # FileNotFoundError while the script does not exist
        return module.main(argv)


@pytest.fixture
def script():
    return _Script()


def _application(db: str) -> LearnerApplication:
    return LearnerApplication(env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db})


# 1 load_preferences ---------------------------------------------------------------------------------------


def _write(tmp_path: Path, content, name="prefs.json") -> str:
    path = tmp_path / name
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return str(path)


def test_load_preferences_reads_every_field(tmp_path):
    path = _write(tmp_path, {
        "L1": {
            "timezone": "Asia/Kolkata",
            "opted_out_channels": ["push"],
            "opted_out_categories": ["streak_risk", "general"],
        },
    })
    prefs = _prefs_module().load_preferences(path)
    assert prefs == {
        "L1": LearnerPreferences(
            learner_id="L1",
            timezone="Asia/Kolkata",
            opted_out_channels={ChannelType.PUSH},
            opted_out_categories={NotificationCategory.STREAK_RISK, NotificationCategory.GENERAL},
        )
    }


def test_load_preferences_defaults_missing_optional_keys(tmp_path):
    prefs = _prefs_module().load_preferences(_write(tmp_path, {"L1": {}, "L2": {"timezone": "Europe/London"}}))
    assert prefs["L1"] == LearnerPreferences(learner_id="L1", timezone="UTC")
    assert prefs["L1"].opted_out_channels == set() and prefs["L1"].opted_out_categories == set()
    assert prefs["L2"].timezone == "Europe/London" and prefs["L2"].opted_out_channels == set()


def test_load_preferences_of_an_empty_object_is_empty(tmp_path):
    assert _prefs_module().load_preferences(_write(tmp_path, {})) == {}


@pytest.mark.parametrize(
    "entry, token",
    [
        ({"opted_out_channels": ["sms"]}, "sms"),
        ({"opted_out_categories": ["spam"]}, "spam"),
        ({"timezone": "Mars/Phobos"}, "Mars/Phobos"),
        ("push", "push"),  # not an object
        (["push"], "list"),  # not an object
        ({"opted_out_channels": "push"}, "opted_out_channels"),  # a string, not a list
        ({"timezone": 5}, "timezone"),
        ({"opted_in": ["push"]}, "opted_in"),  # unknown key: never silently ignored
    ],
    ids=["channel", "category", "timezone", "string-entry", "list-entry", "string-set", "timezone-type", "unknown-key"],
)
def test_load_preferences_rejects_bad_entries_naming_the_learner_and_the_problem(tmp_path, entry, token):
    path = _write(tmp_path, {"L-good": {}, "L-bad-42": entry})
    with pytest.raises(ValueError) as excinfo:
        _prefs_module().load_preferences(path)
    message = str(excinfo.value)
    assert "L-bad-42" in message
    assert token in message
    assert "L-good" not in message


def test_load_preferences_rejects_invalid_json_and_a_non_object_document(tmp_path):
    with pytest.raises(ValueError) as excinfo:
        _prefs_module().load_preferences(_write(tmp_path, '{"L1": {"timezone": '))
    assert "json" in str(excinfo.value).lower()
    with pytest.raises(ValueError) as excinfo:
        _prefs_module().load_preferences(_write(tmp_path, ["L1"], name="list.json"))
    assert "object" in str(excinfo.value).lower()


def test_load_preferences_stops_at_the_first_bad_learner_and_returns_nothing_partial(tmp_path):
    path = _write(tmp_path, {"A": {"timezone": "Asia/Kolkata"}, "B": {"opted_out_channels": ["fax"]}})
    with pytest.raises(ValueError, match="B"):
        _prefs_module().load_preferences(path)


# 2 list_learner_ids -----------------------------------------------------------------------------------------


def test_list_learner_ids_returns_every_created_learner_sorted(tmp_path):
    application = _application(str(tmp_path / "events.db"))
    try:
        assert _learners_module().list_learner_ids(application) == []
        ids = [
            make_learner(application, "variant_a", nth=0, created=CREATED_OLD),  # assigned
            make_learner(application, "variant_b", nth=0, created=CREATED_OLD, assigned_at=None),  # not assigned
            make_learner(application, "variant_a", nth=1, created=CREATED_OLD, assigned_at=None),
        ]
        listed = _learners_module().list_learner_ids(application)
        assert listed == sorted(ids)
        assert all(isinstance(i, str) for i in listed)
        assert len(set(listed)) == len(listed)  # a learner with several events appears once
    finally:
        application.close()


# the CLI world ----------------------------------------------------------------------------------------------


def _world(tmp_path) -> SimpleNamespace:
    """One db with four learners: L1 eligible treatment (streak at risk), L2 opted out, L3 too young, L4 no prefs."""
    db = str(tmp_path / "engine.db")
    application = _application(db)
    try:
        ids = {
            "L1": make_learner(application, "variant_a", nth=0, created=CREATED_OLD, assigned_at=None),
            "L2": make_learner(application, "variant_b", nth=0, created=CREATED_OLD, assigned_at=None),
            "L3": make_learner(application, "variant_a", nth=1, created=CREATED_YOUNG, assigned_at=None),
            "L4": make_learner(application, "variant_b", nth=1, created=CREATED_OLD, assigned_at=None),
        }
        learner = application.repository.get(UUID(ids["L1"]))  # activity yesterday (IST), so the streak is at risk
        learner.complete_lesson(
            "c-1", "lesson-1", activity_date="2026-10-11",
            occurred_at=datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc), timezone="Asia/Kolkata",
        )
        application.save(learner)
    finally:
        application.close()
    prefs = {
        ids["L1"]: {"timezone": "Asia/Kolkata"},
        ids["L2"]: {"timezone": "Asia/Kolkata", "opted_out_channels": ["push"]},
        ids["L3"]: {"timezone": "Asia/Kolkata"},
        # L4 has no entry: missing preferences
    }
    prefs_path = _write(tmp_path, prefs)
    return SimpleNamespace(db=db, ids=ids, prefs_path=prefs_path, prefs=prefs, tmp_path=tmp_path)


def _cli(script, capsys, *argv):
    capsys.readouterr()  # drop earlier output
    code = script.main([str(a) for a in argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _assign(script, capsys, world, assigned_at=ASSIGNED_AT, *extra):
    return _cli(
        script, capsys, "assign", "--db", world.db, "--prefs", world.prefs_path,
        "--assigned-at", assigned_at.isoformat(), *extra,
    )


def _eligibility(db: str, exp_id: str = EXP_ID) -> dict[str, dict]:
    store = ExperimentStore(db)
    try:
        return {row["learner_id"]: row for row in store.list_eligibility(exp_id)}
    finally:
        store.close()


# 3 assign -----------------------------------------------------------------------------------------------------


def test_assign_assigns_every_learner_and_records_symmetric_eligibility(script, capsys, tmp_path):
    world = _world(tmp_path)
    code, out, err = _assign(script, capsys, world)

    assert code == 0, err
    summary = json.loads(out)
    assert summary["exp_id"] == EXP_ID and summary["learners"] == 4
    assert (summary["newly_assigned"], summary["already_assigned"]) == (4, 0)
    assert (summary["eligible"], summary["excluded"]) == (1, 3)
    assert summary["assigned_at"] == ASSIGNED_AT.isoformat()

    rows = _eligibility(world.db)
    assert {i: (r["variant"], bool(r["eligible"]), r["reason"]) for i, r in rows.items()} == {
        world.ids["L1"]: ("variant_a", True, None),
        world.ids["L2"]: ("variant_b", False, "excluded_opted_out"),
        world.ids["L3"]: ("variant_a", False, "excluded_tenure"),
        world.ids["L4"]: ("variant_b", False, "missing_preferences"),
    }


def test_assign_uses_the_exp_id_option(script, capsys, tmp_path):
    world = _world(tmp_path)
    code, out, err = _assign(script, capsys, world, ASSIGNED_AT, "--exp-id", "exp_other")
    assert code == 0, err
    assert json.loads(out)["exp_id"] == "exp_other"
    assert len(_eligibility(world.db, "exp_other")) == 4 and _eligibility(world.db) == {}
    application = _application(world.db)
    try:
        learner = application.repository.get(UUID(world.ids["L1"]))
        assert set(learner.experiment_assignments) == {"exp_other"}
    finally:
        application.close()


def test_assign_with_a_naive_assigned_at_exits_2_and_writes_nothing(script, capsys, tmp_path):
    world = _world(tmp_path)
    code, out, err = _assign(script, capsys, world, datetime(2026, 10, 1, 12, 0))  # no offset
    assert code == 2
    assert "timezone" in err.lower() and "assigned-at" in err
    assert out.strip() == ""
    assert _eligibility(world.db) == {}
    application = _application(world.db)
    try:
        assert all(
            "ExperimentAssigned" not in stored_event_names(application, i) for i in world.ids.values()
        )
    finally:
        application.close()


def test_assign_with_a_bad_prefs_file_exits_2_naming_the_learner_and_writes_nothing(script, capsys, tmp_path):
    world = _world(tmp_path)
    bad = {world.ids["L1"]: {"opted_out_channels": ["sms"]}}
    world.prefs_path = _write(tmp_path, bad, name="bad.json")
    code, out, err = _assign(script, capsys, world)
    assert code == 2
    assert world.ids["L1"] in err and "sms" in err
    assert _eligibility(world.db) == {}


# 3 sweep ------------------------------------------------------------------------------------------------------


def test_sweep_runs_the_real_dispatcher_with_the_experiment_store(script, capsys, tmp_path):
    world = _world(tmp_path)
    assert _assign(script, capsys, world)[0] == 0

    code, out, err = _cli(script, capsys, "sweep", "--db", world.db, "--prefs", world.prefs_path, "--now", NOW.isoformat())
    assert code == 0, err
    report = json.loads(out)
    assert report["now"] == NOW.isoformat()
    outcomes = {o["learner_id"]: o for o in report["outcomes"]}
    assert set(outcomes) == set(world.ids.values())
    l1 = outcomes[world.ids["L1"]]
    assert (l1["reason"], l1["fact_status"]) == (None, "dispatched")
    assert outcomes[world.ids["L2"]] == {"learner_id": world.ids["L2"], "reason": "ineligible:excluded_opted_out", "fact_status": None}
    assert outcomes[world.ids["L3"]]["reason"] == "ineligible:excluded_tenure"
    assert outcomes[world.ids["L4"]]["reason"] == "missing_preferences"

    store = ExperimentStore(world.db)
    nstore = NotificationStore(world.db)
    try:
        decisions = {r["learner_id"]: r for r in store.list_decisions(EXP_ID)}
        assert decisions[world.ids["L1"]]["outcome"] == "sent"
        assert decisions[world.ids["L1"]]["fact_status"] == "dispatched"
        assert {decisions[i]["outcome"] for i in (world.ids["L2"], world.ids["L3"], world.ids["L4"])} == {"skipped"}
        rows = nstore.list_dispatches()
        assert [(r["learner_id"], r["category"], r["channel"]) for r in rows] == [
            (world.ids["L1"], "streak_risk", "push")
        ]
    finally:
        store.close()
        nstore.close()


def test_sweep_with_a_naive_now_exits_2_and_sends_nothing(script, capsys, tmp_path):
    world = _world(tmp_path)
    assert _assign(script, capsys, world)[0] == 0
    code, out, err = _cli(
        script, capsys, "sweep", "--db", world.db, "--prefs", world.prefs_path, "--now", "2026-10-12T15:40:00"
    )
    assert code == 2 and "timezone" in err.lower() and out.strip() == ""
    nstore = NotificationStore(world.db)
    try:
        assert nstore.list_dispatches() == []
    finally:
        nstore.close()


# 3 report -------------------------------------------------------------------------------------------------------


def _report_world(script, capsys, tmp_path):
    """assign, then: L1 (treatment) has two attempts of different versions and a cap violation."""
    world = _world(tmp_path)
    assert _assign(script, capsys, world)[0] == 0
    application = _application(world.db)
    try:
        record_attempt(application, world.ids["L1"], "att-v1", passed=True, version=1)
        record_attempt(application, world.ids["L1"], "att-v2", passed=False, version=2)
    finally:
        application.close()
    nstore = NotificationStore(world.db)
    try:
        record_violation(nstore, world.ids["L1"], (ASSIGNED_AT + timedelta(days=1)).isoformat())
    finally:
        nstore.close()
    return world


def _direct_report(world, **kwargs) -> dict:
    """The same run, made in-process with the library, for comparison with the script's file."""
    application = _application(world.db)
    evidence = EvidenceStore(world.db)
    signals = AbuseSignalStore(world.db)
    store = ExperimentStore(world.db)
    nstore = NotificationStore(world.db)
    try:
        awards = awards_from_abuse_service(AbuseDetectionService(application, evidence, signals))
        return run_experiment(
            application, store, nstore, awards, assessment_id=ASSESSMENT_ID, as_of=AS_OF_30, **kwargs
        ).to_dict()
    finally:
        for opened in (nstore, store, signals, evidence, application):
            opened.close()


def test_report_writes_the_run_json_and_exits_0_even_when_the_run_is_invalid(script, capsys, tmp_path):
    world = _report_world(script, capsys, tmp_path)
    out_file = tmp_path / "report.json"
    code, out, err = _cli(
        script, capsys, "report", "--db", world.db, "--as-of", AS_OF_30.isoformat(),
        "--assessment-id", ASSESSMENT_ID, "--out", out_file,
    )
    assert code == 0, err
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["valid"] is False
    assert "cap_breach_treatment" in data["invalid_reasons"]
    assert data == json.loads(json.dumps(_direct_report(world), sort_keys=True))  # same numbers as the library gives
    printed = json.loads(out)
    assert printed["out"] == str(out_file)
    assert printed["valid"] is False and "cap_breach_treatment" in printed["invalid_reasons"]
    # the awards came from the abuse service: the treatment cohort has XP in the week of L1's lesson
    weeks = data["gaming"]["weeks"]
    assert any(r["total_xp"] > 0 for reports in weeks.values() for r in reports)


def test_report_with_prefs_computes_opt_outs_and_without_them_says_so(script, capsys, tmp_path):
    world = _report_world(script, capsys, tmp_path)
    plain = tmp_path / "plain.json"
    with_prefs = tmp_path / "with-prefs.json"
    common = ("report", "--db", world.db, "--as-of", AS_OF_30.isoformat(), "--assessment-id", ASSESSMENT_ID)
    assert _cli(script, capsys, *common, "--out", plain)[0] == 0
    assert _cli(script, capsys, *common, "--prefs", world.prefs_path, "--out", with_prefs)[0] == 0
    without, supplied = (json.loads(p.read_text(encoding="utf-8")) for p in (plain, with_prefs))
    assert without["arms"]["treatment"]["opt_outs"] is None and without["notes"]
    assert supplied["arms"]["treatment"]["opt_outs"] == 0 and supplied["notes"] == []


def test_report_assessment_version_removes_the_mixed_versions_reason(script, capsys, tmp_path):
    world = _report_world(script, capsys, tmp_path)
    mixed, single = tmp_path / "mixed.json", tmp_path / "single.json"
    common = ("report", "--db", world.db, "--as-of", AS_OF_30.isoformat(), "--assessment-id", ASSESSMENT_ID)
    assert _cli(script, capsys, *common, "--out", mixed)[0] == 0
    assert _cli(script, capsys, *common, "--assessment-version", "1", "--out", single)[0] == 0
    assert "mixed_assessment_versions" in json.loads(mixed.read_text(encoding="utf-8"))["invalid_reasons"]
    version_one = json.loads(single.read_text(encoding="utf-8"))
    assert "mixed_assessment_versions" not in version_one["invalid_reasons"]
    assert version_one == json.loads(json.dumps(_direct_report(world, assessment_version=1), sort_keys=True))


def test_report_with_a_naive_as_of_exits_2_and_writes_no_file(script, capsys, tmp_path):
    world = _report_world(script, capsys, tmp_path)
    out_file = tmp_path / "never.json"
    code, out, err = _cli(
        script, capsys, "report", "--db", world.db, "--as-of", "2026-10-31T12:00:00",
        "--assessment-id", ASSESSMENT_ID, "--out", out_file,
    )
    assert code == 2 and "timezone" in err.lower()
    assert not out_file.exists()


# 3 standalone ---------------------------------------------------------------------------------------------------


def test_the_script_runs_standalone_from_any_directory(tmp_path):
    world = _world(tmp_path)
    work = tmp_path / "elsewhere"
    work.mkdir()
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "assign", "--db", world.db, "--prefs", world.prefs_path,
         "--assigned-at", ASSIGNED_AT.isoformat()],
        cwd=work, capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    summary = json.loads(completed.stdout)
    assert (summary["learners"], summary["newly_assigned"], summary["eligible"]) == (4, 4, 1)
    assert len(_eligibility(world.db)) == 4


# 4 idempotence --------------------------------------------------------------------------------------------------


def test_rerunning_assign_is_a_no_op(script, capsys, tmp_path):
    world = _world(tmp_path)
    assert _assign(script, capsys, world)[0] == 0
    rows_before = _eligibility(world.db)
    application = _application(world.db)
    try:
        events_before = len(read_all_notifications(application))
    finally:
        application.close()

    # even with changed preferences and a different time, the first record wins
    changed = {i: {"timezone": "Europe/London", "opted_out_channels": ["push"]} for i in world.ids.values()}
    world.prefs_path = _write(tmp_path, changed, name="changed.json")
    code, out, err = _assign(script, capsys, world, ASSIGNED_AT + timedelta(days=2))

    assert code == 0, err
    again = json.loads(out)
    assert (again["newly_assigned"], again["already_assigned"]) == (0, 4)
    assert _eligibility(world.db) == rows_before
    application = _application(world.db)
    try:
        assert len(read_all_notifications(application)) == events_before  # no new event of any kind
        assert all(stored_event_names(application, i).count("ExperimentAssigned") == 1 for i in world.ids.values())
    finally:
        application.close()
