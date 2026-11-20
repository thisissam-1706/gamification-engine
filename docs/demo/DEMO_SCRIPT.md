# Demo run of show (about 10 minutes)

Expected results below are copied from `docs/demo/smoke_results.json`, produced by `scripts/demo_smoke.py` on a fresh sandbox (69 checks, all passing). Your numbers match exactly on a fresh sandbox; use **Reset demo** between takes.

## Start

```bash
scripts/start_demo.sh --fresh
```

Open these two URLs:

- Learner split view: <http://localhost:8000/demo.html> (lesson on the left, learner home on the right)
- Admin console: <http://localhost:8000/admin.html>

Recommended layout: browser window 1 with `demo.html` on the left half of the screen, browser window 2 with `admin.html` on the right half. The Admin link in the split-view top bar opens the admin console for the learner currently selected. Between takes press **Reset demo** in the Admin control bar (it restores the seeded data, clocks and counters).

Use `--fresh` the first time after pulling this version: the seed now stamps learner creation on the simulated timeline, so an older sandbox directory would make everyone ineligible for the experiment.

## Run of show

Select the learner in the Admin control bar (top left). Persona names in the table are the learner to pick. Tabs are named in bold.

| Step | Where | Learner | Click | Say | Expected result |
|---|---|---|---|---|---|
| **Intro** | | | | | |
| 1 | Split view, lesson pane | Priya Nair | Answer the five questions, then the summary button | "A learner finishes a lesson; the engine awards XP from an immutable event." | Lesson accepted; XP 1000 -> 1100 (+100) |
| 2 | Split view, home pane | Priya Nair | (watch; it updates by itself) | "The home page is a live read of engine state; confetti marks the reward." | Streak 4 -> 4 (she already practised on the demo day, so it holds) |
| **M1/M2: events, idempotency, rebuild** | | | | | |
| **M3: cohorts and leaderboard** | | | | | |
| 9 | Admin **Cohorts** | Priya Nair | (scroll the cohort cards) | "Learners are grouped into cohorts; ranks are deterministic." | 5 cohorts for 2026-W42, 12 learners |
| 11 | Admin **Cohorts** | Priya Nair | **Run query probe** | "Reads are fast." | p95 rank 0.011 ms, p95 top-N 0.025 ms |
| 12 | Admin **Cohorts** | Priya Nair | **Send a lesson and measure update** | "A reward shows up in the ranks immediately." | ingest 7.06 ms + projection 6.66 ms; cohort rank 2 -> 2, global rank 11 -> 10, XP +100 |
| **M4: quests and remediation policy** | | | | | |
| 12a | Admin **Quests & Policy** | Tara Bose | read the Quests card (fail a checkpoint on the lesson page to see a practice quest appear) | "Quests are goals with a deadline: a weekly sprint for everyone, and a practice quest the policy hands out." | weekly lesson sprint listed with progress / target, status and deadline |
| 18 | Admin **Quests & Policy** | Vikram Rao | read the Quests card | "Another learner still has a quest open." | Practice quest 0 / 1 (assigned) from the seed |
| **M5: notifications** | | | | | |
| 20 | Admin **Notifications** | Arjun Reddy | channel in_app, **Send one** | "Messages go through consent, caps, quiet hours and retry." | in-app message delivered |
| 22 | Admin **Notifications** | Arjun Reddy | channel push, **Send 7 in a row (daily cap)** | "A fatigue cap protects the learner: at most 5 a day on each channel (in-app, push, email), reset at local midnight." | 7 push sends: 5 delivered, 2 suppressed daily_cap_exceeded; tile shows push 5 (in-app and email keep their own 5) |
| 22a | Admin **Notifications** | Arjun Reddy | read Where each channel goes and Delivered to phone and email | "In-app goes to the home inbox, push to the (simulated) phone, email to a fake inbox." | the delivered pushes appear as phone notifications with the demo-clock time |
| 22b | Admin **Notifications** | Diya Menon | **Create a free Ethereal fake inbox** (once, needs internet), channel email, **Send one**, then **Open the inbox** | "Email is real SMTP into a fake inbox that never delivers." | email delivered; it shows in the Ethereal inbox and in the outbox with an open-this-email link |
| 23 | Admin **Notifications** | Ananya Das | channel push, **Send one** | "Opt-outs are respected." | suppressed opt_out |
| 24 | Admin **Notifications** | Diya Menon | channel email, **Send one** | "Email is a second channel." | email delivered (local outbox when no Ethereal inbox is set up) |
| 25 | Demo split view | Priya Nair | **⋯ → Push provider: up** so it reads DOWN, **Next day**, then **Same day, evening**; then **⋯ → Push provider** again | "A provider outage retries, then dead-letters; recovery re-sends exactly once." | push dead-lettered after 3 attempts (badge "Push provider DOWN"); after bringing it back, "Push back: re-sent 1" and the reminder pops up |
| 27 | Admin **Notifications** | Arjun Reddy | clock **Tonight 23:00 (quiet hours)**, push **Send one** | "Quiet hours defer instead of drop." | deferred until 2026-10-21T08:00:00Z (the date follows the clock you set) |
| 28 | Admin **Notifications** | Arjun Reddy | clock **Next day 09:00** | "In the morning it is delivered." | the demo scheduler delivers it as the clock passes 08:00 (timeline: held, released; then delivered at 09:00). **Release held messages now** finds nothing left to do |
| **M6: abuse detection** | | | | | |
| 32a | Admin **Abuse** | Rohan Iyer | Lesson page: finish a lesson, then **Redo this lesson** 4 times (use **⋯ → +1 hour** between redos so only the farming rule fires) | "Repeating a finished lesson is accepted, but it counts toward the farming rule." | Live progress: repeated lessons 4 / 4, new flag scripted_farming.repeat; further lessons earn 0 XP for 24 h (XP hold) |
| 32b | Admin **Abuse** | Vikram Rao | **Freeze clock**, finish 4 lessons on the Lesson page | "Lessons with no time between them are impossible." | 4th lesson: Abuse flag raised impossible_timing.short_gaps; the Events tab shows the four lessons at the frozen demo time |
| 33 | Admin **Abuse** | any | Flags: set filter to Everyone, then **Scan everyone** | "Each flag keeps resolved evidence; a scan never changes scores." | scripted_farming (Rohan) and impossible_timing (Vikram) with resolved evidence; scores changed: no |
| **M7: experiment** | | | | | |
| 34 | Admin **Experiments** | Priya Nair | Step 1 **Assign all learners (deterministic)** | "Assignment is a hash, so reruns change nothing." | newly assigned + already assigned = 12 (a learner whose clock already passed 20:00 was assigned by the scheduler), 9 eligible, 3 excluded; second run: 0 newly assigned |
| 35 | Admin **Experiments** | Priya Nair | Step 2 table | "Half the learners get reminders, half are held out." | treatment 6, control 6; Priya Nair is treatment, Tara Bose is control |
| 36 | Admin **Experiments** | Priya Nair, then Tara Bose | clock set to 2026-10-17 20:00 UTC, Step 3 **Run streak-risk sweep now** | "A treatment learner at risk gets a reminder on every channel; a control learner gets none." | Priya: reminder dispatched at 20:00 UTC on in-app, push and email; Tara: not_treatment, no reminder |
| 37 | Admin **Experiments** | Priya Nair | Step 4 **Compute report** (validity, arms, hash only: the live sandbox has immature outcomes, so mastery reads "n/a"), then **Compute reference report** in the Reference run panel below it for the mastery numbers | "The primary metric is learning progress, with guardrails. The live log has no matured outcomes yet, so the numbers come from the seeded reference population." | live: valid, treatment eligible 5, control eligible 4, hash 77cd96207f93; reference (20 learners, seed 42): mastery treatment 0.333 vs control 0.625, difference -0.292, 95% CI [-0.720, 0.137], hash e97e52762dde |
| 38 | Admin **Experiments** | Priya Nair | Step 5 **Compute the report twice** | "The report is reproducible from the log." | identical, 77cd96207f93 = 77cd96207f93 |
| **M8: scale and recovery** | | | | | |
| 39 | Admin **Recovery** | any | **Wipe and rebuild** (or step 1, show the empty home, then step 2) | "Derived state can be destroyed and rebuilt from the log." | 734 events replayed in 368.9 ms, checksum a9433cf9fc3f identical |
| 40 | Admin **Recovery** | any | read Notification recovery | "Dead letters are tracked." | open 0, resolved 1 |
| 41 | Admin **Recovery** | any | **Run small benchmark live**; pick `scale_xlarge_seed42.json`, **Show saved report** | "The same checks at 1,000 learners are saved evidence." | overall_passed true in 1.9 s (small); the saved xlarge report also shows PASSED |
| **Engine tick** | | | | | |
| 42 | Admin header | any | **Run engine tick** | "One button runs the scheduled jobs once." | 8 steps ok: weekly_cycle, assign, poll, quest_expiry, remediation, streak_risk, abuse, reconcile; second tick summary {'new_decisions': 0, 'new_signals': 0, 'new_notifications': 0} |
| **Close** | | | | | |
| 43 | Admin | | (no click) | "Done when: a synthetic population generates learning events, earns correct replayable rewards, enters cohorts and leaderboards, receives policy-compliant notifications, participates in controlled experiments, with abuse and failure cases handled and measurable learning-oriented outcomes reported." | Every row above is the evidence for one clause. |

Numbers that depend on how many actions you took first (XP totals, event-log counts, ranks, the 2 delivered in step 22) match the smoke run only when you follow the same order on a fresh or freshly reset sandbox.

## Recovery between takes

1. Press **Reset demo** in the Admin control bar. It restores the seeded event store, leaderboard, notifications and per-learner clocks, and clears the rule-version override, outage switches, the delivered-messages outbox and sent-event history. The Ethereal email account is kept (it is saved next to the sandbox, not inside it); **Disconnect** removes it.
2. Clock changes left behind by each section, all cleared by Reset demo:
   - M5: Arjun Reddy's clock (steps 25, 27, 28). To undo by hand, use **Set (UTC)** with `2026-10-16T18:00`.
   - M7: Priya Nair's and Tara Bose's clocks (step 36). Same fix.
   - Policy and Abuse use the seeded clock and leave it alone.
3. Experiment assignments, abuse signals and the saved dead letters are stored in the sandbox, so Reset demo is the only way to clear them. Restarting with `scripts/start_demo.sh --fresh` also rebuilds the seed.
4. The learner home pane polls the demo version every two seconds; after Reset demo give it a moment to refresh.

## If something looks different on the day

- `webapp/demo_personas.py`: roles and one-line descriptions shown in the learner dropdowns.
- `webapp/demo_admin_abuse.py`: the four attack shapes and their thresholds.
- `webapp/demo_admin_lab.py`: the duplicate, late-event, rebuild and rule-version lab. Its admin card was removed from the Events tab on 2026-11-21; the endpoints remain and the smoke script still checks them.

Re-run the whole check, which also rewrites `docs/demo/smoke_results.json` and `docs/demo/smoke_output.txt`:

```bash
.venv/bin/python scripts/demo_smoke.py
```
