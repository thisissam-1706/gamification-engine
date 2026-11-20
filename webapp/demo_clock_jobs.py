"""Clock-driven jobs for the demo: what the cron scheduler would do as time passes.

``send_due_streak_reminders`` sends the exp_007 streak-risk push reminders (learner-local
REMINDER_TIMES_LOCAL in src/notifications/streak_risk.py: 20:00, 21:00, 21:30, 21:45) as a
learner's demo clock passes each time. It reuses the engine's own planner and sweep; the only
demo-specific part is deciding *when* to call them. Each reminder slot fires once per learner
(remembered in demo_flags, which Reset demo clears).
"""

from dataclasses import replace
from uuid import UUID
from zoneinfo import ZoneInfo

from src.experiments.store import ExperimentStore
from src.notifications.models import ChannelType
from src.notifications.store import NotificationStore
from src.notifications.streak_risk import (
    REMINDER_TIMES_LOCAL,
    plan_streak_risk_reminders,
    run_streak_risk_sweep,
)
from src.state_machine.learner import LearnerApplication
from webapp import demo_admin, demo_admin_tick, demo_backend, demo_notify

EXP_ID = "exp_007"


def _slot_flag(learner_id: str) -> str:
    return f"streak_slot:{learner_id}"


def _in_app_copy(push):
    """The in-app twin of a push reminder, so it shows in the learner's inbox on the home page."""
    streak = push.payload.get("streak_count", 0)
    return replace(
        push,
        proposal_id=push.proposal_id + ":in_app",
        channel=ChannelType.IN_APP,
        payload={**push.payload, "title": f"Your {streak}-day streak is at risk",
                 "body": "Finish one quick lesson today to keep it going."},
        experiment_id=None,
        variant=None,
    )


def send_due_streak_reminders(sandbox, learner_id: str) -> bool:
    """Sends this learner's newly due streak-risk reminder, if any. Returns True if one was sent."""
    live = sandbox.live_dir
    now = sandbox.get_clock(learner_id)
    prefs = demo_notify.prefs_for(live, learner_id)
    local = now.astimezone(ZoneInfo(prefs.timezone or "UTC"))
    first_hour, first_minute = (int(part) for part in REMINDER_TIMES_LOCAL[0].split(":"))
    if (local.hour, local.minute) < (first_hour, first_minute):
        return False  # nothing due yet today: skip the work of opening the stores

    sent = False
    with demo_backend._submit_lock:
        opened = []
        try:
            app = LearnerApplication(
                env={"PERSISTENCE_MODULE": "eventsourcing.sqlite",
                     "SQLITE_DBNAME": str(live / "events.db")}
            )
            opened.append(app.close)
            experiments = ExperimentStore(str(live / "experiments.db"))
            opened.append(experiments.close)

            learner = app.repository.get(UUID(learner_id))
            if EXP_ID not in learner.experiment_assignments:
                # The engine's assign step (normally the cron tick) enrols a learner in exp_007.
                module = demo_admin_tick._load_run_engine_tick()
                module._assign_step(app, experiments, [learner_id], {learner_id: prefs}, now)
                learner = app.repository.get(UUID(learner_id))
                sent_changes = True
            else:
                sent_changes = False

            due = plan_streak_risk_reminders(learner, prefs, now, experiment_id=EXP_ID)
            if due and demo_notify.get_flag(live, _slot_flag(learner_id)) != due[-1].proposal_id:
                notif = NotificationStore(str(live / "notifications.db"))
                opened.append(notif.close)
                dispatcher = demo_notify.build_dispatcher(live, notif, lambda: now)
                # In-app first: it is the copy the learner sees, and the push goes through the
                # same 5-per-24h cap afterwards.
                dispatcher.dispatch(_in_app_copy(due[-1]), prefs=prefs, now_utc=now)
                run_streak_risk_sweep(
                    app, dispatcher, [learner_id], {learner_id: prefs},
                    now_utc=now, experiment_id=EXP_ID, store=experiments,
                )
                demo_notify.set_flag(live, _slot_flag(learner_id), due[-1].proposal_id)
                sent = True
            changed = sent or sent_changes
        finally:
            for close in reversed(opened):
                close()
        if changed:
            # Same catch-up the admin "Run engine tick" does after it writes.
            with demo_admin.open_app(sandbox) as fresh:
                demo_backend._project_new_notifications(
                    fresh, live / "leaderboard.db", live / "demo_state.db"
                )
            demo_backend._bump_version(sandbox.root)
    return sent
