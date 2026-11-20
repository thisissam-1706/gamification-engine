"""Demo persona roles and one-line descriptions (UI-only, not engine state)."""

from pathlib import Path

from webapp import views

PERSONAS = {
    "learner_0000": {"role": "Badge earner",
                     "blurb": "7-day streak, Week Warrior badge earned and weekly quest done; starts at 1,500 XP."},
    "learner_0001": {"role": "Streak + freeze (main persona)",
                     "blurb": "4-day streak with Wednesday protected by a streak freeze and 1 freeze left; starts at 1,000 XP. In the exp_007 reminder group: gets the streak-risk reminders (in-app, push and email)."},
    "learner_0002": {"role": "Missed a day, push off",
                     "blurb": "Missed Thursday so the streak restarted at 1; push is off; starts at 1,700 XP."},
    "learner_0003": {"role": "Remediation done",
                     "blurb": "Failed a checkpoint and finished the remediation quest; 5-day streak, starts at 2,000 XP."},
    "learner_0004": {"role": "Remediation pending",
                     "blurb": "Failed a checkpoint; the remediation quest is still open (0 of 1); 5-day streak."},
    "learner_0005": {"role": "Push off",
                     "blurb": "Push is off so only in-app and email reach them; 5-day streak, weekly quest 2 of 3."},
    "learner_0006": {"role": "Abuse target A",
                     "blurb": "Clean history (5-day streak, 2,100 XP) before the impossible-timing and scripted-farming attacks."},
    "learner_0007": {"role": "Abuse target B",
                     "blurb": "Clean history (5-day streak, 1,000 XP) before the replay and duplicate-exploit attacks."},
    "learner_0008": {"role": "Replay and late-event lab",
                     "blurb": "Clean history, 3-day streak, 1,400 XP, no lesson yet on the demo day: the duplicate and late-event lab."},
    "learner_0009": {"role": "Email and quiet hours",
                     "blurb": "Push is off, 5-day streak: used for the email and quiet-hours demos."},
    "learner_0010": {"role": "Notification stress",
                     "blurb": "4-day streak, push on, 1 notification so far, 1,400 XP: used for dedup, daily-cap and provider-outage demos."},
    "learner_0011": {"role": "Experiment learner",
                     "blurb": "5-day streak, push on, in the exp_007 control group: never gets streak-risk reminders (the comparison group)."},
}


def personas_payload(live_dir: Path) -> list[dict]:
    profile = views.load_profile(Path(live_dir))
    rows = []
    for learner_id, info in profile["learners"].items():
        persona = PERSONAS.get(info["key"], {"role": "", "blurb": ""})
        rows.append({
            "learner_id": learner_id,
            "key": info["key"],
            "display_name": info["display_name"],
            "role": persona["role"],
            "blurb": persona["blurb"],
        })
    return sorted(rows, key=lambda row: row["key"])
