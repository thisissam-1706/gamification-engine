"""Builds a deterministic demo dataset for the learner home page.

The engine is driven only through its public methods (``Learner.complete_lesson``,
``acquire_freeze``, ``assign_quest``, ``record_checkpoint_attempt``,
``run_remediation``, ``LeaderboardProjection.apply``, ``NotificationDispatcher``).
Nothing here is imported by ``views.py``; the API never writes to these stores.

Base activity comes from ``generate_synthetic_workload`` (the simulator/pilot code
cannot produce lessons, XP or streaks; see docs/audit/frontend-01-recon.md).
Scripted overlays then add the states the workload alone never produces
(a freeze, a missed day, a badge, remediation quests, notifications).

Files written to ``webapp/demo_data/``:
  events.db         engine event store (eventsourcing SQLite)
  leaderboard.db    engine leaderboard read model
  notifications.db  engine notification store
  ui_profile.json   UI-only data: display names, cohort names, notification settings
"""

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid5

from src.interventions.service import run_remediation
from src.leaderboard.cohort_builder import build_weekly_cohorts
from src.leaderboard.poller import run_until_idle
from src.leaderboard.projection import LeaderboardProjection
from src.leaderboard.store import SQLiteLeaderboardStore
from src.notifications.dispatcher import NotificationDispatcher
from src.notifications.models import (
    ChannelType,
    LearnerPreferences,
    NotificationCategory,
    NotificationProposal,
    NotificationStatus,
)
from src.notifications.store import NotificationStore
from src.scale_and_recovery.rebuild import SYNTHETIC_BUCKETS, SYNTHETIC_MIN_COHORT_SIZE
from src.scale_and_recovery.workload import WorkloadProfile, generate_synthetic_workload
from src.state_machine.learner import Learner, LearnerApplication

DATA_DIR = Path(__file__).resolve().parent / "demo_data"

# The fixed "today" for the UI. A Friday inside ISO week 2026-W42, after the last
# seeded event (workload events after it are dropped by seed()).
AS_OF = "2026-10-16T18:00:00Z"
EPOCH = "2026-W42"

POPULATION = 12
SEED = 42
NAMESPACE = UUID("6f1c0b52-3c58-4d0e-9a53-0d8a3d0e7a11")  # fixed, makes learner ids stable

# UI-only: the engine has no display names (recon, question b).
DISPLAY_NAMES = [
    "Aarav Mehta", "Priya Nair", "Rohan Iyer", "Sneha Kulkarni",
    "Vikram Rao", "Ananya Das", "Kabir Singh", "Meera Pillai",
    "Ishaan Gupta", "Diya Menon", "Arjun Reddy", "Tara Bose",
]
# UI-only cohort names ("Cohort A", "Cohort B", ...) are given to the cohorts the builder
# produces, in cohort id order; see _cohort_names().

WEEKLY_QUEST_ASSIGNED_AT = "2026-10-15T00:00:00Z"  # engine default deadline: +7 days
FREEZE_LEARNER = 1      # has a freeze; its 2026-10-14 is skipped, so the day is frozen
MISSED_LEARNER = 2      # no freeze; its 2026-10-15 is skipped, so the day is missed
BADGE_LEARNER = 0       # extra lessons on Oct 10-11 give a 7-day streak and the badge
REMEDIATION_PENDING = 4
REMEDIATION_DONE = 3
SKIP_DATES = {FREEZE_LEARNER: {"2026-10-14"}, MISSED_LEARNER: {"2026-10-15"}}
PUSH_OPTED_OUT = {2, 5, 9}


def learner_uuid(index: int) -> UUID:
    return uuid5(NAMESPACE, f"learner_{index:04d}")


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _reset(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    for name in ("events.db", "leaderboard.db", "notifications.db", "ui_profile.json"):
        for suffix in ("", "-wal", "-shm"):
            path = data_dir / (name + suffix)
            if path.exists():
                path.unlink()


def seed(data_dir: Path | str = DATA_DIR) -> dict:
    """Rebuilds the demo dataset from scratch and returns a small summary."""
    data_dir = Path(data_dir)
    _reset(data_dir)
    as_of = _dt(AS_OF)

    workload = generate_synthetic_workload(
        WorkloadProfile(
            population_size=POPULATION, events_per_learner=10,
            seed=SEED, epoch=EPOCH, base_start_iso="2026-10-12T08:00:00Z",
        )
    )
    index_of = {lid: i for i, lid in enumerate(workload.learner_ids)}

    app = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite",
             "SQLITE_DBNAME": str(data_dir / "events.db")}
    )
    uuids: dict[int, UUID] = {}
    try:
        # 1. Learners: stable ids, weekly quest, and the scripted freeze.
        for lid in workload.learner_ids:
            i = index_of[lid]
            learner = Learner._create(Learner.Created, id=learner_uuid(i))
            learner.assign_quest(
                "lesson_starter_v1",
                assignment_id="weekly:2026-W42",
                assigned_at=WEEKLY_QUEST_ASSIGNED_AT,
            )
            if i == FREEZE_LEARNER:
                learner.acquire_freeze("freeze:welcome", activity_date="2026-10-12")
            app.save(learner)
            uuids[i] = learner.id

        # 1b. Prior-week lessons (ISO week before EPOCH, Monday to Thursday), then this week's
        #     cohorts from them. The history is ordinary lesson completions in the event log.
        for evt in workload.history_events:
            learner = app.repository.get(uuids[index_of[evt.learner_id]])
            learner.complete_lesson(
                completion_id=evt.completion_id, lesson_id=evt.lesson_id,
                occurred_at=evt.occurred_at, timezone="UTC",
            )
            app.save(learner)
        built = build_weekly_cohorts(
            app, [str(u) for u in uuids.values()], EPOCH,
            min_size=SYNTHETIC_MIN_COHORT_SIZE,  # see rebuild.py: 12 learners, a rank needs a rival
            activity_buckets=SYNTHETIC_BUCKETS, module_buckets=SYNTHETIC_BUCKETS,
            reference_time=_dt(workload.profile.base_start_iso),
        )
        cohort_names = _cohort_names(built.cohort_counts)

        # 2. Base activity, minus scripted skips, up to the as-of instant.
        for evt in workload.events:
            i = index_of[evt.learner_id]
            if evt.activity_date in SKIP_DATES.get(i, ()) or _dt(evt.occurred_at) > as_of:
                continue
            learner = app.repository.get(uuids[i])
            learner.complete_lesson(
                completion_id=evt.completion_id, lesson_id=evt.lesson_id,
                occurred_at=evt.occurred_at, timezone="UTC",
            )
            app.save(learner)

        # 3. Overlay: extra earlier lessons give the badge learner a 7-day streak.
        learner = app.repository.get(uuids[BADGE_LEARNER])
        for n, day in enumerate(("2026-10-10", "2026-10-11"), start=1):
            learner.complete_lesson(
                completion_id=f"bonus:{n}", lesson_id=f"lesson_bonus_{n}",
                occurred_at=f"{day}T09:00:00Z", timezone="UTC",
            )
        app.save(learner)

        # 4. Overlay: failed checkpoint -> remediation quest; one learner completes it.
        for i in (REMEDIATION_DONE, REMEDIATION_PENDING):
            learner = app.repository.get(uuids[i])
            learner.record_checkpoint_attempt(
                f"attempt:{i}", "ckpt_1", 1, False,
                timezone="UTC", occurred_at="2026-10-15T10:00:00Z",
            )
            app.save(learner)
            run_remediation(
                app, str(uuids[i]), f"attempt:{i}",
                clock=lambda: _dt("2026-10-15T10:05:00Z"),
            )
        learner = app.repository.get(uuids[REMEDIATION_DONE])
        learner.complete_lesson(
            completion_id="remediation:1", lesson_id="remediation_lesson_1",
            occurred_at="2026-10-16T10:00:00Z", timezone="UTC",
            completion_kind="remediation",
        )
        app.save(learner)

        # 5. Leaderboard read model from the engine's notification log (as the AT-15 harness does).
        store = SQLiteLeaderboardStore(str(data_dir / "leaderboard.db"))
        projection = LeaderboardProjection(store)
        run_until_idle(app, projection)
        store.close()

        # 6. Notifications through the real dispatcher (in-app channel).
        notif_store = NotificationStore(str(data_dir / "notifications.db"))
        dispatcher = NotificationDispatcher(notif_store, clock=lambda: as_of)
        profile_learners: dict[str, dict] = {}
        for i in sorted(uuids):
            learner = app.repository.get(uuids[i])
            lid = str(learner.id)
            opted_out = {ChannelType.PUSH} if i in PUSH_OPTED_OUT else set()
            prefs = LearnerPreferences(lid, timezone="UTC", opted_out_channels=opted_out)
            for key, category, title, body, when in _inbox_facts(learner, as_of):
                dispatcher.dispatch(
                    NotificationProposal(
                        proposal_id=f"{key}:{lid}", learner_id=lid,
                        channel=ChannelType.IN_APP, category=category,
                        payload={"title": title, "body": body}, created_at=when,
                    ),
                    prefs=prefs, now_utc=when,
                )
            profile_learners[lid] = {
                "key": workload.learner_ids[i],
                "display_name": DISPLAY_NAMES[i],
                "push_enabled": ChannelType.PUSH not in opted_out,
            }
        quiet = {"start_hour": dispatcher.quiet_start_hour, "end_hour": dispatcher.quiet_end_hour}
        notif_store.close()
    finally:
        app.close()

    profile = {
        "as_of": AS_OF, "epoch": EPOCH, "cohort_names": cohort_names,
        "quiet_hours": quiet, "learners": profile_learners,
    }
    (data_dir / "ui_profile.json").write_text(json.dumps(profile, indent=2, sort_keys=True))
    return {"learners": len(uuids), "as_of": AS_OF, "epoch": EPOCH, "data_dir": str(data_dir)}


def _cohort_names(cohort_counts: dict[str, int]) -> dict[str, str]:
    """UI-only display names for the built cohorts, in cohort id order."""
    return {cohort_id: f"Cohort {chr(ord('A') + n)}" for n, cohort_id in enumerate(sorted(cohort_counts))}


def _inbox_facts(learner: Learner, as_of: datetime):
    """Yields (key, category, title, body, iso_time) for facts the engine already holds.

    Nothing in the engine creates proposals from learner events (recon, question e),
    so the demo derives a few from final aggregate state. Times are fixed for determinism.
    """
    if learner.badges_issued:
        yield ("badge", NotificationCategory.GENERAL, "Badge earned: Week Warrior",
               "You kept a 7-day streak.", "2026-10-16T08:00:00Z")
    for assignment_id, inst in sorted(learner.quest_instances.items()):
        if assignment_id.startswith("remediation_quest:"):
            if inst["status"] == "completed":
                yield (f"rem_done:{assignment_id}", NotificationCategory.REMEDIATION,
                       "Practice quest complete", "Nice recovery on your checkpoint.",
                       "2026-10-16T10:30:00Z")
            yield (f"rem:{assignment_id}", NotificationCategory.REMEDIATION,
                   "New practice quest",
                   "A short practice quest is ready after your checkpoint.", "2026-10-15T10:10:00Z")
        elif inst["status"] == "completed":
            yield (f"weekly_done:{assignment_id}", NotificationCategory.QUEST_UPDATE,
                   "Weekly quest complete", "You finished 3 lessons this week.",
                   "2026-10-16T12:00:00Z")
    if learner.new_streak >= 3 and learner.last_activity_date == "2026-10-16":
        yield ("streak", NotificationCategory.GENERAL, f"{learner.new_streak}-day streak",
               "Keep it going tomorrow.", "2026-10-16T09:00:00Z")


if __name__ == "__main__":
    print(json.dumps(seed(*(sys.argv[1:2])), indent=2))
