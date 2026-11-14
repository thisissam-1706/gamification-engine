"""Lifetime XP and this week's leaderboard score are different numbers; the demo must label them apart.

Seeded learners carry prior-week lessons, so ``learner.xp`` (lifetime, shown on the XP card) is higher than
the weekly score in the leaderboard rows (``leaderboard.*_rows[].xp``). The numbers are unchanged; only the
page labels are checked: "Total XP" on the XP card, "XP this week" on leaderboard rows.
"""

from pathlib import Path
import re

import pytest

from tests.demo.test_demo_backend import _fresh_http_client, _http_learner_id


def _own_row(home: dict) -> dict:
    return next(row for row in home["leaderboard"]["cohort_rows"] if row["is_you"])


def test_lifetime_xp_and_weekly_score_can_differ_in_the_home_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _fresh_http_client(monkeypatch, tmp_path) as client:
        homes = [
            client.get(f"/api/learners/{learner['id']}/home").json()
            for learner in client.get("/api/learners").json()
        ]

    differing = [h for h in homes if h["learner"]["xp"] != _own_row(h)["xp"]]
    assert differing, "expected seeded learners whose lifetime XP is above this week's score"
    for home in differing:
        assert home["learner"]["xp"] > _own_row(home)["xp"]  # lifetime includes the earlier week
    assert "this week" in homes[0]["leaderboard"]["tie_rule"]  # the footnote already says what is ranked


def test_the_page_labels_lifetime_xp_and_the_weekly_score_differently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _fresh_http_client(monkeypatch, tmp_path) as client:
        index = client.get("/").text
        script = client.get("/app.js").text

    # XP card: the lifetime number is "Total XP", not a bare "XP".
    assert re.search(r'class="xp-unit">Total XP<', index)
    assert not re.search(r'class="xp-unit">XP<', index)
    # Leaderboard rows: the weekly score is "XP this week", not a bare "XP".
    assert 'fmt(row.xp) + " XP this week"' in script
    assert 'fmt(row.xp) + " XP"' not in script
