# Demo Sandbox Plan: show every milestone (M1 to M8) live

This file is the complete work order for a coding agent. Read the whole file first, then do Task 1 to Task 11 in order.

---

## !!! GIT RULE (applies to every task, no exceptions) !!!

**Do not run any git command that changes anything.** That means no `git add`, `git commit` (not even at the end), `git branch`, `git checkout`, `git switch`, `git stash`, `git reset`, `git restore`, `git merge`, `git rebase`, `git cherry-pick`, `git tag`, `git push`, `git pull`, `git fetch`, or anything else that touches the working tree state, the index, branches, history or the remote `origin`.
The owner handles all git operations personally. Leave every change you make as plain uncommitted file changes.
Read-only commands (`git status`, `git diff`, `git log`, `git show`) are allowed.

---

## Global rules

**Order and pace.** Work through the tasks in numeric order. Finish each task completely, including its verification step, before starting the next. Do not pause between tasks to ask for confirmation. Later tasks depend on files that earlier tasks create.

**Progress file.** Before Task 1, create `docs/audit/demo-06-progress.md` containing a checklist of the 11 tasks (`- [ ] Task 1: ...`). Tick each box when the task is completely done and verified.

**Audit file per task.** Each task names its own audit file in `docs/audit/`. Keep it as you work with these sections: Changes, Before/After, Exact commands and output, Assumptions, Uncertainty. Complete it before moving on.

**Scope.** Only create or edit files under `webapp/` and `docs/`. Do not edit anything under `src/` or `tests/`. Do not edit existing files under `scripts/`. The only exception is Task 11, which creates two new files in `scripts/`. Do not write tests (verification uses throwaway smoke commands, described in each task).

**Frontend style.** Plain HTML, CSS and ES5-style JavaScript in `webapp/static` (`var`, `function`, IIFE; no frameworks, no build step, no new dependencies). Copy the style of `webapp/static/app.js` and `lesson.js`. Reuse the classes and CSS variables of `webapp/static/styles.css`.

**Backend rules for every admin module** (Task 1 writes them into a comment block in `webapp/demo_admin.py`):
1. Every write endpoint bumps the demo version with `demo_backend._bump_version(sandbox.root)`.
2. `demo_backend._submit_lock` is a non-reentrant `threading.Lock`. An endpoint that calls `Sandbox.submit_lesson_completed`, `submit_checkpoint`, `resubmit_event`, `resubmit_last_lesson` or `set_clock` must NOT hold the lock itself, because those methods take it. An endpoint that does not call them may hold the lock for its whole body.
3. Open and close every SQLite connection, store and `LearnerApplication` inside the request, closing them in `finally`.
4. All admin routes exist only when `DEMO_MODE=1` (they are included from the existing `if DEMO_MODE:` block of `webapp/app.py`, before the static mount).

**Verification pattern used by every task.** Run `python -m py_compile` on every changed `.py` file and `node --check` on every changed `.js` file. Then run a smoke check with FastAPI `TestClient` against a temporary sandbox: set `DEMO_MODE=1` and `DEMO_SANDBOX_PATH` to a new temp directory **before** importing `webapp.app`. If a verification command fails, fix the cause in the code and re-run until it passes. Paste exact commands and outputs into the task's audit file.

**Persona ids** (from `webapp/demo_data/ui_profile.json`):

| Learner | Id | Push |
|---|---|---|
| Aarav Mehta (learner_0000) | c6bb39cc-7ede-5881-83ca-1f84ebd502c1 | on |
| Priya Nair (learner_0001) | 4871b5d7-1673-5416-8a37-48353d80e6a4 | on |
| Rohan Iyer (learner_0002) | a9ec8603-758d-5d9e-bfce-e60a1d311ff6 | off |
| Sneha Kulkarni (learner_0003) | bc056f39-1fae-5982-80d8-82e8650f7f40 | on |
| Vikram Rao (learner_0004) | 8da11022-e214-5ce4-ba9d-57d72dcb0448 | on |
| Ananya Das (learner_0005) | 062566b7-92cc-58c1-baa3-02fe4ffd8f5d | off |
| Kabir Singh (learner_0006) | 1d6b3c14-06bc-5524-9b40-7007ff5ed83e | on |
| Meera Pillai (learner_0007) | bca1da5f-f106-526d-9bf3-35d91e61b374 | on |
| Ishaan Gupta (learner_0008) | b1102b23-33f4-570e-b81d-e3b9ac243cfc | on |
| Diya Menon (learner_0009) | afabc3f3-e17b-568b-b824-8123953874f8 | off |
| Arjun Reddy (learner_0010) | 5e45c482-1318-52ec-8b1c-f116f08db37f | on |
| Tara Bose (learner_0011) | a4b67f76-95be-50c1-8157-55022bf4e70d | on |

**Existing system facts** (already verified by reading the repo):
- `webapp/demo_backend.py` has `Sandbox` (golden and live SQLite copies, per-learner demo clock in `live/demo_state.db`, `submit_lesson_completed`, `submit_checkpoint`, `reset`) and a module-level `_submit_lock`, `_bump_version`, `_project_new_notifications`, `_get_last_offset`, `_set_last_offset`.
- `webapp/demo_routes.py` exposes `/api/demo/...` routes (clock, lesson-complete, checkpoint, reset, version). `webapp/app.py` mounts them only when `DEMO_MODE=1`.
- Pages: `index.html` (learner home, polls `/api/demo/version`), `lesson.html`, `demo.html` (split view). The seed epoch is `2026-W42`, `AS_OF = 2026-10-16T18:00:00Z`.
- Engine entry points used below: `src/abuse/service.py` (`AbuseDetectionService`), `src/abuse/store.py` (`AbuseSignalStore`), `src/notifications/dispatcher.py` (`NotificationDispatcher.dispatch`, `release_rescheduled`, `replay_dlq`), `src/notifications/queue.py` (`RetryScheduler`), `src/experiments/*` (`run_experiment`, `assign_and_record_eligibility`, `ExperimentStore`), `src/notifications/streak_risk.py` (`run_streak_risk_sweep`), `src/scale_and_recovery/*`, `scripts/run_engine_tick.py` (`run_tick`), `scripts/run_scale_benchmark.py` (`run_benchmark`).

## Task overview

| Task | Milestone | Deliverable |
|---|---|---|
| 1 | shell | Admin page skeleton, 7 milestone tabs, clock bar, personas |
| 2 | M1/M2 | Events tab (event history, ids, ingestion decisions) |
| 3 | M2 | Lab: duplicate, late event, rebuild-compare, rule-version switch |
| 4 | M3 | Cohorts tab (membership, ranks, consistency, latency) |
| 5 | M4 | Policy tab (remediation decisions, guardrail) |
| 6 | M5 | Notifications tab (channels, quiet hours, dedup, cap, outage, DLQ) |
| 7 | M6 | Abuse tab (four attacks, detection, evidence) |
| 8 | M7 | Experiments tab (assignment, exposure, report, reproducibility) |
| 9 | M8 | Recovery tab (wipe and rebuild, DLQ view, benchmark) |
| 10 | all | "Run engine tick" button |
| 11 | all | End-to-end smoke script, start script, run-of-show document |

---

## Task 1: Admin shell, personas, clock bar

Audit file: `docs/audit/demo-06-d1-admin-shell.md`

GOAL: Build the skeleton of a demo Admin page with one tab per milestone, plus a persona list. Later tasks fill the tabs.

STEP 0. Read completely before changing anything: `webapp/app.py`, `webapp/demo_routes.py`, `webapp/demo_backend.py`, `webapp/views.py`, `webapp/seed.py`, `webapp/static/demo.html`, `demo.js`, `demo.css`, `styles.css`, `webapp/demo_data/ui_profile.json`.

STEP 1. Create `webapp/demo_personas.py`.
- A dict `PERSONAS` keyed by learner key (`"learner_0000"` to `"learner_0011"`). Each value is `{"role": <short title>, "blurb": <one sentence>}`. Use exactly these roles:
  - learner_0000 Aarav Mehta: "Badge earner" (7-day streak and the Week Warrior badge)
  - learner_0001 Priya Nair: "Streak + freeze (main persona)" (has an available streak freeze)
  - learner_0002 Rohan Iyer: "Missed a day, push off" (broke a streak, push opted out)
  - learner_0003 Sneha Kulkarni: "Remediation done" (completed a remediation quest)
  - learner_0004 Vikram Rao: "Remediation pending" (has an open remediation quest)
  - learner_0005 Ananya Das: "Push off" (opted out of push, uses in-app and email)
  - learner_0006 Kabir Singh: "Abuse target A" (impossible-timing and scripted-farming attacks)
  - learner_0007 Meera Pillai: "Abuse target B" (replay and duplicate-exploit attacks)
  - learner_0008 Ishaan Gupta: "Replay and late-event lab" (duplicate and late events)
  - learner_0009 Diya Menon: "Email and quiet hours" (push off, email and quiet-hours demos)
  - learner_0010 Arjun Reddy: "Notification stress" (fatigue cap and provider outage demos)
  - learner_0011 Tara Bose: "Experiment learner" (experiment sweep demo)
- A function `personas_payload(live_dir)` that loads `views.load_profile(live_dir)` and returns a list (sorted by key) of `{"learner_id", "key", "display_name", "role", "blurb"}`.

STEP 2. Create `webapp/demo_admin.py`.
- A contextmanager `open_app(sandbox)` yielding a `LearnerApplication` on `sandbox.live_dir / "events.db"` (same env dict `demo_backend.py` uses), always closed in `finally`.
- `learner_snapshot(sandbox, learner_id) -> dict` with keys `xp_total`, `streak` (`learner.new_streak`), `freezes` (`learner.freezes_remaining`), `active_days` (`len(learner.activity_dates)`), `ruleset` (`views.learner_rule_version(learner)`). Raise `fastapi.HTTPException(404)` for an invalid UUID or `AggregateNotFoundError`.
- `global_clock(sandbox) -> datetime`: the maximum `now_utc` over all rows of table `learner_clock` in `live/demo_state.db`, or the parsed `webapp.seed.AS_OF` when the table is empty. Reuse `demo_backend._parse_iso_utc`.
- `create_admin_router(sandbox) -> APIRouter` with:
  - `GET /api/demo/personas` returning `personas_payload(sandbox.live_dir)`
  - `GET /api/demo/admin/learners/{learner_id}/snapshot` returning `learner_snapshot`
  - `GET /api/demo/admin/clock` returning `{"now_utc": <global_clock ISO with trailing Z>}`
- At the top of the file add a comment block "Rules for admin modules" copying the four backend rules from the Global rules section above.

STEP 3. In `webapp/app.py`, inside the existing `if DEMO_MODE:` block, import `create_admin_router` from `webapp.demo_admin` and call `app.include_router(create_admin_router(demo_sandbox))` right after the existing demo router is included (still before the static mount).

STEP 4. Create `webapp/static/admin.html`, `admin.css`, `admin.js`.

`admin.html`:
- Topbar like `demo.html`: brand "Streakwise Admin", links "Demo split view" (`demo.html`), "Learner home" (`index.html`), "Lesson" (`lesson.html`).
- A control bar: learner `<select id="adm-learner">` (option text "Display name - role"), a clock chip showing the selected learner's demo clock, buttons "+1 hour", "+6 hours", "Tonight 23:00 (quiet hours)", "Next day 09:00", "Reset demo", and an `<input type="datetime-local">` with a "Set (UTC)" button.
- A tab bar with 7 tabs, in this order, and 7 sections with ids `tab-events`, `tab-cohorts`, `tab-policy`, `tab-notifications`, `tab-abuse`, `tab-experiments`, `tab-recovery`:
  - Events (M1/M2): "Canonical events, idempotency, deterministic rebuild."
  - Cohorts (M3): "Cohorts, leaderboards, latency and consistency."
  - Policy (M4): "Remediation quest policy and frequency guardrails."
  - Notifications (M5): "Channels, preferences, quiet hours, dedup, retry, dead letters."
  - Abuse (M6): "Flag-only abuse detection with evidence."
  - Experiments (M7): "Deterministic assignment, exposure, learning-progress metric, guardrails."
  - Recovery (M8): "Rebuild, scale benchmarks, failure recovery."
  Each section has an `<h2>` with the milestone name, a one-sentence muted description (the sentence above) and an empty `<div class="adm-body"></div>`.
- Script tags in this order: `admin.js`, `admin-events.js`, `admin-cohorts.js`, `admin-policy.js`, `admin-notifications.js`, `admin-abuse.js`, `admin-experiments.js`, `admin-recovery.js`.

`admin.js` (shell, IIFE) defines `window.Admin` with:
- `api(method, url, body)` returning a Promise of parsed JSON; on a non-OK response throw an Error whose message is `body.detail || body.message || "Request failed (status)"`; after every successful non-GET call run all `onChange` callbacks.
- `learnerId()` (selected learner id); `learners` (array from `/api/demo/personas`); `onLearnerChange(fn)`; `onChange(fn)` (callbacks also run when `GET /api/demo/version`, polled every 2000 ms, returns a new number).
- `esc(text)` HTML-escape, `fmtTime(iso)` (local "Nov 14, 17:05:03"), `short(id)` (first 8 chars), `badge(text, kind)` returning `<span class="adm-badge adm-badge-KIND">TEXT</span>` with kind `ok`, `warn`, `bad` or `info`, `toast(text, kind)`, `table(columns, rows)` where columns is `[{key,label,render?}]`, returning table HTML.
- `registerTab(name, fn)`: `fn(rootElement)` is called once at init with the `.adm-body` element of section `tab-NAME`.
- Tab switching via the tab bar, remembered in `location.hash`. Selected learner remembered in the `?learner=` query param (default: Priya Nair). Clock chip refreshed on learner change and on every `onChange` (`GET /api/demo/learners/{id}/clock`). Clock buttons compute the new time from the selected learner's current clock (UTC) and `POST /api/demo/learners/{id}/clock`; "Tonight 23:00" sets today's date at 23:00 UTC; "Next day 09:00" sets the next day at 09:00 UTC. "Reset demo" POSTs `/api/demo/reset` then reloads the learner list.
- Errors from any control go to `Admin.toast`.

Each of the 7 `admin-*.js` files is a stub: `Admin.registerTab("<name>", function (root) { root.innerHTML = '<p class="muted">Coming in a later step.</p>'; });` with names `events`, `cohorts`, `policy`, `notifications`, `abuse`, `experiments`, `recovery`.

`admin.css`: page max-width 1200px centred; cards (`.adm-card`), two-column grid (`.adm-grid`, one column under 900px), tab bar, tables (`.adm-table`: compact, zebra rows, sticky header inside a scrollable wrapper), badges (`.adm-badge-ok` green, `-warn` amber, `-bad` red, `-info` blue), `.adm-json` (monospace, small, scrollable, max-height 320px), `.adm-row-linked` highlight, buttons reuse `.btn-small` from `styles.css`.

STEP 5. `webapp/static/demo.js`: build the learner `<select>` options from `GET /api/demo/personas` as "Display name - role" (ids stay as values), and add an "Admin" link in the `demo.html` topbar going to `admin.html?learner=<currently selected id>` (update the href whenever the learner changes).

STEP 6. Verify: standard verification pattern. Smoke: `GET /api/demo/personas` returns 12 items; `GET /api/demo/admin/learners/<Priya id>/snapshot` returns 200 with `xp_total`; `GET /api/demo/admin/clock` returns 200; `GET /admin.html` returns 200.

---

## Task 2: Events tab, read side (M1/M2)

Audit file: `docs/audit/demo-06-d2-events-tab.md`

GOAL: Fill the Events tab with a read-only view of a learner's event history and ingestion decisions, so the audience sees that state is a fold of immutable events with ids.

STEP 0. Read completely: `webapp/demo_admin.py`, `webapp/static/admin.js`, `admin-events.js`, `admin.css`, `src/state_machine/learner.py` (all nested event classes and their fields, and `checkpoint_chain_ids`), `src/state_machine/notification_log.py`, `src/ingestion/evidence.py` (EvidenceRecord fields), `src/ingestion/service.py` (which refusal reasons are recorded as evidence and which fields the envelope carries), `webapp/views.py` (how it opens the stores).

STEP 1. Create `webapp/demo_admin_events.py` with `create_events_router(sandbox)` and include it from `create_admin_router` in `webapp/demo_admin.py`.
- `GET /api/demo/admin/learners/{learner_id}/events?limit=60`: use `demo_admin.open_app`; read the learner's own event stream with `list(app.events.get(UUID(learner_id)))`; return the newest `limit` events newest first. Each item: `{"seq": originator_version, "type": <event class name>, "timestamp": ISO or null, "correlation_id", "causation_id", "source_event_id", "rule_version" (each null when the event has no such attribute), "fields": <JSON-safe dict of every attribute of the event except the eventsourcing internals originator_id, originator_version, originator_topic and timestamp>}`. JSON-safe means: datetime to ISO string, UUID to str, set/frozenset to sorted list, dict/list/tuple recursively, anything else `str()`. Return `{"total": <events in the stream>, "events": [...]}`.
- `GET /api/demo/admin/learners/{learner_id}/ingestion`: open `EvidenceStore(sandbox.live_dir / "evidence.db")`, call `list(learner_id)`, return newest first as `{"records": [{id, kind, reason, action_id, event_id, original_ref, received_at}], "by_reason": {reason: count}}`. Close the store in `finally`.
- A 404 for an invalid UUID or unknown learner (reuse the check in `demo_admin.learner_snapshot`).

STEP 2. Implement the tab in `webapp/static/admin-events.js` (replace the stub) and add needed CSS to `admin.css`.
- Layout, top to bottom:
  (a) Explanation paragraph: "Every row below is an immutable event. XP, streaks, quests and badges on the learner home page are a fold of these rows."
  (b) Snapshot strip with five stat tiles from `GET /api/demo/admin/learners/{id}/snapshot`: XP, Streak, Freezes, Active days, Ruleset.
  (c) An empty container `<div id="lab-slot"></div>` (Task 3 puts controls here; leave it empty now).
  (d) A two-column grid. Left card "Event history": table with columns #, Time, Type, Key facts, Correlation. "Key facts" shows the 2-3 most meaningful fields per event type (for example `xp_amount` for XPAwarded, `lesson_id` for LessonCompleted, the streak value for StreakUpdated), chosen from the fields found in `learner.py`. "Correlation" shows `Admin.short(correlation_id)` or a dash. Clicking a row shows the full event as pretty JSON in a detail card under the table and adds class `adm-row-linked` to every row with the same `correlation_id`, with a line "N events share this correlation id" above the JSON. Right card "Ingestion decisions": table of rejection evidence (Time, Reason as a badge: duplicate reasons use kind warn, late/other refusals use kind bad, Event id short, Action id), plus chips of counts by reason above the table.
- Re-render the whole tab on `Admin.onLearnerChange` and `Admin.onChange`. Show clear empty-state text ("No rejected events for this learner yet.").
- Type badges: XPAwarded (ok), StreakUpdated (info), BadgeAwarded (ok), QuestCompleted (ok); others no badge.

STEP 3. Verify: standard pattern. Smoke: `POST /api/demo/learners/<Priya id>/lesson-complete` with `{"lesson_id":"smoke_lesson"}`, then GET the events endpoint and confirm the newest events include a LessonCompleted and an XPAwarded; GET the ingestion endpoint and confirm 200.

---

## Task 3: M2 lab controls (duplicate, late, rebuild compare, rule version)

Audit file: `docs/audit/demo-06-d3-m2-lab.md`

GOAL: Let the presenter prove M2 live: duplicate events do nothing, late events follow the 72-hour policy, a learner's state can be rebuilt from history and matches exactly, and a rule-version change keeps old rewards under the old version.

STEP 0. Read completely: `webapp/demo_backend.py`, `webapp/demo_routes.py`, `webapp/demo_admin.py`, `webapp/demo_admin_events.py`, `webapp/static/admin-events.js`, `src/ingestion/service.py` (especially `canonical_digest`, how a repeated `event_id` with identical vs different content is handled, and the late-window rule), `src/ingestion/validation.py` (`expected_dedup_key`), `src/ingestion/config.py` (`IngestConfig`), `src/state_machine/projection.py` (`rebuild_learner_state`, `LearnerReplayState`), `src/state_machine/learner.py` (`applied_rule_versions`, `xp_total`, `new_streak`, `freezes_remaining`, `activity_dates`, `badges_issued`, `quest_instances`), and the module providing `get_ruleset` (grep `def get_ruleset` under `src/`; list the rule versions it knows).

STEP 1. In `webapp/demo_backend.py` add three small demo tables, created with `CREATE TABLE IF NOT EXISTS` by one helper `_ensure_demo_tables(db_path)` in `live/demo_state.db`:
- `demo_sent_events(seq INTEGER PRIMARY KEY AUTOINCREMENT, learner_id TEXT, kind TEXT, event_id TEXT, envelope_json TEXT)`
- `learner_ruleset(learner_id TEXT PRIMARY KEY, version TEXT)`
- `demo_flags(key TEXT PRIMARY KEY, value TEXT)`
Call `_ensure_demo_tables` from `_init_demo_state` and at the start of every function in Step 2 that touches these tables.

STEP 2. Extend the `Sandbox` class in `webapp/demo_backend.py` (purely additive; existing behaviour and existing route bodies must keep working):
- `submit_lesson_completed` gains optional keyword parameters `completion_id: str | None = None` and `correlation_id: str | None = None`. When `completion_id` is given use it as `payload["completion_id"]` instead of `f"cmp_{evt_id}"`. When `correlation_id` is given use it in the envelope instead of a new uuid4. The returned dict keeps its keys and also gains `"event_id"`. After ingestion, always insert the exact envelope you built into `demo_sent_events` (kind `"lesson"`), whatever the ingestion status, so it can be resubmitted byte for byte.
- Add `get_ruleset_override(learner_id) -> str | None` and `set_ruleset_override(learner_id, version | None)` (None deletes the row; take `_submit_lock` and bump the version when changed). In `submit_lesson_completed` and `submit_checkpoint`, `rule_version = override if present else learner_rule_version(learner)`. The dedup key and `IngestConfig` must use that `rule_version`.
- Add `resubmit_event(learner_id, event_id) -> dict` and `resubmit_last_lesson(learner_id) -> dict`: load the stored envelope from `demo_sent_events` (latest row of kind `"lesson"` for `resubmit_last_lesson`), ingest it again with `ingest_lesson_completed` using exactly the stored envelope (same ids, same `occurred_at`, same `correlation_id`, same `dedup_key`), the learner's demo clock as the config clock, inside `_submit_lock`; project new notifications and bump the version when accepted; return `{"status","reason","event_id"}`. Return `{"status":"rejected","reason":"no_event_to_resubmit"}` when nothing is stored.
- Update `webapp/demo_routes.py`: `LessonRequest` gets optional `event_id`, `occurred_at`, `completion_id` (all `str | None`) and passes them to `submit_lesson_completed`; nothing else changes there.

STEP 3. Create `webapp/demo_admin_lab.py` with `create_lab_router(sandbox)` and include it from `create_admin_router`. All endpoints under `/api/demo/admin/learners/{learner_id}/lab/`. Each returns `{"result": <submit result>, "before": learner_snapshot, "after": learner_snapshot}` (take "before" before the call and "after" after it). Never hold `_submit_lock` in these endpoints.
- `POST fresh-lesson`: `lesson_id` = `f"lab_lesson_{n}"` where n is the count of existing `demo_sent_events` rows for that learner plus 1.
- `POST duplicate`: `sandbox.resubmit_last_lesson(learner_id)`.
- `POST late {hours_late: float}`: `occurred_at` = learner demo clock minus `hours_late` hours, `lesson_id` `f"lab_late_{n}"`, passed to `submit_lesson_completed(occurred_at=...)`. The response also echoes `hours_late`.
- `POST rebuild-compare`: open the app; `learner = repository.get`; `replay = rebuild_learner_state(list(app.events.get(uuid)))`; compare side by side: `xp_total`, `new_streak`, `freezes_remaining`, `activity_dates` (sorted list), completed quest ids (from `learner.quest_instances` with status `"completed"` versus `replay.completed_quests`), badge ids (keys of `learner.badges_issued` versus keys of `replay.badges_issued`). Return `{"rows":[{"field","live","rebuilt","match"}], "identical": all(match), "events_replayed": <events in the stream>}`. This endpoint changes no state, so no version bump.
- `GET ruleset` returns `{"override": ..., "effective": views.learner_rule_version(learner), "available": <rule versions get_ruleset knows>}`; `POST ruleset {version: str | null}` validates the version against `available` (HTTP 422 otherwise) and calls `set_ruleset_override`.

STEP 4. Build the controls in `webapp/static/admin-events.js` inside `<div id="lab-slot">` as a card "Replay and late-event lab" with the muted text "Pick the replay/late-event learner (Ishaan Gupta) to keep the story clean.":
- Buttons: "Send a lesson", "Resend the last event (exact duplicate)", "Send event 24 h late (inside 72 h window)", "Send event 100 h late (outside window)".
- A result strip after every action: badge of status (accepted = ok, rejected/duplicate = warn), the reason text, "XP 120 -> 140 (+20)" computed from before/after, and streak before -> after.
- A button "Rebuild this learner from history and compare": renders the rows table with a green check or red cross per row and a bold banner "Rebuilt state is identical to live state" when identical.
- A "Rule version" row: a select of available versions (preselected with the effective one) and a button "Apply to new events", plus the sentence: "Old rewards keep the version they were issued under; only events sent from now on use the new one."
- Every `Admin.api` write already triggers `onChange`, so the event history below reloads on its own.

STEP 5. Verify: standard pattern. Smoke for Ishaan Gupta: `fresh-lesson` returns accepted and XP increases; `duplicate` returns rejected/duplicate with XP unchanged; `late` with 24 returns accepted; `late` with 100 returns a rejection with reason `outside_late_window`; `rebuild-compare` returns identical true; `POST ruleset` with the second available version, then `fresh-lesson`, then GET the events endpoint and confirm the newest events carry the new rule version while older ones keep the old one.

---

## Task 4: Cohorts tab (M3)

Audit file: `docs/audit/demo-06-d4-cohorts-tab.md`

GOAL: The Cohorts tab shows who is in which cohort and why, ranks at cohort and global level, the consistency semantics in plain words, and measured query and update latency.

STEP 0. Read completely: `webapp/demo_admin.py`, `webapp/views.py` (especially `_rows`, `_leaderboard` and how they open the leaderboard store), `webapp/demo_backend.py` (`submit_lesson_completed`, `_project_new_notifications`), `webapp/static/app.js` (`renderLeaderboard` and the `lb-footnote` text), `src/leaderboard/service.py`, `projection.py`, `store.py`, `cohort_builder.py` (what a CohortAssigned event stores and which constraints the builder uses), and every file under `docs/adr/` whose name or content mentions leaderboard, cohort or consistency (read the relevant parts to learn the real consistency semantics: ordering, ties, `first_reached_at`, offsets, eventual vs synchronous).

STEP 1. In `webapp/demo_backend.py` make `submit_lesson_completed` measure time: wrap the ingest call and the `_project_new_notifications` call with `time.perf_counter` and add `"timings_ms": {"ingest": float, "project": float}` (rounded to 2 decimals; `"project"` is 0.0 when nothing was projected) to the returned dict. Purely additive.

STEP 2. Create `webapp/demo_admin_cohorts.py` with `create_cohorts_router(sandbox)` and include it from `create_admin_router`.
- `GET /api/demo/admin/cohorts`: epoch and cohort display names from `views.load_profile(live_dir)`; open the leaderboard store the same way `views` does (read-only) and `LeaderboardService`; for every learner in the profile return `display_name`, `learner_id`, `cohort_id` (from the learner's CohortAssigned event in the event stream, via `demo_admin.open_app`), cohort display name, `cohort_score`, `cohort_rank`, `global_score`, `global_rank`, `first_reached_at`, and a dict `"constraints"` containing every field the CohortAssigned event stores besides ids (for example tier, module or bucket info, whatever exists). Group them into a list of cohorts `{cohort_id, name, member_count, members: [...sorted by cohort_rank]}`.
- `GET /api/demo/admin/cohorts/consistency`: `{"projection_offset": <last offset in demo_state.db>, "log_head": <id of the last entry in the event notification log>, "lag": log_head - projection_offset, "mode": "synchronous in demo", "semantics": [4 to 5 short plain-English strings]}`. Write the semantics strings yourself from what you read in the ADRs and leaderboard code (accurate: how ranks are ordered and tie-broken, when a read sees an update, what happens after a crash, what a rebuild guarantees). Do not invent guarantees the code does not give.
- `POST /api/demo/admin/cohorts/latency-probe`: run 200 timed `get_learner_rank` calls (cycling over the learners, alternating cohort and global scope) and 200 timed top-N queries (cohort and global) against the live leaderboard with `time.perf_counter`; return `{"queries": 400, "rank_ms": {"p50","p95","p99","max"}, "top_ms": {...}}`. No state change, no version bump.
- `POST /api/demo/admin/learners/{learner_id}/cohorts/measure-update`: read cohort rank, global rank and XP before; call `sandbox.submit_lesson_completed(learner_id, f"lab_rank_{unix_ms}")` without holding any lock yourself; read the same values after; return `{"result", "timings_ms", "before", "after"}`.

STEP 3. Implement the tab in `webapp/static/admin-cohorts.js` (replace the stub) plus CSS:
- Top card "Cohorts (week <epoch>)": one sub-card per cohort with its display name and member count, and a members table (Rank, Learner, XP, Global rank, First reached); highlight the selected learner's row; below the table a small muted line listing the constraints from the first member (for example "grouped by activity tier and module").
- Card "Consistency": projection offset, log head and lag as three tiles and the semantics strings as a bullet list.
- Card "Latency": button "Run query probe" showing a small table of p50/p95/p99/max for rank and top-N lookups; button "Send a lesson and measure update" for the selected learner showing "Ingest X ms + projection Y ms = Z ms" and "Cohort rank 4 -> 2, Global rank 9 -> 6, XP +20" with arrows.
- Re-render on `Admin.onLearnerChange` and `Admin.onChange`.

STEP 4. Verify: standard pattern. Smoke: GET cohorts returns at least one cohort with members; GET consistency returns lag 0 on a fresh sandbox; POST latency-probe returns numbers; POST measure-update for Priya returns `timings_ms` and an XP increase.

---

## Task 5: Policy tab (M4)

Audit file: `docs/audit/demo-06-d5-policy-tab.md`

GOAL: The Policy tab lets the presenter fail a checkpoint, watch the engine assign a remediation quest, fail again and watch the frequency guardrail suppress a second one, complete the remediation, and run the quest-expiry sweep.

STEP 0. Read completely: `webapp/demo_admin.py`, `webapp/demo_backend.py` (`submit_checkpoint` including its remediation and notification step), `webapp/views.py` (`_quests`), `src/interventions/config.py`, `policy.py`, `service.py`, `notification_hook.py`, the relevant parts of `src/state_machine/learner.py` (checkpoint attempt events, remediation decision events, quest instances with statuses and deadlines), and the ADR for the intervention policy (run `ls docs/adr` and read the file named like `ADR-0007*`).

STEP 1. Create `webapp/demo_admin_policy.py` with `create_policy_router(sandbox)` and include it from `create_admin_router`.
- `GET /api/demo/admin/policy/config`: a list of `{"name","value","meaning"}` for every setting in the interventions config objects (window days, quest deadline days, frequency limits, quest id, and so on). "meaning" is a short plain-English sentence you write.
- `GET /api/demo/admin/learners/{learner_id}/policy`: open the app via `demo_admin.open_app` and return `{"attempts": [...], "decisions": [...], "quests": [...]}`:
  - attempts: every checkpoint attempt (`attempt_id`, `assessment_id`, `passed`, `occurred_at`) newest first;
  - decisions: every remediation decision the engine persisted for this learner (find the event or record in `learner.py` and `service.py`; give `attempt_id`, decision, reason, assignment id if any, timestamp) newest first;
  - quests: remediation quests only (assignment id starting with `"remediation_quest:"`), each with status, `assigned_at`, deadline, progress if the instance stores it.
  Use only data that exists; do not invent fields.
- `POST /api/demo/admin/learners/{learner_id}/policy/checkpoint {"passed": bool}`: call `sandbox.submit_checkpoint(learner_id, "fractions_checkpoint_1", passed)` (never hold `_submit_lock` yourself); return `{"result", "latest_decision": <newest decision or null>, "snapshot": learner_snapshot}`.
- `POST /api/demo/admin/learners/{learner_id}/policy/complete-remediation`: call `sandbox.submit_lesson_completed(learner_id, f"remediation_lesson_{unix_ms}", completion_kind="remediation")`; return `{"result","snapshot"}`.
- `POST /api/demo/admin/policy/expiry-sweep`: run the interventions expiry function (`run_quest_expiry` in `src/interventions/service.py`; read its exact signature) for all learners in the profile with `now` = the demo clock of the learner given as `{"learner_id"}` in the body, save every changed aggregate the way the function expects, bump the version with `demo_backend._bump_version(sandbox.root)`, and return what it reports (counts and expired quest ids) as JSON-safe data. Hold `demo_backend._submit_lock` for the duration of this endpoint and do not call Sandbox submit or clock methods inside it.

STEP 2. Implement the tab in `webapp/static/admin-policy.js` (replace the stub) plus CSS:
- Card "Policy settings": the config table.
- Card "Try it" for the selected learner with buttons "Fail checkpoint", "Pass checkpoint", "Complete remediation lesson", "Run quest-expiry sweep". After a checkpoint click show a result strip: the ingestion status, the engine's latest decision as a badge (assigned = ok; suppressed or skipped = warn) with its reason text.
- A muted hint under the buttons explaining how to see the guardrail, written from what `policy.py` really does (for example "Fail a checkpoint, then fail another inside the window: the second one is recorded but no new quest is assigned"); the text must match the actual rules you read, using the real window and limit values from the config.
- Tables: "Checkpoint attempts" (Time, Attempt, Result badge pass/fail), "Engine decisions" (Time, Attempt, Decision badge, Reason, Quest), "Remediation quests" (Quest, Status badge, Assigned, Deadline).
- Re-render on `Admin.onLearnerChange` and `Admin.onChange`.

STEP 3. Verify: standard pattern. Smoke for Tara Bose: fail a checkpoint, read the policy endpoint and confirm a decision and a remediation quest exist; fail again and confirm the second decision is the guardrail outcome `policy.py` defines; `complete-remediation` returns accepted; the expiry sweep returns 200.

---

## Task 6: Notifications tab (M5)

Audit file: `docs/audit/demo-06-d6-notifications-tab.md`

GOAL: The Notifications tab lets the presenter send in-app, push and email messages and show preferences, quiet hours, deduplication, the daily cap, provider retry, dead letters and recovery, all live.

STEP 0. Read completely: `webapp/demo_backend.py` (`submit_checkpoint` and its `NotificationDispatcher` usage), `webapp/demo_admin.py`, `webapp/views.py` (`_inbox`), `src/notifications/dispatcher.py` (`dispatch`, `release_rescheduled`, `replay_dlq`, `check_consent`, the quiet-hours and cap logic), `src/notifications/queue.py` (`RetryScheduler`), `src/notifications/store.py` (all table schemas), `src/notifications/models.py`, every file in `src/notifications/channels/` (base adapter, in-app, simulated push, simulated email), `src/scale_and_recovery/outage.py` (how an outage adapter raises `TransientProviderError`), `webapp/demo_data/ui_profile.json`.

STEP 1. Create `webapp/demo_notify.py` (it must NOT import `webapp.demo_backend`, to avoid a circular import):
- `get_flag(live_dir, key) -> str | None` and `set_flag(live_dir, key, value | None)` using table `demo_flags` in `live/demo_state.db` (`CREATE TABLE IF NOT EXISTS demo_flags(key TEXT PRIMARY KEY, value TEXT)` before use; value None deletes the row).
- `class OutageAwareAdapter(BaseChannelAdapter)`: constructor `(live_dir, flag_key, inner)`. `send(proposal)` first reads the flag; when it equals `"1"` raise `TransientProviderError("Simulated <channel> provider outage (503 Service Unavailable)")`; otherwise return `inner.send(proposal)`.
- `prefs_for(live_dir, learner_id, timezone="UTC") -> LearnerPreferences`: `opted_out_channels = {ChannelType.PUSH}` when `ui_profile.json` (use `views.load_profile`) says `push_enabled` is false for that learner, else an empty set.
- `build_dispatcher(live_dir, notif_store, clock) -> NotificationDispatcher` with adapters `{IN_APP: the engine's in-app adapter, PUSH: OutageAwareAdapter(live_dir, "outage_push", the engine's SimulatedPushAdapter()), EMAIL: OutageAwareAdapter(live_dir, "outage_email", the engine's SimulatedEmailAdapter())}`, `retry_scheduler=RetryScheduler(max_retries=3)` with no backoff sleeping, `clock=clock`.
Then in `webapp/demo_backend.py` replace the `NotificationDispatcher(...)` construction inside `submit_checkpoint` with `demo_notify.build_dispatcher(self.live_dir, notif_store, lambda: demo_clock)` and build the preferences with `demo_notify.prefs_for(self.live_dir, learner_id, tz)`. Behaviour is otherwise unchanged.

STEP 2. Create `webapp/demo_admin_notifications.py` with `create_notifications_router(sandbox)` and include it from `create_admin_router`. Use read-only sqlite3 connections (the `_ro_uri` idea in `views.py`) for reads, and `NotificationStore` plus `build_dispatcher` for writes. For writes hold `demo_backend._submit_lock` (these endpoints never call Sandbox submit or clock methods) and call `demo_backend._bump_version(sandbox.root)` afterwards.
- `GET /api/demo/admin/learners/{learner_id}/notifications`: one merged timeline (newest first) from the proposals, dispatches, suppressions, dlq and rescheduled tables for that learner. Each item: `{"time","proposal_id","channel","category","outcome": one of "delivered","suppressed","deferred","dead_letter","failed","queued","reason","attempts","dlq_id"|null,"resolved": bool|null,"deliver_at"|null}`. Also return `"counts": {delivered, suppressed, deferred, dead_letter_open, dead_letter_resolved}`, `"outage": {"push": bool, "email": bool}` from the flags, `"quiet_hours": {"start": dispatcher.quiet_start_hour, "end": dispatcher.quiet_end_hour}`, `"opted_out"`: list of channel names for that learner.
- `POST /api/demo/admin/learners/{learner_id}/notifications/send {"channel","category","count": int=1, "proposal_id": str|null, "repeat_same": bool=false}`: builds `NotificationProposal` objects (payload title and body written for the category, `created_at` = learner clock) and dispatches them with the learner's prefs and `now_utc` = learner clock. Each of `count` proposals gets a fresh id `f"demo:{uuid4().hex[:10]}"` unless `proposal_id` is supplied; when `repeat_same` is true the single proposal is dispatched twice with the same id. Return the TerminalFacts as `{status, reason, channel, proposal_id, attempt_count}`.
- `POST .../notifications/outage {"channel": "push"|"email", "active": bool}` calls `set_flag`; return the new outage state.
- `POST .../notifications/replay-dlq`: for every unresolved row of `store.get_dlq_entries(resolved=False)` of ANY learner call `dispatcher.replay_dlq(dlq_id, prefs=prefs_for(that learner), now_utc=that learner's clock)`; return a list of `{dlq_id, status, reason}`.
- `POST .../notifications/release-deferred`: `dispatcher.release_rescheduled(now_utc=<selected learner's clock>, prefs_provider=lambda learner_id: prefs_for(live_dir, learner_id))`; return the released facts as `{proposal_id, status, reason}`.

STEP 3. Implement the tab in `webapp/static/admin-notifications.js` (replace the stub) plus CSS:
- Status strip tiles: Delivered, Suppressed, Deferred (quiet hours), Dead letters open, plus chips "Push: opted out / on" and "Quiet hours 22:00-08:00" (from the response) and the learner's clock.
- Card "Send": channel select, category select (all NotificationCategory values), buttons "Send one", "Send same proposal twice (dedup)", "Send 7 in a row (daily cap)". A one-line hint under each button (dedup: second is suppressed as `duplicate_proposal`; cap: sends after the fifth push or email in 24 h are suppressed; push to a push-opted-out learner is suppressed by consent).
- Card "Provider health": two switches "Push provider outage" and "Email provider outage" (state from the flags). Hint: "While an outage is on, sends retry 3 times then land in the dead-letter queue."
- Card "Recovery": buttons "Replay dead letters" and "Release deferred (quiet hours)", with the hint "Move the clock to 23:00 with the admin bar, send a push (it is deferred), then move to the morning and release."
- A timeline table with outcome badges: delivered (ok), suppressed (warn, show reason), deferred (info, show "until <time>"), dead letter (bad, show attempts and a "resolved" badge when resolved).
- Re-render on `Admin.onLearnerChange` and `Admin.onChange`.

STEP 4. Verify: standard pattern. Smoke for Arjun Reddy (push on): send one `in_app` (delivered); send one with `repeat_same` (second suppressed `duplicate_proposal`); send 7 push (cap suppression appears); turn push outage on and send one push (a dead_letter appears); turn the outage off and `replay-dlq` (resolved); set the learner clock to 23:00 UTC the same day via `POST /api/demo/learners/<id>/clock`, send a push (deferred), set the clock to 09:00 next day, `release-deferred` (delivered). For Ananya Das (push off) send a push and confirm it is suppressed by consent.

---

## Task 7: Abuse tab (M6)

Audit file: `docs/audit/demo-06-d7-abuse-tab.md`

GOAL: The Abuse tab fires four attack patterns against a learner (replay, impossible timing, scripted point farming, duplicate-event exploit), runs the detection pass, and shows the flags with evidence, while proving scores are not changed by detection.

STEP 0. Read completely: `webapp/demo_backend.py` (including `resubmit_event` and the optional `event_id`, `occurred_at`, `completion_id` parameters of `submit_lesson_completed`), `webapp/demo_admin.py`, `src/abuse/service.py`, `detectors.py` (every detector: the exact conditions and what ends up in `evidence_refs`), `rules.py`, `abuse_rules.json` (thresholds of the default version), `store.py`, `src/ingestion/evidence.py` and `src/ingestion/service.py` (which refusals become evidence records: `duplicate_completion_id`, replays of an event id, and so on).

STEP 1. Create `webapp/demo_admin_abuse.py` with `create_abuse_router(sandbox)` and include it from `create_admin_router`. All writes in this file call `demo_backend._bump_version(sandbox.root)`.
- `POST /api/demo/admin/learners/{learner_id}/abuse/attack {"pattern": "replay" | "impossible_timing" | "scripted_farming" | "duplicate_exploit"}`. Use only `Sandbox.submit_lesson_completed` and `Sandbox.resubmit_event` (never hold `_submit_lock` yourself). Let `clock` be the learner's demo clock and use explicit `occurred_at` values in the past of `clock` so events are never future-dated:
  - impossible_timing: 6 lessons with distinct lesson ids `"attack_it_{i}"`, `occurred_at = clock - (6-i)*5 seconds` (gaps of 5 s, below the 20 s minimum).
  - scripted_farming: 14 lessons with distinct lesson ids `"attack_sf_{i}"`, `occurred_at = clock - (14-i)*90 seconds` (a perfectly regular 90 s metronome).
  - replay: 6 lessons `"attack_rp_{i}"` with `occurred_at = clock - (6-i)*300 seconds`, each accepted once, then each resubmitted once with `Sandbox.resubmit_event` using its returned `event_id` (6 replays).
  - duplicate_exploit: 4 lessons with explicit `completion_id "attack_dx_{i}"` and distinct lesson ids, `occurred_at = clock - (4-i)*300 seconds`; then 4 more submissions with the SAME completion ids but fresh event ids and later `occurred_at` values (each a resubmitted completion).
  Treat these numbers as the starting point. Read `detectors.py` and `abuse_rules.json`, then adjust counts and spacings (keeping the intent of each pattern) until `AbuseDetectionService(...).run(shadow=True)` on a scratch sandbox produces the intended `signal_type` for that pattern alone. Put the final numbers in module-level constants with a comment per pattern citing the rule threshold they exceed.
  Return `{"pattern","events_sent","accepted","rejected_by_reason": {reason: count},"before": learner_snapshot,"after": learner_snapshot}` (snapshots via `demo_admin.learner_snapshot`).
- `POST /api/demo/admin/abuse/scan {"learner_id": str | null}`: open `demo_admin.open_app`, `EvidenceStore(live/evidence.db)`, `AbuseSignalStore(live/abuse.db)`; capture before: the `xp_total` of every scanned learner and the number of entries in the event notification log; run `AbuseDetectionService(...).run(learner_ids=[learner_id] or None)` with the default rules; capture the same values after; return `{"summary": {counts_by_type, learners_scanned, events_read, new_signals, existing_signals, rules_version}, "scores_changed": bool, "event_log_before": n, "event_log_after": n, "xp_before": {...}, "xp_after": {...}}`. Close everything in `finally`. Bump the version only if new signals were stored. Hold `demo_backend._submit_lock` around the whole body of this endpoint.
- `GET /api/demo/admin/abuse/signals?learner_id=`: signals from `AbuseSignalStore.list(learner_id or None)`. For every signal return its fields, the `display_name` of the learner, and `"evidence"`: each `evidence_ref` resolved as far as the data allows (an ingestion evidence record via `EvidenceStore.list_mentioning(ref)` or by id, otherwise a completion fact from `AbuseDetectionService.completion_facts()` whose `event_id` or `completion_id` matches the ref; include time, lesson id, reason, event id); keep the unresolved ref string for anything that cannot be resolved.
- `GET /api/demo/admin/abuse/rules`: the JSON of the default rules version from `abuse_rules.json` (via `src/abuse/rules.py` `get_abuse_rules` or by reading the file), including the version name.

STEP 2. Implement the tab in `webapp/static/admin-abuse.js` (replace the stub) plus CSS:
- Explanation line: "Detection is flag-only: it never changes XP, streaks or leaderboards. It records suspected abuse with evidence."
- Card "Attack simulator" for the selected learner with four buttons ("Replay attack", "Impossible timing", "Scripted point farming", "Duplicate-event exploit") and a one-line description under each; after a click show a result strip: "N events sent, A accepted, rejected: duplicate_event 6" and "XP before -> after".
- Card "Detection pass": buttons "Scan selected learner" and "Scan everyone"; after a scan show tiles New signals and Events read, and a green banner "Scores changed by this scan: no. Event log entries before/after: N/N." (red text if they ever differ).
- Card "Flags": one card per signal: type badge (replay, impossible_timing, scripted_farming, duplicate_exploit, each with a distinct kind), a "suspected" status badge, learner name, rule id and version, detection window, then the evidence list (time, lesson, reason, short event id). Filter select: "Selected learner" or "Everyone".
- Collapsible "Rules in force" section with the rules JSON in `.adm-json`.
- Re-render on `Admin.onLearnerChange` and `Admin.onChange`.

STEP 3. Verify: standard pattern. Smoke: for Kabir Singh run `impossible_timing` then scan and confirm a signal of type `impossible_timing` for him; run `scripted_farming`, scan, confirm `scripted_farming`; for Meera Pillai run `replay`, scan, confirm `replay`; run `duplicate_exploit`, scan, confirm `duplicate_exploit`; confirm every scan response has `scores_changed` false and equal event log counts; GET signals returns evidence lists that are not all empty.

---

## Task 8: Experiments tab (M7)

Audit file: `docs/audit/demo-06-d8-experiments-tab.md`

GOAL: The Experiments tab walks through experiment `exp_007` (streak-risk reminder vs no reminder) on the sandbox's real learner log: deterministic assignment, eligibility, exposure logging through the reminder sweep, the report with its learning-progress primary metric and fatigue/guardrail metrics, and a reproducibility check.

STEP 0. Read completely: `webapp/demo_admin.py` (`global_clock`, `open_app`), `webapp/demo_notify.py` (`prefs_for`, `build_dispatcher`), `webapp/demo_backend.py`, `scripts/run_experiment.py` (the exact way assign, sweep and report call the engine), `src/experiments/service.py`, `assignment.py`, `eligibility.py`, `exposure.py`, `store.py`, `runner.py` (`run_experiment` signature, `ExperimentRunResult` and its `to_dict`, `TREATMENT_VARIANT`, `CONTROL_VARIANT`, `CONTROL_NOTE`), `metrics.py`, `fatigue.py`, `srm.py`, `learners.py`, `prefs.py`, `src/notifications/streak_risk.py` (`run_streak_risk_sweep` and `SweepOutcome` reasons), `src/abuse/service.py` (`completion_facts`), `src/scale_and_recovery/experiment_repro.py` (only to understand how reproducibility is hashed).

STEP 1. Create `webapp/demo_admin_experiments.py` with `create_experiments_router(sandbox)` and include it from `create_admin_router`. Constants: `EXP_ID = "exp_007"`, `ASSESSMENT_ID = "fractions_checkpoint_1"`, `ASSESSMENT_VERSION = 1`. The experiment store is `ExperimentStore(str(sandbox.live_dir / "experiments.db"))` (a fresh file per sandbox; opened and closed per request).
- Helper `prefs_by_learner(sandbox)` building `{learner_id: demo_notify.prefs_for(live_dir, learner_id)}` for every learner in `views.load_profile`.
- Helper `compute_report(sandbox)`: opens the app, `EvidenceStore(live/evidence.db)`, `NotificationStore(live/notifications.db)` and the `ExperimentStore`; builds `awards = awards_from_abuse_service(AbuseDetectionService(app, evidence, AbuseSignalStore(":memory:")))`; calls `run_experiment(app, store, notification_store, awards, exp_id=EXP_ID, assessment_id=ASSESSMENT_ID, assessment_version=ASSESSMENT_VERSION, as_of=demo_admin.global_clock(sandbox), current_prefs=prefs_by_learner(sandbox))`; returns `result.to_dict()`. Everything closed in `finally`.
- `POST /api/demo/admin/experiments/assign`: under `demo_backend._submit_lock` call `assign_and_record_eligibility(app, store, list_learner_ids(app), prefs_by_learner, EXP_ID, demo_admin.global_clock(sandbox))`; bump the version; return the summary as JSON-safe data (`newly_assigned`, `already_assigned`, `eligible`, `excluded`).
- `GET /api/demo/admin/experiments/assignments`: one row per learner in the profile: `display_name`, `learner_id`, `variant` (null until assigned) plus `"arm"` (`"treatment"` for `variant_a`, `"control"` for `variant_b`), `eligible` (bool|null), exclusion reason if the eligibility row stores one, and first exposure (`learner.first_experiment_exposure(EXP_ID)`, as JSON-safe data or null). Also return `"assigned_count"` and `"arm_counts"`.
- `POST /api/demo/admin/experiments/sweep {"learner_id": str}`: under `demo_backend._submit_lock` open the app, a `NotificationStore`, the dispatcher via `demo_notify.build_dispatcher(live_dir, store, <clock lambda returning that learner's demo clock from sandbox.get_clock>)` and call `run_streak_risk_sweep(app, dispatcher, [learner_id], prefs_by_learner, now_utc=<learner clock>, experiment_id=EXP_ID, store=<experiment store>)`; bump the version; return the outcomes as `[{"learner_id","reason","fact_status"}]`. Do not call Sandbox submit or clock methods inside this endpoint.
- `GET /api/demo/admin/experiments/report`: `{"report": compute_report(sandbox), "sha256": <sha256 of json.dumps(report, sort_keys=True, separators=(",",":"))>}`.
- `POST /api/demo/admin/experiments/reproduce`: compute the report twice with fresh connections; return `{"hash_1","hash_2","identical": bool, "valid": report["valid"]}`. No state change, no version bump.

STEP 2. Implement the tab in `webapp/static/admin-experiments.js` (replace the stub) plus CSS, as a numbered guided flow:
- Intro card: "Experiment exp_007 compares streak-risk reminders (treatment) with no reminder (control). The primary objective is learning progress (checkpoint mastery), not opens or clicks. Guardrails: notification cap breaches and abuse-gaming share."
- Step 1 card "Assign everyone": button "Assign all learners (deterministic)", then summary tiles (assigned, eligible, excluded) and arm counts. Hint: "Assignment is a hash of learner id and experiment id, so re-running changes nothing."
- Step 2 card "Who got what": table (Learner, Arm badge treatment=info control=warn, Eligible, First exposure time or dash); highlight the selected learner.
- Step 3 card "Trigger the reminder sweep for the selected learner": button "Run streak-risk sweep now"; show the outcome reason in plain words (for example treatment learner at risk: reminder sent; control learner: held out, no reminder; not at risk: skipped). Hint: "Move the clock to the evening of a day the learner has not completed a lesson first."
- Step 4 card "Experiment report": button "Compute report". Render a verdict banner (valid = green, invalid = red with the `invalid_reasons` in plain words), an arms table (Arm, Eligible, Mastery result as the report gives it, Exposure rate, Messages per learner per day, Max delivered in 24 h, Cap breach learners, Opt-outs), SRM checks (assigned and eligible: p-value or verdict as in the report), gaming result (breached or not, share), the contrast block, the report notes, and the hash (first 12 chars) under the card.
- Step 5 card "Reproducibility": button "Compute the report twice", showing both hashes and a green banner "Identical" or red "Different".
- Raw report JSON in a collapsible `.adm-json` block.
- Re-render on `Admin.onLearnerChange` and `Admin.onChange`.

STEP 3. Verify: standard pattern. Smoke: POST assign returns counts that add up to 12; POST assign again returns `newly_assigned` 0; GET assignments returns 12 rows with variants; POST a lesson-complete and a failing checkpoint for a few learners so mastery data exists, then GET report returns a dict with `valid` and `arms`; POST reproduce returns identical true; set Tara Bose's clock to `2026-10-17T20:00:00Z`, POST sweep for her and print the outcome.

---

## Task 9: Recovery tab (M8)

Audit file: `docs/audit/demo-06-d9-recovery-tab.md`

GOAL: The Recovery tab proves M8 live: wipe the leaderboard read model and rebuild it from the event log with a matching checksum, show the notification dead-letter state, run the scale benchmark, and show saved benchmark reports.

STEP 0. Read completely: `webapp/demo_backend.py` (`_project_new_notifications`, `_get_last_offset`, `_set_last_offset`, `create_sandbox`), `webapp/demo_admin.py`, `webapp/views.py` (`_leaderboard`, `_rows`: what happens with an empty leaderboard store), `src/scale_and_recovery/rebuild.py` (`simulate_projection_wipe` and `execute_full_rebuild`: copy their idea), `src/leaderboard/store.py`, `projection.py`, `poller.py`, `scripts/run_scale_benchmark.py` (`run_benchmark`, the exact structure of the dict it returns, and `main`), `src/scale_and_recovery/latency.py` and `outage.py` (only to understand the benchmark sections), and `ls docs/evidence/`.

STEP 1. In `webapp/views.py` make `_leaderboard` (and anything it calls) return an empty leaderboard for the learner (no rows, no error, same JSON shape the home page expects) when the leaderboard store has no entries for the learner's scopes. The home page must keep rendering normally in that state. This is the only edit outside the new files.

STEP 2. Create `webapp/demo_admin_recovery.py` with `create_recovery_router(sandbox)` and include it from `create_admin_router`.
- Helper `leaderboard_fingerprint(db_path) -> {"hash": sha256 hex, "tables": {table: row_count}, "rows": total}`: open the file read-only, for every table in `sqlite_master` (type table, name not starting with `sqlite_`) select all rows ordered by all columns, serialise to JSON with `sort_keys` and hash everything in table-name order. Exclude any table or column that stores only wall-clock write times or replay offsets that legitimately differ after a rebuild (decide by reading `store.py`; list the exclusions in the audit file).
- `POST /api/demo/admin/recovery/wipe-leaderboard`: under `demo_backend._submit_lock`: compute the fingerprint, store its hash with `demo_notify.set_flag(live_dir, "recovery_pre_hash", hash)` (import `webapp.demo_notify`), delete `live/leaderboard.db` and its `-wal` and `-shm` files, create an empty store with `SQLiteLeaderboardStore` (so the schema exists), set the demo projection offset to 0 with `demo_backend._set_last_offset`, bump the version. Return `{"before": fingerprint, "wiped": true}`. Never call Sandbox submit or clock methods here.
- `POST /api/demo/admin/recovery/rebuild-leaderboard`: under `demo_backend._submit_lock`: open the app, open the leaderboard store, create `LeaderboardProjection`, time with `perf_counter` a replay of every notification from offset 1 (`read_all_notifications(app, start=1)` then `projection.apply(n, offset=n.id)` for each), set the demo offset to the last notification id, compute the fingerprint, compare with the flag `recovery_pre_hash` (when the flag is absent compare with None and report identical false), bump the version. Return `{"events_replayed","duration_ms","events_per_sec","after": fingerprint,"before_hash","identical": bool}`.
- `POST /api/demo/admin/recovery/wipe-and-rebuild`: do both steps above in sequence and return `{"wipe": ..., "rebuild": ...}` (call the two helper functions directly; do not call HTTP routes from inside).
- `GET /api/demo/admin/recovery/dead-letters`: read-only counts from the notifications store: `{"open": n, "resolved": n, "entries": [last 10 with learner display name, channel, reason, attempts, failed_at, resolved]}`.
- `POST /api/demo/admin/recovery/benchmark {"profile": "small"}`: load `scripts/run_scale_benchmark.py` with `importlib.util.spec_from_file_location` (add the repo root to `sys.path` first, as the script does), call `run_benchmark(profile_name="small", seed=42)` and return its dict. Reject any profile other than `"small"` with HTTP 422 (large profiles are shown from saved reports). No version bump.
- `GET /api/demo/admin/recovery/reports`: list files in `docs/evidence/` whose names start with `scale` and end with `.json` as `[{"name","size","modified"}]`; `GET /api/demo/admin/recovery/reports/{name}`: return that file's JSON only when `name` is in the listing (never build a path from user input beyond matching it against the listing).

STEP 3. Implement the tab in `webapp/static/admin-recovery.js` (replace the stub) plus CSS:
- Card "Rebuild from the event log": explanation "The leaderboard is a derived read model. Delete it and replay the event log: the checksum must match." Three buttons: "1. Wipe leaderboard", "2. Rebuild from event log", "Wipe and rebuild". After each, show fingerprint tiles (rows, checksum first 12 chars), events replayed, duration and events per second, and a green banner "Checksum identical before and after" or red "Mismatch". Hint: "Open the Learner home pane after step 1: the leaderboard is empty. After step 2 it is back."
- Card "Notification recovery": tiles Open dead letters and Resolved, a short table of the last entries, and a link "Open Notifications tab" (switches tab via `location.hash` and the shell's tab switch). Hint describing the outage demo flow.
- Card "Scale benchmark": button "Run small benchmark live" showing "Running..." while it runs and then a readable results view built from the structure of the report: for every top-level section a sub-card with its key numbers (generic renderer: nested objects become labelled rows, numbers formatted with thousand separators and 2 decimals, booleans as badges) and a big badge for the overall passed flag. Below it a select of saved reports from the reports endpoint with a button "Show saved report" rendering it with the same renderer (card named with the file name and modified time).
- Re-render the dead-letter card on `Admin.onChange`.

STEP 4. Verify: standard pattern. Smoke: send one lesson for Priya, GET her `/api/learners/<id>/home` to confirm leaderboard rows exist, POST wipe-leaderboard, GET home again and confirm 200 with empty leaderboard rows, POST rebuild-leaderboard and confirm identical true, GET home and confirm rows are back; POST wipe-and-rebuild confirms identical true; POST benchmark with profile `small` returns a dict containing an overall pass flag (print how long it took).

---

## Task 10: Engine tick button

Audit file: `docs/audit/demo-06-d10-engine-tick.md`

GOAL: One button in the Admin header runs the engine's scheduled jobs (weekly cohort cycle, experiment assignment, leaderboard poll, quest expiry, remediation sweep, streak-risk reminders, abuse pass, leaderboard reconciliation) once on the sandbox data and shows what each step did.

STEP 0. Read completely: `scripts/run_engine_tick.py` (`run_tick`, every `_*_step` function, `STEP_NAMES`, the structure of the dict it returns), `webapp/demo_admin.py`, `webapp/demo_notify.py`, `webapp/demo_backend.py` (`_project_new_notifications`, `_get_last_offset`, `_set_last_offset`), `src/leaderboard/projection.py` (is `apply` idempotent per offset), `src/scale_and_recovery/rebuild.py` (`SYNTHETIC_MIN_COHORT_SIZE`).

STEP 1. Create `webapp/demo_admin_tick.py` with `create_tick_router(sandbox)` and include it from `create_admin_router`.
- Load `run_tick` from `scripts/run_engine_tick.py` with `importlib.util.spec_from_file_location` (add the repository root to `sys.path` first, as that script does).
- `POST /api/demo/admin/tick {"now": ISO string | null}`: `now` = the given instant (must be timezone-aware, otherwise HTTP 422) or `demo_admin.global_clock(sandbox)`. Under `demo_backend._submit_lock` (never calling Sandbox submit or clock methods inside) open: the `LearnerApplication` on `live/events.db`, `SQLiteLeaderboardStore(live/leaderboard.db)` wrapped in `LeaderboardProjection`, `NotificationStore(live/notifications.db)` with the dispatcher from `demo_notify.build_dispatcher(live_dir, store, lambda: now)`, `EvidenceStore(live/evidence.db)`, `AbuseSignalStore(live/abuse.db)`, `ExperimentStore(live/experiments.db)`; `prefs_by_learner = {learner_id: demo_notify.prefs_for(live_dir, learner_id) for every learner in views.load_profile}`. Call `run_tick(application, projection, dispatcher, evidence, signal_store, now=now, prefs_by_learner=prefs_by_learner, experiment_store=experiment_store, cohort_min_size=SYNTHETIC_MIN_COHORT_SIZE, cohort_max_size=5000)`. Close everything in `finally`. Afterwards call `demo_backend._project_new_notifications(app, live_dir / "leaderboard.db", live_dir / "demo_state.db")` on a fresh app so the demo's own projection offset catches up with anything the tick wrote, then `demo_backend._bump_version(sandbox.root)`.
- Return `{"now": ISO, "summary": result["summary"], "steps": [{"name", "ok": bool (no "error" key), "headline": <short human sentence built from the numbers in that step's dict>, "raw": <the step dict>}, ...in STEP_NAMES order]}`. Write the headline per step yourself from what each step returns (for example "3 reminders sent, 4 held out" for streak_risk). Make all data JSON-safe.

STEP 2. Admin header UI: in `webapp/static/admin.html` add a button "Run engine tick" in the control bar and an empty `<dialog id="adm-tick-dialog">`. In `webapp/static/admin.js` wire the button to `POST /api/demo/admin/tick` with `{"now": null}`, show a disabled "Running..." state while it runs, then fill the dialog: a title with the tick time, three tiles from summary (New decisions, New signals, New notifications), a table of the eight steps (Step name, status badge ok/bad, headline), a collapsible raw JSON per step in `.adm-json`, and a Close button. Add the needed CSS to `admin.css`. Errors go to `Admin.toast`.

STEP 3. Verify: standard pattern. Smoke: POST `/api/demo/admin/tick` with `{}` and print the eight step names, their ok flags and headlines; POST it a second time and print the summary to show decisions and signals are idempotent; then GET Priya's home page to confirm it still returns 200.

---

## Task 11: Smoke script, start script, run-of-show document

Audit file: `docs/audit/demo-06-d11-demo-script.md`

Scope exception for this task only: you may create two new files `scripts/demo_smoke.py` and `scripts/start_demo.sh`, plus files under `docs/demo/`. Everything else in the Global rules still applies.

GOAL: Prove the whole demo end to end with one script, and produce a click-by-click run-of-show document for the recorded demo video whose expected results come from real output.

STEP 0. Read completely: `webapp/demo_personas.py`, `webapp/demo_admin.py` and every `webapp/demo_admin_*.py` file, `webapp/demo_routes.py`, `webapp/demo_backend.py`, `webapp/static/admin.html`, `lesson.html`, `demo.html` (to describe the right buttons), and `docs/audit/demo-06-d*.md` (reuse the smoke checks already worked out).

STEP 1. Create `scripts/demo_smoke.py`:
- Sets `os.environ["DEMO_MODE"]="1"` and `DEMO_SANDBOX_PATH` to a new temp directory BEFORE importing `webapp.app`, adds the repository root to `sys.path`, then uses `fastapi.testclient.TestClient(app)`.
- Walks the demo in this order, one function per milestone, printing one line per check `PASS <name>` or `FAIL <name>: <detail>` and finally a count; exit code 1 if any check failed, 0 otherwise. Persona ids are in `webapp/demo_data/ui_profile.json` (map by display name).
  - Intro: personas endpoint returns 12; Priya home returns 200; lesson-complete for Priya increases XP and the streak behaves per the engine.
  - M1/M2 (Ishaan Gupta): events endpoint shows ids; fresh lesson accepted; duplicate rejected with no XP change; late 24 h accepted; late 100 h rejected `outside_late_window`; rebuild-compare identical; ruleset switch affects only new events.
  - M3 (Priya): cohorts endpoint, consistency lag, latency probe, measure-update.
  - M4 (Tara Bose, plus Vikram Rao for the pending-remediation story): fail checkpoint, decision recorded, second fail follows the guardrail, complete remediation.
  - M5 (Arjun Reddy, Ananya Das, Diya Menon): in-app send, dedup, daily cap, consent suppression for a push-opted-out learner, outage then dead letter then replay, quiet-hours deferral then release (use clock changes).
  - M6 (Kabir Singh and Meera Pillai): four attacks, scan, four signal types present, `scores_changed` false.
  - M7 (Tara Bose): assign, assignments, sweep, report, reproduce identical.
  - M8: wipe-and-rebuild identical, dead-letter counts, small benchmark passed flag.
  - Tick: engine tick returns eight steps.
- Records, for every check, the key numbers it saw (for example "XP 120 -> 140", "reason outside_late_window", "p95 rank 0.4 ms") in a dict, and at the end writes `docs/demo/smoke_results.json` and `docs/demo/smoke_output.txt` (create `docs/demo/`).

STEP 2. Run the script once with `python scripts/demo_smoke.py`. Keep the exact output in the audit file.

STEP 3. Update `webapp/demo_personas.py` so every persona's role and blurb describes exactly what that learner really shows in the golden seed and in the smoke run (read the numbers from `docs/demo/smoke_results.json` and the seeded snapshots). Keep the keys and function names unchanged.

STEP 4. Create `scripts/start_demo.sh` (bash, executable: `chmod +x`): `cd` to the repository root; accept an optional `--fresh` flag that deletes the sandbox directory first; use `DEMO_SANDBOX_PATH=${DEMO_SANDBOX_PATH:-/tmp/gamification-demo-sandbox}`; `export DEMO_MODE=1`; run `uvicorn webapp.app:app --port 8000`; first echo the URLs `http://localhost:8000/demo.html` and `http://localhost:8000/admin.html`.

STEP 5. Create `docs/demo/DEMO_SCRIPT.md`, a run-of-show for a recorded demo of about 10 minutes:
- Section "Start": exact commands (`scripts/start_demo.sh --fresh`), the two URLs, the recommended browser window layout (`demo.html` on the left half and `admin.html` on the right half), and a note on Reset demo between takes.
- Section "Run of show": a table with columns Step, Where, Learner, Click, Say (one sentence tying it to the milestone claim), Expected result (taken from `docs/demo/smoke_results.json`, not invented). One group of rows per milestone M1 to M8 in order, preceded by an intro group (lesson on the lesson page, home page updates live with confetti) and followed by the engine tick and a closing line mapping the demo to the project's "Done when" statement: a synthetic population generates learning events, earns correct replayable rewards, enters cohorts and leaderboards, receives policy-compliant notifications, participates in controlled experiments, with abuse and failure cases handled and measurable learning-oriented outcomes reported.
- Section "Recovery between takes": reset steps, and which clock changes each section leaves behind (for example set the clock back with the admin bar buttons or press Reset demo).
- Section "If something looks different on the day": list the three files that define behaviour of personas and attacks (`webapp/demo_personas.py`, `webapp/demo_admin_abuse.py`, `webapp/demo_admin_lab.py`) and the command to re-run the smoke check.

STEP 6. Final verification, recorded in the audit file: run `python -m py_compile` on `scripts/demo_smoke.py` and every `webapp/*.py` file, `node --check` on every `webapp/static/*.js` file, `bash -n scripts/start_demo.sh`, and re-run `python scripts/demo_smoke.py` one last time, pasting the final output.

---

## Completion

When all 11 boxes in `docs/audit/demo-06-progress.md` are ticked, write a short final summary at the bottom of that file: the list of files created or changed, the one existing code path that was rewired (`submit_checkpoint` now uses `demo_notify.build_dispatcher`), and the final smoke result. Then end your turn.

**Reminder: no git commands that change anything. The owner handles git.**
