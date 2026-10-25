"""Week 4 fix 5: persisted save/reload evidence for the state-machine readers.

Each scenario writes through a real LearnerApplication on a SQLite file,
snapshots every reader, closes and drops every object, reopens the same file
with new objects, snapshots again, and compares both snapshots with expected
values written by hand in docs/audit/week4-fix5-persisted-reload.md.
"""

from collections import Counter
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import gc
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from src.state_machine.learner import Learner, LearnerApplication
from src.state_machine.projection import (
    AssignmentRecord,
    LearnerCompletionProjection,
    LearnerQuestProjection,
    LearnerQuestSummary,
    LearnerReplayState,
    LearnerStreakProjection,
    LearnerStreakSummary,
    SQLiteLearnerProjection,
    rebuild_learner_state,
)

ROOT = Path(__file__).resolve().parents[2]
QUEST = "lesson_starter_v1"
UTC = timezone.utc


def _at(month, day, hour=0):
    return datetime(2026, month, day, hour, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Write, snapshot, reopen
# ---------------------------------------------------------------------------
def _env(db: Path) -> dict[str, str]:
    return {"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": str(db)}


def _run(learner: Learner, command) -> bool:
    name, args, kwargs = command
    return getattr(learner, name)(*args, **kwargs)


def _aggregate_state(learner: Learner) -> dict:
    return {
        "new_streak": learner.new_streak,
        "freezes_remaining": learner.freezes_remaining,
        "last_activity_date": learner.last_activity_date,
        "streak_revisions": dict(learner.streak_revisions),
        "streak_outcomes": dict(learner.streak_outcomes),
        "streak_before_break": learner.streak_before_break,
        "xp_total": learner.xp_total,
        "completed_lesson_count": learner.completed_lesson_count,
        "completed_lesson_ids": set(learner.completed_lesson_ids),
        "lesson_completion_counts": dict(learner.lesson_completion_counts),
        "activity_dates": set(learner.activity_dates),
        "freeze_ids": set(learner.freeze_ids),
        "freeze_acquisitions": dict(learner.freeze_acquisitions),
        "quest_instances": {k: dict(v) for k, v in learner.quest_instances.items()},
        "quest_progress": dict(learner.quest_progress),
        "completed_quest_ids": set(learner.completed_quest_ids),
        "badges_issued": dict(learner.badges_issued),
    }


def _snapshot(aggregate, events, read_model_state, learner_id, as_ofs) -> dict:
    streak = LearnerStreakProjection()
    streak.rebuild(events)
    quests = LearnerQuestProjection()
    quests.rebuild(events)
    completions = LearnerCompletionProjection()
    completions.rebuild(events)
    summary = streak.streak_summaries.get(learner_id, LearnerStreakSummary(0, 0))
    return {
        "aggregate": _aggregate_state(aggregate),
        "event_counts": dict(Counter(type(e).__name__ for e in events)),
        "event_types": [type(e).__name__ for e in events],
        "event_versions": [e.originator_version for e in events],
        "streak_projection": (summary.new_streak, summary.freezes_remaining),
        "quest_summaries": {
            as_of: quests.get_summary(learner_id, as_of=as_of) for as_of in as_ofs
        },
        "completion_count": completions.completed_lesson_counts.get(learner_id, 0),
        "replay": rebuild_learner_state(events),
        "read_model": read_model_state,
    }


def _badge_ids(events):
    badges = [e for e in events if isinstance(e, Learner.BadgeAwarded)]
    return badges[0].qualifying_action_ids if badges else None


def _phase_one(db: Path, commands, as_ofs):
    """Write the batch, persist the replay read model, snapshot; close everything."""
    app = LearnerApplication(env=_env(db))
    try:
        learner = Learner()
        assert [_run(learner, command) for command in commands] == [True] * len(commands)
        in_memory_badge_ids = _badge_ids(learner.pending_events)
        app.save(learner)
        learner_id = learner.id
        writer_state = _aggregate_state(learner)
        events = list(app.events.get(learner_id))
        read_model = SQLiteLearnerProjection(str(db))
        try:
            read_model.rebuild(str(learner_id), events)
            pre = _snapshot(
                app.repository.get(learner_id),
                events,
                read_model.state(str(learner_id)),
                learner_id,
                as_ofs,
            )
            stored_badge_ids = _badge_ids(events)
        finally:
            read_model.close()
    finally:
        app.close()
    # The writer's in-memory aggregate and the replayed one agree.
    assert pre["aggregate"] == writer_state
    return learner_id, pre, in_memory_badge_ids, stored_badge_ids


def _phase_two(db: Path, learner_id, as_ofs):
    """Fresh application and fresh read-model connection on the same file."""
    app = LearnerApplication(env=_env(db))
    try:
        events = list(app.events.get(learner_id))
        read_model = SQLiteLearnerProjection(str(db))  # no rebuild: read what is on disk
        try:
            post = _snapshot(
                app.repository.get(learner_id),
                events,
                read_model.state(str(learner_id)),
                learner_id,
                as_ofs,
            )
        finally:
            read_model.close()
        return post, _badge_ids(events)
    finally:
        app.close()


def _write_and_reload(tmp_path, scenario):
    db = tmp_path / "events.db"
    learner_id, pre, in_memory_ids, stored_ids = _phase_one(
        db, scenario["commands"], scenario["as_ofs"]
    )
    gc.collect()
    assert db.exists()
    post, reloaded_ids = _phase_two(db, learner_id, scenario["as_ofs"])
    return db, learner_id, pre, post, (in_memory_ids, stored_ids, reloaded_ids)


def _assert_headline(snapshot, expected):
    """Readable first failures before the full-snapshot comparison."""
    for key in ("new_streak", "freezes_remaining", "xp_total"):
        assert snapshot["aggregate"][key] == expected["aggregate"][key], key
    assert snapshot["event_counts"] == expected["event_counts"]
    assert snapshot["streak_projection"] == expected["streak_projection"]
    assert snapshot["read_model"] == expected["read_model"]


# ---------------------------------------------------------------------------
# Hand-written expected values (docs/audit/week4-fix5-persisted-reload.md)
# ---------------------------------------------------------------------------
EMPTY_QUESTS = LearnerQuestSummary(
    assigned_quests=(), completed_quests=(), quest_progress={}, assignments={}
)


def _aggregate(**overrides) -> dict:
    base = {
        "new_streak": 0,
        "freezes_remaining": 0,
        "last_activity_date": None,
        "streak_revisions": {},
        "streak_outcomes": {},
        "streak_before_break": None,
        "xp_total": 0,
        "completed_lesson_count": 0,
        "completed_lesson_ids": set(),
        "lesson_completion_counts": {},
        "activity_dates": set(),
        "freeze_ids": set(),
        "freeze_acquisitions": {},
        "quest_instances": {},
        "quest_progress": {},
        "completed_quest_ids": set(),
        "badges_issued": {},
    }
    base.update(overrides)
    return base


def _outcome(learner_id, day, rev, streak, remaining, consumed, previous, supersedes_rev=None):
    outcome_id = f"streak:{learner_id}:{day}:{rev}"
    supersedes = f"streak:{learner_id}:{day}:{supersedes_rev}" if supersedes_rev else None
    return (streak, remaining, consumed, previous, outcome_id, supersedes)


def _read_model(xp, streak, freezes, dates, completed=(), badges=None) -> dict:
    return {
        "xp_total": xp,
        "new_streak": streak,
        "freezes_remaining": freezes,
        "activity_dates": list(dates),
        "completed_quests": list(completed),
        "badges_issued": badges or {},
    }


def _lesson(cid, **kwargs):
    return ("complete_lesson", (cid, "lesson-1"), kwargs)


S1 = {
    "commands": [
        _lesson("a1", activity_date="2026-09-01"),
        _lesson("a2", activity_date="2026-09-02"),
        ("acquire_freeze", ("f1",), {"activity_date": "2026-09-02"}),
        _lesson("a4", activity_date="2026-09-04"),
    ],
    "as_ofs": (None,),
}


def _expected_s1(lid):
    dates = ("2026-09-01", "2026-09-02", "2026-09-04")
    return {
        "aggregate": _aggregate(
            new_streak=3,
            freezes_remaining=0,
            last_activity_date="2026-09-04",
            streak_revisions={d: 1 for d in dates},
            streak_outcomes={
                "2026-09-01": _outcome(lid, "2026-09-01", 1, 1, 0, 0, 0),
                "2026-09-02": _outcome(lid, "2026-09-02", 1, 2, 0, 0, 1),
                "2026-09-04": _outcome(lid, "2026-09-04", 1, 3, 0, 1, 2),
            },
            xp_total=300,
            completed_lesson_count=3,
            completed_lesson_ids={"a1", "a2", "a4"},
            lesson_completion_counts={"lesson-1": 3},
            activity_dates=set(dates),
            freeze_ids={"f1"},
            freeze_acquisitions={"f1": "2026-09-02"},
        ),
        "event_counts": {
            "Created": 1, "LessonCompleted": 3, "XPAwarded": 3,
            "StreakUpdated": 3, "FreezeAcquired": 1,
        },
        "event_types": [
            "Created",
            "LessonCompleted", "XPAwarded", "StreakUpdated",
            "LessonCompleted", "XPAwarded", "StreakUpdated",
            "FreezeAcquired",
            "LessonCompleted", "XPAwarded", "StreakUpdated",
        ],
        "event_versions": list(range(1, 12)),
        "streak_projection": (3, 0),
        "quest_summaries": {None: EMPTY_QUESTS},
        "completion_count": 3,
        "replay": LearnerReplayState(300, 3, 0, dates, (), {}),
        "read_model": _read_model(300, 3, 0, dates),
    }


S2 = {
    "commands": [
        _lesson("b1", activity_date="2026-09-01"),
        _lesson("b3", activity_date="2026-09-03"),
        _lesson("b4", activity_date="2026-09-04"),
        _lesson("b2", activity_date="2026-09-02"),  # late
    ],
    "as_ofs": (None,),
}


def _expected_s2(lid):
    dates = ("2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04")
    return {
        "aggregate": _aggregate(
            new_streak=4,
            last_activity_date="2026-09-04",
            streak_revisions={
                "2026-09-01": 1, "2026-09-02": 1, "2026-09-03": 2, "2026-09-04": 2,
            },
            streak_outcomes={
                "2026-09-01": _outcome(lid, "2026-09-01", 1, 1, 0, 0, 0),
                "2026-09-02": _outcome(lid, "2026-09-02", 1, 2, 0, 0, 2),
                "2026-09-03": _outcome(lid, "2026-09-03", 2, 3, 0, 0, 1, supersedes_rev=1),
                "2026-09-04": _outcome(lid, "2026-09-04", 2, 4, 0, 0, 1, supersedes_rev=1),
            },
            xp_total=400,
            completed_lesson_count=4,
            completed_lesson_ids={"b1", "b2", "b3", "b4"},
            lesson_completion_counts={"lesson-1": 4},
            activity_dates=set(dates),
        ),
        "event_counts": {
            "Created": 1, "LessonCompleted": 4, "XPAwarded": 4, "StreakUpdated": 6,
        },
        "event_types": [
            "Created",
            "LessonCompleted", "XPAwarded", "StreakUpdated",
            "LessonCompleted", "XPAwarded", "StreakUpdated",
            "LessonCompleted", "XPAwarded", "StreakUpdated",
            "LessonCompleted", "XPAwarded", "StreakUpdated", "StreakUpdated", "StreakUpdated",
        ],
        "event_versions": list(range(1, 16)),
        "streak_projection": (4, 0),
        "quest_summaries": {None: EMPTY_QUESTS},
        "completion_count": 4,
        "replay": LearnerReplayState(400, 4, 0, dates, (), {}),
        "read_model": _read_model(400, 4, 0, dates),
    }


S3 = {
    "commands": [
        _lesson("c0", activity_date="2026-10-05"),
        _lesson("c1", activity_date="2026-10-03"),
        ("acquire_freeze", ("f2",), {"activity_date": "2026-10-02"}),
        _lesson("c3", activity_date="2026-10-01"),
    ],
    "as_ofs": (None,),
}


def _expected_s3(lid):
    dates = ("2026-10-01", "2026-10-03", "2026-10-05")
    return {
        "aggregate": _aggregate(
            new_streak=1,
            freezes_remaining=0,
            last_activity_date="2026-10-05",
            streak_revisions={"2026-10-01": 1, "2026-10-03": 2, "2026-10-05": 1},
            streak_outcomes={
                "2026-10-01": _outcome(lid, "2026-10-01", 1, 1, 1, 0, 1),
                "2026-10-03": _outcome(lid, "2026-10-03", 2, 2, 0, 1, 1, supersedes_rev=1),
                "2026-10-05": _outcome(lid, "2026-10-05", 1, 1, 0, 0, 0),
            },
            xp_total=300,
            completed_lesson_count=3,
            completed_lesson_ids={"c0", "c1", "c3"},
            lesson_completion_counts={"lesson-1": 3},
            activity_dates=set(dates),
            freeze_ids={"f2"},
            freeze_acquisitions={"f2": "2026-10-02"},
        ),
        "event_counts": {
            "Created": 1, "LessonCompleted": 3, "XPAwarded": 3,
            "FreezeAcquired": 1, "StreakUpdated": 4,
        },
        "event_types": [
            "Created",
            "LessonCompleted", "XPAwarded", "StreakUpdated",
            "LessonCompleted", "XPAwarded", "StreakUpdated",
            "FreezeAcquired",
            "LessonCompleted", "XPAwarded", "StreakUpdated", "StreakUpdated",
        ],
        "event_versions": list(range(1, 13)),
        "streak_projection": (1, 0),
        "quest_summaries": {None: EMPTY_QUESTS},
        "completion_count": 3,
        "replay": LearnerReplayState(300, 1, 0, dates, (), {}),
        "read_model": _read_model(300, 1, 0, dates),
    }


S4_AS_OFS = (None, _at(10, 14), _at(10, 15), _at(10, 16), _at(10, 21))
S4 = {
    "commands": [
        ("assign_quest", (QUEST,), {
            "assignment_id": "q-done",
            "assigned_at": "2026-10-01T00:00:00Z", "deadline_at": "2026-10-08T00:00:00Z",
        }),
        ("assign_quest", (QUEST,), {
            "assignment_id": "q-expired",
            "assigned_at": "2026-10-01T00:00:00Z", "deadline_at": "2026-10-02T00:00:00Z",
        }),
        ("assign_quest", (QUEST,), {
            "assignment_id": "q-progress",
            "assigned_at": "2026-10-04T00:00:00Z", "deadline_at": "2026-10-20T00:00:00Z",
        }),
        ("assign_quest", (QUEST,), {
            "assignment_id": "q-assigned",
            "assigned_at": "2026-10-10T00:00:00Z", "deadline_at": "2026-10-15T00:00:00Z",
        }),
        _lesson("c1", occurred_at="2026-10-01T10:00:00Z"),
        _lesson("c2", occurred_at="2026-10-03T10:00:00Z"),
        _lesson("c3", occurred_at="2026-10-05T10:00:00Z"),
        _lesson("c4", occurred_at="2026-10-06T10:00:00Z"),
    ],
    "as_ofs": S4_AS_OFS,
}

S4_WINDOWS = {  # assignment -> (assigned_at, deadline_at)
    "q-assigned": (_at(10, 10), _at(10, 15)),
    "q-done": (_at(10, 1), _at(10, 8)),
    "q-expired": (_at(10, 1), _at(10, 2)),
    "q-progress": (_at(10, 4), _at(10, 20)),
}
S4_PROGRESS = {"q-assigned": 0, "q-done": 3, "q-expired": 1, "q-progress": 2}
S4_COMPLETED_AT = {"q-done": _at(10, 5, 10)}
# Event-derived status, and the read-time status per as_of.
S4_STATUS = {
    None: {"q-assigned": "assigned", "q-done": "completed", "q-expired": "expired", "q-progress": "assigned"},
    _at(10, 14): {"q-assigned": "assigned", "q-done": "completed", "q-expired": "expired", "q-progress": "assigned"},
    _at(10, 15): {"q-assigned": "assigned", "q-done": "completed", "q-expired": "expired", "q-progress": "assigned"},
    _at(10, 16): {"q-assigned": "expired", "q-done": "completed", "q-expired": "expired", "q-progress": "assigned"},
    _at(10, 21): {"q-assigned": "expired", "q-done": "completed", "q-expired": "expired", "q-progress": "expired"},
}


def _s4_summary(as_of):
    records = {
        aid: AssignmentRecord(
            assignment_id=aid,
            quest_id=QUEST,
            status=S4_STATUS[as_of][aid],
            progress=S4_PROGRESS[aid],
            assigned_at=S4_WINDOWS[aid][0],
            deadline_at=S4_WINDOWS[aid][1],
            completed_at=S4_COMPLETED_AT.get(aid),
        )
        for aid in S4_WINDOWS
    }
    return LearnerQuestSummary(
        assigned_quests=(QUEST,) * 4,
        completed_quests=(QUEST,),
        quest_progress=dict(S4_PROGRESS),
        assignments=records,
    )


def _expected_s4(lid):
    instances = {
        aid: {
            "assignment_id": aid,
            "quest_id": QUEST,
            "definition_version": "v1",
            "status": S4_STATUS[None][aid],  # the aggregate never expires by time
            "assigned_at": S4_WINDOWS[aid][0],
            "deadline_at": S4_WINDOWS[aid][1],
            "completed_at": S4_COMPLETED_AT.get(aid),
            "progress": S4_PROGRESS[aid],
        }
        for aid in S4_WINDOWS
    }
    return {
        "aggregate": _aggregate(
            xp_total=400,
            completed_lesson_count=4,
            completed_lesson_ids={"c1", "c2", "c3", "c4"},
            lesson_completion_counts={"lesson-1": 4},
            quest_instances=instances,
            quest_progress=dict(S4_PROGRESS),
            completed_quest_ids={
                "quest_completion:q-done", "quest-completed:q-done",
                "quest-completed:lesson_starter_v1",
            },
        ),
        "event_counts": {
            "Created": 1, "QuestAssigned": 4, "LessonCompleted": 4, "XPAwarded": 4,
            "QuestProgressed": 6, "QuestCompleted": 1, "QuestExpired": 1,
        },
        "event_types": [
            "Created",
            "QuestAssigned", "QuestAssigned", "QuestAssigned", "QuestAssigned",
            # c1: q-done 1, q-expired 1
            "LessonCompleted", "XPAwarded", "QuestProgressed", "QuestProgressed",
            # c2: q-done 2, q-expired expires
            "LessonCompleted", "XPAwarded", "QuestProgressed", "QuestExpired",
            # c3: q-done 3 + completed, q-progress 1
            "LessonCompleted", "XPAwarded", "QuestProgressed", "QuestCompleted", "QuestProgressed",
            # c4: q-progress 2
            "LessonCompleted", "XPAwarded", "QuestProgressed",
        ],
        "event_versions": list(range(1, 22)),
        "streak_projection": (0, 0),
        "quest_summaries": {as_of: _s4_summary(as_of) for as_of in S4_AS_OFS},
        "completion_count": 4,
        "replay": LearnerReplayState(400, 0, 0, (), (QUEST,), {}),
        "read_model": _read_model(400, 0, 0, (), completed=(QUEST,)),
    }


S5 = {
    "commands": [_lesson(f"e{k}", activity_date=f"2026-09-0{k}") for k in range(1, 8)],
    "as_ofs": (None,),
}
S5_BADGE_IDS = ["e1", "e2", "e3", "e4", "e5", "e6", "e7"]


def _expected_s5(lid):
    dates = tuple(f"2026-09-0{k}" for k in range(1, 8))
    badges = {"week_warrior_v1": "badge:week_warrior_v1"}
    return {
        "aggregate": _aggregate(
            new_streak=7,
            last_activity_date="2026-09-07",
            streak_revisions={d: 1 for d in dates},
            streak_outcomes={
                d: _outcome(lid, d, 1, k, 0, 0, k - 1) for k, d in enumerate(dates, 1)
            },
            xp_total=700,
            completed_lesson_count=7,
            completed_lesson_ids=set(S5_BADGE_IDS),
            lesson_completion_counts={"lesson-1": 7},
            activity_dates=set(dates),
            badges_issued=badges,
        ),
        "event_counts": {
            "Created": 1, "LessonCompleted": 7, "XPAwarded": 7,
            "StreakUpdated": 7, "BadgeAwarded": 1,
        },
        "event_types": ["Created"]
        + ["LessonCompleted", "XPAwarded", "StreakUpdated"] * 7
        + ["BadgeAwarded"],
        "event_versions": list(range(1, 24)),
        "streak_projection": (7, 0),
        "quest_summaries": {None: EMPTY_QUESTS},
        "completion_count": 7,
        "replay": LearnerReplayState(700, 7, 0, dates, (), badges),
        "read_model": _read_model(700, 7, 0, dates, badges=badges),
    }


SCENARIOS = {
    "S1": (S1, _expected_s1),
    "S2": (S2, _expected_s2),
    "S3": (S3, _expected_s3),
    "S4": (S4, _expected_s4),
    "S5": (S5, _expected_s5),
}


# ---------------------------------------------------------------------------
# S1-S5: snapshot before restart == snapshot after restart == hand-written
# ---------------------------------------------------------------------------
def _check(tmp_path, name):
    scenario, expected_for = SCENARIOS[name]
    _, learner_id, pre, post, badge_ids = _write_and_reload(tmp_path, scenario)
    expected = expected_for(learner_id)
    _assert_headline(pre, expected)
    _assert_headline(post, expected)
    assert pre == expected
    assert post == expected
    assert post == pre
    return badge_ids


def test_s1_freeze_consumed_in_order_survives_restart(tmp_path):
    _check(tmp_path, "S1")


def test_s2_late_lesson_superseding_outcomes_survives_restart(tmp_path):
    _check(tmp_path, "S2")


def test_s3_freeze_consumed_by_earlier_date_correction_survives_restart(tmp_path):
    _check(tmp_path, "S3")


def test_s4_quest_states_and_as_of_reads_survive_restart(tmp_path):
    _check(tmp_path, "S4")


def test_s5_badge_survives_restart_with_same_qualifying_ids(tmp_path):
    in_memory, stored_before, reloaded = _check(tmp_path, "S5")

    # The decided value, the stored value and the reloaded value are one sorted list.
    assert in_memory == stored_before == reloaded == S5_BADGE_IDS
    assert type(in_memory) is type(stored_before) is type(reloaded) is list


# ---------------------------------------------------------------------------
# D: resending the whole batch after the restart emits nothing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_d_resend_whole_batch_after_restart_emits_zero_events(tmp_path, name):
    scenario, expected_for = SCENARIOS[name]
    db = tmp_path / "events.db"
    learner_id, pre, _, _ = _phase_one(db, scenario["commands"], scenario["as_ofs"])
    gc.collect()

    app = LearnerApplication(env=_env(db))
    try:
        reloaded = app.repository.get(learner_id)
        results = [_run(reloaded, command) for command in scenario["commands"]]
        assert results == [False] * len(scenario["commands"])
        assert app.save(reloaded) == []
    finally:
        app.close()

    post, _ = _phase_two(db, learner_id, scenario["as_ofs"])  # a third application
    assert post["event_counts"] == pre["event_counts"]
    assert post["event_types"] == pre["event_types"]
    assert post == expected_for(learner_id)
    assert post == pre


# ---------------------------------------------------------------------------
# E1: S3 reloaded in a separate Python process
# ---------------------------------------------------------------------------
def _jsonable(value):
    """Canonical JSON form so a snapshot can cross a process boundary."""
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


SUBPROCESS_SCRIPT = """
import json, sys, uuid
from pathlib import Path
from tests.state_machine.test_persisted_reload_evidence import _jsonable, _phase_two
post, _ = _phase_two(Path(sys.argv[1]), uuid.UUID(sys.argv[2]), (None,))
print(json.dumps(_jsonable(post)))
"""


def test_e1_s3_reloaded_in_a_separate_process(tmp_path):
    db = tmp_path / "events.db"
    learner_id, pre, _, _ = _phase_one(db, S3["commands"], S3["as_ofs"])
    gc.collect()

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    completed = subprocess.run(
        [sys.executable, "-c", SUBPROCESS_SCRIPT, str(db), str(learner_id)],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    post = json.loads(completed.stdout)

    assert post == _jsonable(_expected_s3(learner_id))
    assert post == _jsonable(pre)
    assert post["aggregate"]["freezes_remaining"] == 0
    assert post["streak_projection"] == [1, 0]
