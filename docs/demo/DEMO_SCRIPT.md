# Demo run of show (about 10 minutes)

Expected results below are copied from `docs/demo/smoke_results.json`, produced by `scripts/demo_smoke.py` on a fresh sandbox (53 checks, all passing). Your numbers match exactly on a fresh sandbox; use **Reset demo** between takes.

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
| 3 | Admin **Events** | Ishaan Gupta | Lab: **Send a lesson** | "Every action is a canonical event with ids." | accepted, XP 1400 -> 1500; newest event has a correlation id; 52 events in the stream |
| 4 | Admin **Events** | Ishaan Gupta | **Resend the last event (exact duplicate)** | "Delivering the same event again changes nothing: idempotency." | duplicate, reason duplicate_event_id, XP unchanged at 1500 |
| 5 | Admin **Events** | Ishaan Gupta | **Send event 24 h late (inside 72 h window)** | "Late events inside the 72 h policy are accepted." | accepted, XP 1500 -> 1600 |
| 6 | Admin **Events** | Ishaan Gupta | **Send event 100 h late (outside window)** | "Outside the window they are rejected and logged as evidence." | reason outside_late_window, XP unchanged at 1600; shows in Ingestion decisions |
| 7 | Admin **Events** | Ishaan Gupta | **Rebuild this learner from history and compare** | "State is a deterministic fold of the events." | identical, 55 events replayed |
| 8 | Admin **Events** | Ishaan Gupta | Rule version: pick the other version, **Apply to new events**, then **Send a lesson** | "A rule change affects only new rewards; old ones keep their version." | newest reward v2, older rewards keep v1 |
| **M3: cohorts and leaderboard** | | | | | |
| 9 | Admin **Cohorts** | Priya Nair | (scroll the cohort cards) | "Learners are grouped into cohorts; ranks are deterministic." | 5 cohorts for 2026-W42, 12 learners |
| 10 | Admin **Cohorts** | Priya Nair | read the Consistency card | "The leaderboard follows the event log with no lag in this demo." | projection offset 617 = log head 617, lag 0 |
| 11 | Admin **Cohorts** | Priya Nair | **Run query probe** | "Reads are fast." | p95 rank 0.011 ms, p95 top-N 0.025 ms |
| 12 | Admin **Cohorts** | Priya Nair | **Send a lesson and measure update** | "A reward shows up in the ranks immediately." | ingest 7.06 ms + projection 6.66 ms; cohort rank 2 -> 2, global rank 11 -> 10, XP +100 |
| **M4: remediation policy** | | | | | |
| 13 | Admin **Policy** | Tara Bose | **Fail checkpoint** (assessment fractions_checkpoint_1) | "A first failed checkpoint earns one remediation quest." | decision assign, quest deadline 2026-10-23 |
| 14 | Admin **Policy** | Tara Bose | read the tables | "The decision and the quest are stored facts." | 1 decision, 1 remediation quest (assigned) |
| 15 | Admin **Policy** | Tara Bose | **Fail checkpoint** again (same assessment) | "Only the first attempt can trigger a quest." | no_action, reason not_first_attempt |
| 16 | Admin **Policy** | Tara Bose | choose fractions_checkpoint_2, **Fail checkpoint** | "The frequency guardrail: no second quest inside 14 days." | no_action, reason remediated_within_window |
| 17 | Admin **Policy** | Tara Bose | **Complete remediation lesson** | "Finishing the quest completes it." | accepted, remediation quest completed |
| 18 | Admin **Policy** | Vikram Rao | read Remediation quests | "Another learner still has a quest open." | open remediation quest (assigned) from the seed |
| 19 | Admin **Policy** | Tara Bose | **Run quest-expiry sweep** | "Overdue quests expire through an explicit sweep." | swept 12 learners, 0 expired at the seed clock |
| **M5: notifications** | | | | | |
| 20 | Admin **Notifications** | Arjun Reddy | channel in_app, **Send one** | "Messages go through consent, caps, quiet hours and retry." | in-app message delivered |
| 21 | Admin **Notifications** | Arjun Reddy | **Send same proposal twice (dedup)** | "The same proposal is never sent twice." | second copy suppressed: duplicate_proposal |
| 22 | Admin **Notifications** | Arjun Reddy | channel push, **Send 7 in a row (daily cap)** | "A fatigue cap protects the learner." | 7 push sends: 2 delivered, 5 suppressed daily_cap_exceeded |
| 23 | Admin **Notifications** | Ananya Das | channel push, **Send one** | "Opt-outs are respected." | suppressed opt_out |
| 24 | Admin **Notifications** | Diya Menon | channel email, **Send one** | "Email is a second channel." | email delivered |
| 25 | Admin **Notifications** | Arjun Reddy | clock **+6 hours** a few times (or set 2026-10-18 12:00 UTC), switch on **Push provider outage**, push **Send one** | "A provider outage retries, then dead-letters." | 3 attempts failed, dead_letter_open 1 |
| 26 | Admin **Notifications** | Arjun Reddy | switch the outage off, **Replay dead letters** | "Recovery drains the dead-letter queue." | replayed 1, dead_letter_resolved 1 |
| 27 | Admin **Notifications** | Arjun Reddy | clock **Tonight 23:00 (quiet hours)**, push **Send one** | "Quiet hours defer instead of drop." | deferred until 2026-10-21T08:00:00Z (the date follows the clock you set) |
| 28 | Admin **Notifications** | Arjun Reddy | clock **Next day 09:00**, **Release deferred (quiet hours)** | "In the morning it is delivered." | released and delivered at 09:00 |
| **M6: abuse detection** | | | | | |
| 29 | Admin **Abuse** | Kabir Singh | **Impossible timing**, then **Scan selected learner** | "Detection is flag-only and keeps evidence." | 6 events sent, 6 accepted; signal impossible_timing; scores_changed false, event log 650/650 |
| 30 | Admin **Abuse** | Kabir Singh | **Scripted point farming**, then **Scan selected learner** | "A perfectly regular cadence looks like a script." | 14 events sent, 14 accepted; signal scripted_farming; event log 692/692 |
| 31 | Admin **Abuse** | Meera Pillai | **Replay attack**, then **Scan selected learner** | "Many redelivered events are flagged." | 12 events sent, 6 accepted, rejected duplicate_event_id 6; signal replay; event log 710/710 |
| 32 | Admin **Abuse** | Meera Pillai | **Duplicate-event exploit**, then **Scan selected learner** | "A completion re-sent under new event ids is flagged." | 8 events sent, 4 accepted, rejected duplicate_completion_id 4; signal duplicate_exploit; event log 722/722 |
| 33 | Admin **Abuse** | Meera Pillai | Flags: set filter to Everyone | "Four signal types, each with resolved evidence." | 4 signals with resolved evidence; scores never changed by a scan |
| **M7: experiment** | | | | | |
| 34 | Admin **Experiments** | Tara Bose | Step 1 **Assign all learners (deterministic)** | "Assignment is a hash, so reruns change nothing." | 12 assigned, 9 eligible, 3 excluded; second run: 0 newly assigned |
| 35 | Admin **Experiments** | Tara Bose | Step 2 table | "Half the learners get reminders, half are held out." | treatment 6, control 6 |
| 36 | Admin **Experiments** | Tara Bose | clock set to 2026-10-17 20:00 UTC, Step 3 **Run streak-risk sweep now** | "A treatment learner at risk gets a reminder." | Tara is treatment: reminder dispatched at 20:00 UTC on a day she has not practised |
| 37 | Admin **Experiments** | Tara Bose | Step 4 **Compute report** (validity, arms, hash only: the live sandbox has immature outcomes, so mastery reads "n/a"), then **Compute reference report** in the Reference run panel below it for the mastery numbers | "The primary metric is learning progress, with guardrails. The live log has no matured outcomes yet, so the numbers come from the seeded reference population." | live: valid, treatment eligible 5, control eligible 4, hash 29058b117f7c; reference (20 learners, seed 42): mastery treatment 0.333 vs control 0.625, difference -0.292, 95% CI [-0.720, 0.137], hash 8bd5769bbbd1 |
| 38 | Admin **Experiments** | Tara Bose | Step 5 **Compute the report twice** | "The report is reproducible from the log." | identical, 29058b117f7c = 29058b117f7c |
| **M8: scale and recovery** | | | | | |
| 39 | Admin **Recovery** | any | **Wipe and rebuild** (or step 1, show the empty home, then step 2) | "Derived state can be destroyed and rebuilt from the log." | 734 events replayed in 368.9 ms, checksum a9433cf9fc3f identical |
| 40 | Admin **Recovery** | any | read Notification recovery | "Dead letters are tracked." | open 0, resolved 1 |
| 41 | Admin **Recovery** | any | **Run small benchmark live**; pick `scale_xlarge_seed42.json`, **Show saved report** | "The same checks at 1,000 learners are saved evidence." | overall_passed true in 2.4 s (small); the saved xlarge report also shows PASSED |
| **Engine tick** | | | | | |
| 42 | Admin header | any | **Run engine tick** | "One button runs the scheduled jobs once." | 8 steps ok: weekly_cycle, assign, poll, quest_expiry, remediation, streak_risk, abuse, reconcile; second tick summary {'new_decisions': 0, 'new_signals': 0, 'new_notifications': 0} |
| **Close** | | | | | |
| 43 | Admin | | (no click) | "Done when: a synthetic population generates learning events, earns correct replayable rewards, enters cohorts and leaderboards, receives policy-compliant notifications, participates in controlled experiments, with abuse and failure cases handled and measurable learning-oriented outcomes reported." | Every row above is the evidence for one clause. |

Numbers that depend on how many actions you took first (XP totals, event-log counts, ranks, the 2 delivered in step 22) match the smoke run only when you follow the same order on a fresh or freshly reset sandbox.

## Recovery between takes

1. Press **Reset demo** in the Admin control bar. It restores the seeded event store, leaderboard, notifications and per-learner clocks, and clears the rule-version override, outage switches and sent-event history.
2. Clock changes left behind by each section, all cleared by Reset demo:
   - M5: Arjun Reddy's clock (steps 25, 27, 28). To undo by hand, use **Set (UTC)** with `2026-10-16T18:00`.
   - M7: Tara Bose's clock (step 36). Same fix.
   - Policy and Abuse use the seeded clock and leave it alone.
3. Experiment assignments, abuse signals and the saved dead letters are stored in the sandbox, so Reset demo is the only way to clear them. Restarting with `scripts/start_demo.sh --fresh` also rebuilds the seed.
4. The learner home pane polls the demo version every two seconds; after Reset demo give it a moment to refresh.

## If something looks different on the day

- `webapp/demo_personas.py`: roles and one-line descriptions shown in the learner dropdowns.
- `webapp/demo_admin_abuse.py`: the four attack shapes and their thresholds.
- `webapp/demo_admin_lab.py`: the duplicate, late-event, rebuild and rule-version lab.

Re-run the whole check, which also rewrites `docs/demo/smoke_results.json` and `docs/demo/smoke_output.txt`:

```bash
.venv/bin/python scripts/demo_smoke.py
```
