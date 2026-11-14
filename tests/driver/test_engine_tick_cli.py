"""main(): opens the stores, runs one tick, prints sorted-key JSON, exit code 1 on a step error."""

import json
from datetime import datetime, timezone

from tests.experiments.support import make_learner, open_persisted

CREATED = datetime(2026, 9, 1, tzinfo=timezone.utc)
NOW = "2026-10-12T15:40:00+00:00"


def _main(argv):
    from scripts.run_engine_tick import main

    return main(argv)


def _db_and_prefs(tmp_path):
    with open_persisted(tmp_path) as (application, _evidence):
        ids = [
            make_learner(application, "variant_a" if n % 2 == 0 else "variant_b", nth=n // 2,
                         created=CREATED, assigned_at=None)
            for n in range(4)
        ]
    prefs = tmp_path / "prefs.json"
    prefs.write_text(json.dumps({i: {"timezone": "Asia/Kolkata"} for i in ids}), encoding="utf-8")
    return str(tmp_path / "events.db"), str(prefs)


def test_main_runs_a_tick_and_prints_json(tmp_path, capsys):
    db, prefs = _db_and_prefs(tmp_path)
    out = tmp_path / "out" / "tick.json"

    code = _main(["--db", db, "--leaderboard-db", str(tmp_path / "board.db"),
                  "--prefs", prefs, "--now", NOW, "--out", str(out)])

    printed = capsys.readouterr().out
    document = json.loads(printed)
    assert code == 0, document
    assert set(document["steps"]) == {
        "weekly_cycle", "assign", "poll", "quest_expiry", "remediation", "streak_risk", "abuse", "reconcile"
    }
    assert json.loads(out.read_text(encoding="utf-8")) == document


def test_main_rejects_a_naive_now_with_exit_code_2(tmp_path, capsys):
    db, prefs = _db_and_prefs(tmp_path)

    code = _main(["--db", db, "--leaderboard-db", str(tmp_path / "board.db"),
                  "--prefs", prefs, "--now", "2026-10-12T15:40:00"])

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "timezone" in captured.err


def test_main_passes_the_cohort_size_flags_to_the_weekly_cycle(tmp_path, capsys):
    db, prefs = _db_and_prefs(tmp_path)

    code = _main(["--db", db, "--leaderboard-db", str(tmp_path / "board.db"),
                  "--prefs", prefs, "--now", NOW, "--cohort-min-size", "2", "--cohort-max-size", "10"])

    document = json.loads(capsys.readouterr().out)
    assert "error" not in document["steps"]["weekly_cycle"], document["steps"]["weekly_cycle"]
    assert document["steps"]["weekly_cycle"]["assignments_built"] == 4
    assert code == 0, document
