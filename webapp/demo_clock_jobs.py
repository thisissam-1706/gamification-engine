"""Clock-driven jobs for the demo: what the cron scheduler would do as time passes.

``send_due_streak_reminders`` sends the exp_007 streak-risk reminders (learner-local
REMINDER_TIMES_LOCAL in src/notifications/streak_risk.py: 20:00, 21:00, 21:30, 21:45) as a
learner's demo clock passes each time. Each reminder goes out on all three channels: in-app,
push and email (the engine's sweep does this). Every channel has its own daily cap of 5, and
consent still applies per channel, so an opted-out channel is suppressed, not sent. It reuses the engine's own planner and sweep; the only
demo-specific part is deciding *when* to call them. Each reminder slot fires once per learner
(remembered in demo_flags, which Reset demo clears).
"""

from uuid import UUID
from zoneinfo import ZoneInfo

from src.experiments.store import ExperimentStore
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
                # The engine's sweep sends the reminder on in-app, push and email at once
                # (src/notifications/streak_risk.py), each channel with its own daily cap of 5.
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


def recover_dead_letters(sandbox, learner_id: str | None = None) -> int:
    """Recovery worker: re-sends open dead letters whose provider is healthy again.

    A real system runs this as a background job that watches provider health; in the demo it
    runs with the other clock jobs and right after an outage is switched off. Each entry is
    replayed through the dispatcher under its original proposal id, so it is delivered at most
    once; entries for a provider that is still down stay in the queue. Returns how many were
    delivered.
    """
    live = sandbox.live_dir
    delivered = 0
    with demo_backend._submit_lock:
        store = NotificationStore(str(live / "notifications.db"))
        try:
            entries = [
                e for e in store.get_dlq_entries(resolved=False)
                if (learner_id is None or e["learner_id"] == str(learner_id))
                and e["channel"] in ("push", "email")
                and not demo_notify.get_flag(live, f"outage_{e['channel']}")
            ]
            for entry in entries:
                lid = entry["learner_id"]
                now = sandbox.get_clock(lid)
                dispatcher = demo_notify.build_dispatcher(live, store, lambda now=now: now)
                try:
                    fact = dispatcher.replay_dlq(
                        entry["dlq_id"], prefs=demo_notify.prefs_for(live, lid), now_utc=now
                    )
                except ValueError:
                    continue
                delivered += fact.status.value == "dispatched"
        finally:
            store.close()
        if entries:
            demo_backend._bump_version(sandbox.root)
    return delivered
