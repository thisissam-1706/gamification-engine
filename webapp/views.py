"""Read-only adapter: one home-page dict per learner.

Engine reads used (see docs/audit/frontend-01-recon.md):
  LearnerApplication.repository.get / .events.get   (opened with SQLite mode=ro)
  LearnerQuestProjection.rebuild / .get_summary      (quest status and progress, as_of aware)
  quest_definition_for, get_ruleset                  (quest target, badge id and threshold)
  LeaderboardService.get_top_k / get_learner_rank    (read-only SQLite connection)
  get_calendar_week                                  (epoch for the as-of instant)
  raw SELECT on notification_dispatches              (no per-learner reader exists)

Nothing here writes to an engine store or calls a mutating Learner method.
Anything not in the engine is marked "UI-only, not engine state".
"""

import json
import math
import sqlite3
from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import UUID
from zoneinfo import ZoneInfo

from src.leaderboard.cohorts import get_calendar_week
from src.leaderboard.service import LeaderboardService
from src.leaderboard.store import SQLiteLeaderboardStore
from src.state_machine.learner import LearnerApplication
from src.state_machine.projection import LearnerQuestProjection
from src.state_machine.quest_definitions import quest_definition_for
from src.state_machine.rules import get_ruleset

from webapp.seed import DATA_DIR

TIE_RULE = (
    "Ranked by XP this week. Ties go to whoever reached that XP first, "
    "then to the lower learner ID."
)
LEADERBOARD_ROWS = 5
INBOX_LIMIT = 20


class UnknownLearner(KeyError):
    """The id is not a learner in the demo dataset."""


# ---------------------------------------------------------------- UI-only derivations

def level_for(xp: int) -> tuple[int, int]:
    """UI-only, not engine state: (level, xp_to_next) from lifetime XP.

    The engine has no level concept. Formula: XP needed to reach level L is
    50 * L * (L - 1)  (L1=0, L2=100, L3=300, L4=600, L5=1000, L6=1500 ...).
    """
    level = 1
    while xp >= 50 * (level + 1) * level:
        level += 1
    return level, 50 * (level + 1) * level - xp


def level_start_xp(level: int) -> int:
    """UI-only, not engine state: XP at which ``level`` begins (see ``level_for``)."""
    return 50 * level * (level - 1)


def initials_for(name: str) -> str:
    """UI-only, not engine state: first letters of the first two words."""
    return "".join(part[0] for part in name.split()[:2]).upper()


# UI-only, not engine state: titles by engine quest id. The engine stores no titles
# and no quest XP reward, so xp_reward is reported as null rather than invented.
QUEST_UI = {
    "lesson_starter_v1": ("weekly", "Weekly lesson sprint"),
    "remediation_v1": ("remediation", "Practice quest"),
}
# UI-only, not engine state: badge display names by engine badge id.
BADGE_UI = {"week_warrior_v1": "Week Warrior"}


# ---------------------------------------------------------------- read-only openers

def _ro_uri(path: Path) -> str:
    return "file:" + quote(str(path))  # project paths may contain spaces


@contextmanager
def _open_events(data_dir: Path):
    app = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite",
             "SQLITE_DBNAME": _ro_uri(data_dir / "events.db") + "?mode=ro&uri=true"}
    )
    try:
        yield app
    finally:
        app.close()


@contextmanager
def _open_leaderboard(data_dir: Path):
    """SQLiteLeaderboardStore.__init__ creates tables, so it cannot open a read-only file.
    Attach a read-only connection to an uninitialised instance (recon, item 4)."""
    connection = sqlite3.connect(_ro_uri(data_dir / "leaderboard.db") + "?mode=ro", uri=True)
    store = SQLiteLeaderboardStore.__new__(SQLiteLeaderboardStore)
    store.connection = connection
    store._transaction_depth = 0
    try:
        yield LeaderboardService(store)
    finally:
        connection.close()


def load_profile(data_dir: Path = DATA_DIR) -> dict:
    """UI-only sidecar written by seed.py (display names, cohort names, settings)."""
    return json.loads((data_dir / "ui_profile.json").read_text())


def list_learners(data_dir: Path = DATA_DIR) -> list[dict]:
    profile = load_profile(data_dir)
    return sorted(
        ({"id": lid, "display_name": info["display_name"]}
         for lid, info in profile["learners"].items()),
        key=lambda row: row["display_name"],
    )


# ---------------------------------------------------------------- sections

def _streak(learner, as_of: datetime) -> dict:
    tz = ZoneInfo(learner.timezone_history[-1]["timezone"] if learner.timezone_history else "UTC")
    today = as_of.astimezone(tz).date()
    done = {date.fromisoformat(d) for d in learner.activity_dates}

    # Frozen days: engine records only how many freezes the *next active* date consumed
    # (streak_outcomes[date][2]); those many days directly before it are the frozen ones.
    frozen: set[date] = set()
    for iso, outcome in learner.streak_outcomes.items():
        consumed = outcome[2]
        if consumed:
            end = date.fromisoformat(iso)
            frozen.update(end - timedelta(days=n) for n in range(1, consumed + 1))

    monday = today - timedelta(days=today.weekday())  # UI-only: Monday-first calendar week
    week = []
    for offset in range(7):
        day = monday + timedelta(days=offset)
        if day > today:
            week.append("future")  # also hides activity after a back-dated as_of
        elif day in done:
            week.append("done")
        elif day in frozen:
            week.append("frozen")
        elif day == today:
            week.append("today")
        else:
            week.append("missed")

    # UI-only: the engine keeps the streak as of the last activity and never expires it.
    # It is shown as 0 once a whole day has been missed before today.
    last = learner.last_activity_date
    alive = last is not None and date.fromisoformat(last) >= today - timedelta(days=1)
    return {
        "days": learner.new_streak if alive else 0,
        "freezes_left": learner.freezes_remaining,
        "week": week,
    }


def _quests(app, learner, learner_id: UUID, as_of: datetime) -> list[dict]:
    projection = LearnerQuestProjection()
    projection.rebuild(app.events.get(learner_id))
    summary = projection.get_summary(learner_id, as_of=as_of)
    quests = []
    for assignment_id, record in summary.assignments.items():
        if record.status == "expired":
            continue
        version = str(learner.quest_instances[assignment_id]["definition_version"])
        target = quest_definition_for(record.quest_id, version).target
        kind, title = QUEST_UI.get(record.quest_id, ("other", record.quest_id))
        days_left = (
            max(0, math.ceil((record.deadline_at - as_of).total_seconds() / 86400))
            if record.deadline_at else None
        )
        quests.append({
            "kind": kind, "title": title, "status": record.status,
            "progress": min(record.progress, target), "target": target,
            "xp_reward": None,  # engine grants no quest XP; nothing to show
            "days_left": days_left,
        })
    return sorted(quests, key=lambda q: (q["kind"], q["title"]))


def _badges(learner) -> list[dict]:
    badge_id = get_ruleset(learner_rule_version(learner)).badge_id
    return [{"name": BADGE_UI.get(badge_id, badge_id), "earned": badge_id in learner.badges_issued}]


def learner_rule_version(learner) -> str:
    versions = list(learner.applied_rule_versions.values())
    return versions[-1] if versions else learner.DEFAULT_RULE_VERSION


def _rows(service, scope_type, scope_id, epoch, me: str, names: dict) -> list[dict]:
    ranked = service.get_top_k(scope_type, scope_id, epoch, LEADERBOARD_ROWS)
    if me not in {r.subject_id for r in ranked}:
        mine = service.get_learner_rank(scope_type, scope_id, epoch, me)
        if mine:
            ranked = [*ranked, mine]
    return [
        {"rank": r.rank, "learner_id": r.subject_id,
         "display_name": names.get(r.subject_id, "Learner " + r.subject_id[:8]),
         "xp": r.score, "is_you": r.subject_id == me}
        for r in ranked
    ]


def _leaderboard(service, learner, learner_id: str, as_of: datetime, profile: dict) -> dict:
    epoch = get_calendar_week(as_of)
    names = {lid: i["display_name"] for lid, i in profile["learners"].items()}
    membership = learner.cohort_memberships.get(epoch)
    cohort_id = membership["cohort_id"] if membership else None
    return {
        "cohort_name": profile["cohort_names"].get(cohort_id, cohort_id),  # UI-only name
        "cohort_rows": (
            _rows(service, "cohort", cohort_id, epoch, learner_id, names) if cohort_id else []
        ),
        "global_rows": _rows(service, "global", "global", epoch, learner_id, names),
        "tie_rule": TIE_RULE,
    }


def _inbox(data_dir: Path, learner_id: str, as_of: datetime) -> list[dict]:
    path = data_dir / "notifications.db"
    if not path.exists():
        return []
    with closing(sqlite3.connect(_ro_uri(path) + "?mode=ro", uri=True)) as connection:
        rows = connection.execute(
            "SELECT category, dispatched_at, payload_json FROM notification_dispatches "
            "WHERE learner_id = ? AND channel = 'in_app' ORDER BY dispatched_at DESC",
            (learner_id,),
        ).fetchall()
    inbox = []
    for category, dispatched_at, payload_json in rows:
        if datetime.fromisoformat(dispatched_at.replace("Z", "+00:00")) > as_of:
            continue
        payload = json.loads(payload_json)
        inbox.append({"kind": category, "title": payload.get("title", ""),
                      "body": payload.get("body", ""), "created_at": dispatched_at})
    return inbox[:INBOX_LIMIT]


# ---------------------------------------------------------------- entry point

def build_home(learner_id: str, as_of: datetime, data_dir: Path = DATA_DIR) -> dict:
    """Returns the home-page dict for one learner. Raises UnknownLearner."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    as_of = as_of.astimezone(timezone.utc)
    profile = load_profile(data_dir)
    info = profile["learners"].get(learner_id)
    if info is None:
        raise UnknownLearner(learner_id)

    with _open_events(data_dir) as app, _open_leaderboard(data_dir) as board:
        learner = app.repository.get(UUID(learner_id))
        level, xp_to_next = level_for(learner.xp_total)  # lifetime XP
        return {
            "as_of": as_of.isoformat().replace("+00:00", "Z"),
            "learner": {
                "id": learner_id, "display_name": info["display_name"],
                "initials": initials_for(info["display_name"]),
                "level": level, "xp": learner.xp_total, "xp_to_next": xp_to_next,
                # UI-only, not engine state: bounds of the current level, for the progress ring.
                "level_start_xp": level_start_xp(level),
                "next_level_xp": level_start_xp(level + 1),
                "ruleset": learner_rule_version(learner),
            },
            "streak": _streak(learner, as_of),
            "quests": _quests(app, learner, UUID(learner_id), as_of),
            "badges": _badges(learner),
            "leaderboard": _leaderboard(board, learner, learner_id, as_of, profile),
            "inbox": _inbox(data_dir, learner_id, as_of),
            "notification_settings": {
                # UI-only: quiet hours are the dispatcher's constructor defaults and push
                # opt-out lives in LearnerPreferences, which the engine never persists.
                "quiet_hours": profile["quiet_hours"],
                "push_enabled": info["push_enabled"],
            },
        }
