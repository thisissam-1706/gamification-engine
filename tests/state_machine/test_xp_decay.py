"""M6 / AT-6: XP decay as opt-in rule set v2, decided at the decision boundary."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

from src.ingestion.config import IngestConfig
from src.ingestion.evidence import EvidenceStore
from src.leaderboard.projection import LeaderboardProjection
from src.leaderboard.store import SQLiteLeaderboardStore
from src.state_machine.learner import Learner, LearnerApplication
from src.state_machine.projection import rebuild_learner_state
from src.state_machine.rules import (
    _REGISTRY,
    DECAY_RULESETS_FILE,
    RULESETS,
    RuleConfigError,
    RuleSet,
    get_ruleset,
    load_decay_rulesets,
    load_rulesets,
    register_decay_rulesets,
    xp_amount_for,
    xp_for_completion,
)
from tests.ingestion.support import ingest_lesson_completed
from tests.ingestion.test_ingestion_service import _envelope

V1_SHA256 = "ba6d0a3821ebdb1358840ec7a0dd2f67136b4eeb3062d79f3369b53cda6b4430"
BASE = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def v2(monkeypatch):
    """Registers the packaged decay rule sets for one test only (no leak into RULESETS)."""
    for version, ruleset in load_decay_rulesets().items():
        monkeypatch.setitem(_REGISTRY, version, ruleset)
    return "v2"


def _at(minutes: int) -> str:
    return (BASE + timedelta(minutes=minutes)).isoformat()


def _complete(learner, completion_id, lesson_id, minute, version, kind=None):
    return learner.complete_lesson(
        completion_id,
        lesson_id,
        occurred_at=_at(minute),
        timezone="UTC",
        rule_version=version,
        completion_kind=kind,
    )


def _awards(learner):
    return [
        (e.completion_id, e.xp_amount, e.repeat_index)
        for e in learner.pending_events
        if isinstance(e, Learner.XPAwarded)
    ]


# -- pure function and rule file ------------------------------------------------


@pytest.mark.parametrize("n, expected", [(0, 100), (1, 50), (2, 30), (3, 10), (4, 0), (9, 0)])
def test_at6_decay_numbers(v2, n, expected):
    assert xp_for_completion("v2", n) == expected


def test_v1_has_no_decay_and_is_flat():
    for n in (0, 1, 2, 10):
        assert xp_for_completion("v1", n) == 100 == xp_amount_for("v1")
    assert get_ruleset("v1").decay is None


def test_negative_repeat_count_is_rejected():
    with pytest.raises(ValueError):
        xp_for_completion("v1", -1)


def test_v1_digest_unchanged_and_v2_not_in_default_registry():
    assert get_ruleset("v1").digest == V1_SHA256
    assert "v2" not in RULESETS
    assert list(load_rulesets()) == ["v1"]


def test_decay_file_loads_pinned_v2():
    loaded = load_decay_rulesets()
    assert list(loaded) == ["v2"]
    v2 = loaded["v2"]
    assert dict(v2.decay) == {"first_repeat_percent": 50, "step_xp": 20}
    v1 = RULESETS["v1"]
    assert (v2.xp_amount, dict(v2.quest_targets), v2.badge_threshold, v2.badge_id) == (
        v1.xp_amount,
        dict(v1.quest_targets),
        v1.badge_threshold,
        v1.badge_id,
    )
    assert v2.digest != v1.digest
    document = json.loads(DECAY_RULESETS_FILE.read_text(encoding="utf-8"))
    assert document["v2"]["sha256"] == v2.digest


def test_register_decay_rulesets_is_explicit_opt_in(monkeypatch):
    monkeypatch.delitem(_REGISTRY, "v2", raising=False)
    assert "v2" not in RULESETS
    assert register_decay_rulesets() == ["v2"]
    try:
        assert "v2" in RULESETS
        assert register_decay_rulesets() == ["v2"]  # idempotent
    finally:
        _REGISTRY.pop("v2", None)


def _write_decay(tmp_path: Path, **overrides) -> Path:
    entry = json.loads(DECAY_RULESETS_FILE.read_text(encoding="utf-8"))["v2"]
    entry.update(overrides)
    path = tmp_path / "decay.json"
    path.write_text(json.dumps({"v2": entry}), encoding="utf-8")
    return path


def test_tampered_decay_values_without_new_digest_are_rejected(tmp_path):
    path = _write_decay(tmp_path, decay={"first_repeat_percent": 60, "step_xp": 20})
    with pytest.raises(RuleConfigError, match="does not match its sha256"):
        load_rulesets(path)
    path = _write_decay(tmp_path, decay={"first_repeat_percent": 50, "step_xp": 25})
    with pytest.raises(RuleConfigError, match="does not match its sha256"):
        load_rulesets(path)


def test_dropping_decay_with_old_digest_is_rejected(tmp_path):
    entry = json.loads(DECAY_RULESETS_FILE.read_text(encoding="utf-8"))["v2"]
    del entry["decay"]
    path = tmp_path / "decay.json"
    path.write_text(json.dumps({"v2": entry}), encoding="utf-8")
    with pytest.raises(RuleConfigError, match="does not match its sha256"):
        load_rulesets(path)


def test_unknown_keys_are_still_rejected(tmp_path):
    with pytest.raises(RuleConfigError, match="unknown keys"):
        load_rulesets(_write_decay(tmp_path, surprise=1))
    with pytest.raises(RuleConfigError, match="decay"):
        load_rulesets(_write_decay(tmp_path, decay={"first_repeat_percent": 50, "step_xp": 20, "x": 1}))


@pytest.mark.parametrize(
    "decay",
    [
        {"first_repeat_percent": 0, "step_xp": 20},
        {"first_repeat_percent": 101, "step_xp": 20},
        {"first_repeat_percent": 50, "step_xp": 0},
        {"first_repeat_percent": 50.0, "step_xp": 20},
        {"first_repeat_percent": True, "step_xp": 20},
        {"first_repeat_percent": 50},
        "50",
    ],
)
def test_invalid_decay_values_are_rejected(decay):
    with pytest.raises(RuleConfigError):
        RuleSet(
            xp_amount=100,
            quest_targets={"lesson_starter_v1": 3},
            badge_threshold=7,
            badge_id="week_warrior_v1",
            decay=decay,
        )


def test_decay_is_read_only_and_copied():
    source = {"first_repeat_percent": 50, "step_xp": 20}
    ruleset = RuleSet(100, {"lesson_starter_v1": 3}, 7, "week_warrior_v1", decay=source)
    source["step_xp"] = 1
    assert ruleset.decay["step_xp"] == 20
    with pytest.raises(TypeError):
        ruleset.decay["step_xp"] = 1  # type: ignore[index]


# -- aggregate: decision boundary -----------------------------------------------


def test_aggregate_applies_at6_sequence(v2):
    learner = Learner()
    for i in range(5):
        assert _complete(learner, f"c{i}", "l_492", i * 10, v2)
    assert [(a[1], a[2]) for a in _awards(learner)] == [
        (100, 0),
        (50, 1),
        (30, 2),
        (10, 3),
        (0, 4),
    ]
    assert learner.xp_total == 190


def test_review_and_remediation_neither_decay_nor_advance_n(v2):
    learner = Learner()
    _complete(learner, "c0", "l1", 0, v2, "lesson")
    _complete(learner, "r1", "l1", 10, v2, "review")
    _complete(learner, "m1", "l1", 20, v2, "remediation")
    _complete(learner, "r2", "l1", 30, v2, "review")
    _complete(learner, "c1", "l1", 40, v2)  # kind absent counts as lesson: N == 1
    awards = {a[0]: (a[1], a[2]) for a in _awards(learner)}
    assert awards == {
        "c0": (100, 0),
        "r1": (100, 0),
        "m1": (100, 0),
        "r2": (100, 0),
        "c1": (50, 1),
    }


def test_n_counts_only_eligible_kinds_and_lessons_are_independent(v2):
    learner = Learner()
    _complete(learner, "a0", "A", 0, v2, "review")
    _complete(learner, "a1", "A", 1, v2, "lesson")  # review did not count: N == 0
    _complete(learner, "b0", "B", 2, v2, "lesson")
    _complete(learner, "a2", "A", 3, v2, "lesson")  # N == 1
    _complete(learner, "b1", "B", 4, v2, "lesson")  # N == 1
    assert {a[0]: (a[1], a[2]) for a in _awards(learner)} == {
        "a0": (100, 0),
        "a1": (100, 0),
        "b0": (100, 0),
        "a2": (50, 1),
        "b1": (50, 1),
    }


def test_unknown_completion_kind_is_rejected():
    with pytest.raises(ValueError):
        Learner().complete_lesson("c", "l", occurred_at=_at(0), timezone="UTC", completion_kind="nope")


def test_v1_events_unchanged_for_repeats():
    learner = Learner()
    for i in range(4):
        _complete(learner, f"c{i}", "l1", i, "v1")
    assert [a[1] for a in _awards(learner)] == [100, 100, 100, 100]
    assert learner.xp_total == 400


def test_explicit_xp_amount_still_wins(v2):
    learner = Learner()
    learner.complete_lesson("c0", "l", xp_amount=7, occurred_at=_at(0), timezone="UTC", rule_version=v2)
    learner.complete_lesson("c1", "l", xp_amount=7, occurred_at=_at(1), timezone="UTC", rule_version=v2)
    assert [(a[1], a[2]) for a in _awards(learner)] == [(7, 0), (7, 1)]


# -- persistence, replay, old streams ----------------------------------------------


def _env(db):
    return {"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": str(db)}


def _state(learner):
    return (
        learner.xp_total,
        learner.new_streak,
        dict(learner.eligible_lesson_counts),
        dict(learner.lesson_completion_counts),
        dict(learner.applied_rule_versions),
    )


def test_replay_of_v2_history_rebuilds_identical_state(v2, tmp_path):
    db = tmp_path / "events.db"
    app = LearnerApplication(env=_env(db))
    learner = Learner()
    for i in range(4):
        _complete(learner, f"c{i}", "l1", i, v2)
    _complete(learner, "r0", "l1", 10, v2, "review")
    app.save(learner)
    live = _state(learner)
    events = list(app.events.get(learner.id))
    app.close()

    app2 = LearnerApplication(env=_env(db))
    rebuilt = app2.repository.get(learner.id)
    assert _state(rebuilt) == live
    assert rebuilt.xp_total == 100 + 50 + 30 + 10 + 100
    assert rebuild_learner_state(events).xp_total == rebuilt.xp_total
    stored = [(e.xp_amount, e.repeat_index) for e in events if isinstance(e, Learner.XPAwarded)]
    assert stored == [(100, 0), (50, 1), (30, 2), (10, 3), (100, 0)]
    app2.close()


def test_replay_never_recomputes_xp_when_rules_change(v2, monkeypatch, tmp_path):
    db = tmp_path / "events.db"
    app = LearnerApplication(env=_env(db))
    learner = Learner()
    for i in range(3):
        _complete(learner, f"c{i}", "l1", i, v2)
    app.save(learner)
    app.close()
    monkeypatch.setitem(
        _REGISTRY,
        "v2",
        RuleSet(999, {"lesson_starter_v1": 3}, 7, "week_warrior_v1", decay={"first_repeat_percent": 10, "step_xp": 1}),
    )
    app2 = LearnerApplication(env=_env(db))
    assert app2.repository.get(learner.id).xp_total == 180
    app2.close()


def test_old_style_events_without_kind_or_repeat_index_still_replay(tmp_path):
    """Streams stored before M6 carry neither completion_kind nor repeat_index."""
    db = tmp_path / "events.db"
    app = LearnerApplication(env=_env(db))
    learner = Learner()
    # Call the folds the way pre-M6 code did: no completion_kind / repeat_index arguments.
    learner._record_lesson_completion("c0", "l1", "2026-10-12", "UTC", _at(0), None)
    learner._award_xp("xp:c0", "c0", 100, "v1")  # no repeat_index / completion_kind
    app.save(learner)
    app.close()
    app2 = LearnerApplication(env=_env(db))
    rebuilt = app2.repository.get(learner.id)
    events = list(app2.events.get(learner.id))
    award = next(e for e in events if isinstance(e, Learner.XPAwarded))
    assert award.repeat_index is None
    assert award.completion_kind is None
    assert rebuilt.xp_total == 100
    assert rebuilt.eligible_lesson_counts == {"l1": 1}
    app2.close()


# -- ingestion and leaderboard ---------------------------------------------------


def test_ingestion_passes_completion_kind_and_decays(v2, tmp_path):
    db = tmp_path / "events.db"
    app = LearnerApplication(env=_env(db))
    evidence = EvidenceStore(str(db))
    learner = Learner()
    app.save(learner)
    learner_id = str(learner.id)
    config = IngestConfig(v2)

    def send(n, kind):
        env = _envelope(learner_id, f"cmp-{n}", f"00000000-0000-4000-8000-00000000a{n:03d}")
        env["dedup_key"] = f"{env['event_id']}:{v2}"
        env["occurred_at"] = _at(n)
        if kind is not None:
            env["payload"]["completion_kind"] = kind
        return ingest_lesson_completed(app, evidence, env, authenticated_producer="learning-svc", config=config)

    kinds = [None, "lesson", "review", None]
    for n, kind in enumerate(kinds):
        assert send(n, kind).status == "accepted"
    got = app.repository.get(UUID(learner_id))
    events = list(app.events.get(UUID(learner_id)))
    stored = [(e.completion_id, e.xp_amount, e.repeat_index) for e in events if isinstance(e, Learner.XPAwarded)]
    assert stored == [("cmp-0", 100, 0), ("cmp-1", 50, 1), ("cmp-2", 100, 0), ("cmp-3", 30, 2)]
    kinds_stored = [e.completion_kind for e in events if isinstance(e, Learner.XPAwarded)]
    assert kinds_stored == [None, "lesson", "review", None]
    assert got.xp_total == 280
    evidence.close()
    app.close()


def test_leaderboard_sums_the_decayed_amount(v2, tmp_path):
    db = tmp_path / "events.db"
    app = LearnerApplication(env=_env(db))
    learner = Learner()
    for i in range(3):
        _complete(learner, f"c{i}", "l1", i, v2)
    app.save(learner)
    store = SQLiteLeaderboardStore(str(tmp_path / "lb.db"))
    projection = LeaderboardProjection(store)
    pos = 1
    while True:
        batch = list(app.notification_log.select(start=pos, limit=app.notification_log.section_size))
        if not batch:
            break
        for notification in batch:
            projection.apply(notification, offset=notification.id)
        pos = batch[-1].id + 1
    entry = store.get_entry("global", "global", "2026-W42", str(learner.id))
    assert entry.score == 100 + 50 + 30
    store.close()
    app.close()


def test_xp_awarded_schema_accepts_new_optional_fields_and_old_shape():
    from importlib.resources import files

    from jsonschema import Draft7Validator

    schema = json.loads((files("src") / "schemas" / "events" / "xp_awarded.schema.json").read_text())
    validator = Draft7Validator(schema)
    old = {"reward_id": "xp:c", "completion_id": "c", "xp_amount": 100, "rule_version": "v1"}
    validator.validate(old)
    validator.validate({**old, "repeat_index": 2, "completion_kind": "review"})
    assert not validator.is_valid({**old, "repeat_index": -1})
    assert not validator.is_valid({**old, "completion_kind": "other"})
