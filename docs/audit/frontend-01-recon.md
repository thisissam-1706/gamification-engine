# Frontend 01: Read-only recon for a learner home page

Status: recon only. No code or tests were created or edited. The only file written in the repo is this one.
Scratch probes (outside the repo) lived in the session scratchpad; they built a throwaway 6-learner DB there.

## Changes
- Added `docs/audit/frontend-01-recon.md` (this file). Nothing else.

## Before / after
- Before: no `docs/audit/frontend-01-recon.md`. After: this file. `git status --short` was clean before my probes and clean-except-this-file after (probes ran with `PYTHONPATH=.` using `.venv/bin/python`, writing only to the scratchpad).

## Files read
`src/state_machine/{learner,projection,rules,quest_definitions}.py`, `rulesets.json`, `rulesets_decay.json`;
`src/leaderboard/{service,models,__init__,cohorts,projection}.py` and `store.py` (read methods + schema);
`src/notifications/{store,models,queue,__init__}.py`, `channels/in_app.py`, `dispatcher.py` (grep only);
`src/interventions/{service,config}.py`; `src/ingestion/service.py` (head + entry points), `evidence.py` (head);
`simulator/{pilot,generator,run_pilot}.py`; `src/scale_and_recovery/{workload,rebuild}.py`; `pyproject.toml`; README grep.
I did not read `abuse/`, `experiments/`, or `reconciliation.py` in full.

## Headline findings
1. There is **no UI-oriented read facade**. The pieces exist as (a) the `Learner` aggregate (full state in memory after `repository.get`), (b) pure projections in `projection.py`, (c) `LeaderboardService`, (d) raw SQLite tables for notifications.
2. **`simulator/pilot.py` and `simulator/generator.py` cannot seed a home-page dataset.** They emit experiment streams (assignments, deliveries, checkpoint attempts, a single `completions` row per learner). They produce no lesson completions, XP, streaks, quests or badges, and never touch `Learner`. `generator.generate_baseline_stream` raises `NotImplementedError`. The usable seeding path is `src/scale_and_recovery/workload.py` + `rebuild.py:FullStateRebuildHarness` (see (f)).
3. **No level concept, no display names** (answers a, b below).
4. **The library openers are not read-only.** `SQLiteLeaderboardStore.__init__` and `NotificationStore.__init__` run `PRAGMA journal_mode=WAL` and `CREATE TABLE IF NOT EXISTS` on open, so they fail on a read-only connection. Workarounds verified below.

## Table: UI need | existing function (file:name) | gap / derivation needed

| UI need | Existing function (file:name) | Gap / derivation |
|---|---|---|
| XP total | `Learner.xp_total` via `LearnerApplication.repository.get(UUID)` (`src/state_machine/learner.py:71`). Replay alternative: `rebuild_learner_state(events).xp_total` (`projection.py:271`). | Needs learner UUID. No bulk "all learners" API; enumerate with `SELECT DISTINCT originator_id FROM stored_events` (verified: 6 rows for 6 learners). Weekly-epoch XP is in the leaderboard store (`get_entry("global","global",epoch,id).score`), which differs from lifetime XP. |
| Level | NOT AVAILABLE | See (a). Derive from `xp_total` with a UI-owned threshold table. Must be labelled as a UI derivation, not an engine fact. |
| XP to next level | NOT AVAILABLE | Derived from the UI-owned level table: `next_threshold - xp_total`. |
| Current streak | `Learner.new_streak` (`learner.py:76`); `Learner.current_streak` is a compat alias. Projection alternative: `LearnerStreakProjection.streak_summaries[id].new_streak` (`projection.py:79`). | None for the number. **Caveat:** `new_streak` is the streak as of the learner's *last activity date*, not as of "today". A learner who stopped 5 days ago still shows their old streak; the engine does not expire it. UI must compare `last_activity_date` to the as-of date. |
| Freeze balance | `Learner.freezes_remaining` (`learner.py:77`); `LearnerStreakSummary.freezes_remaining` (`projection.py:40`); `fold_streak_and_freezes(streak_events, freeze_events)` (`projection.py:53`). | None. |
| Last 7 days completed/frozen | Partial. `Learner.activity_dates: set[str]` (ISO dates a lesson was completed); `Learner.streak_outcomes[date] = (new_streak, remaining, consumed, previous_streak, id, supersedes)` (`learner.py:82`); `Learner.freeze_acquisitions: dict[freeze_id, date]`. Pure helper `calculate_streak(activity_dates, freezes)` (`rules.py:279`). | See (d). Completed = date in `activity_dates`. Frozen = a gap date covered by a freeze; the engine records only `freezes_consumed` count on the *next active* date's outcome (`consumed = missing_days`), not which calendar dates were covered. Derive: for an active date D with `consumed = k` and previous active date P, the k days directly before D (after P) are the frozen days. Treat as derived, not stored. |
| Active quests (weekly + remediation): progress, days left | `Learner.quest_instances: dict[assignment_id, {assignment_id, quest_id, definition_version, status, assigned_at, deadline_at, completed_at, progress}]` (`learner.py:90`). Read-model alternative: `LearnerQuestProjection.get_summary(learner_id, as_of=None)` returning `LearnerQuestSummary` / `AssignmentRecord` (`projection.py:162,210`). Target: `quest_definition_for(quest_id, definition_version).target` (`quest_definitions.py:31`). | (1) Target is not stored on the instance; look it up via `quest_definition_for`. `lesson_starter_v1` target 3 (rulesets.json `v1`); `remediation_v1` target 1 (`quest_definitions.py`). (2) Days left = `deadline_at - as_of`; derive. (3) Pass `as_of` to `get_summary` or the instance dict will still say "assigned" after the deadline until `expire_due_quests` runs. (4) **No code assigns the weekly quest automatically.** `rules.QUEST_DEFINITIONS` lists `lesson_starter_v1`, but only `Learner.assign_quest` creates it, and nothing in `src/` calls it for that quest. A seeded dataset must call `assign_quest` itself. Remediation quest is assigned by `run_remediation` (`interventions/service.py:27`) with `assignment_id=f"remediation_quest:{attempt_id}"`. (5) The "weekly" label is a UI convention: default deadline is `assigned_at + 7 days` (`learner.py:521`). |
| Earned badges | `Learner.badges_issued: dict[badge_id, award_id]` (`learner.py:91`); `LearnerReplayState.badges_issued` (`projection.py:262`). | Earned timestamp is only in the `BadgeAwarded` event (`awarded_at`), not in aggregate state. Read events via `app.events.get(uid)` if needed. |
| Locked badges | NOT AVAILABLE as a catalogue. | Only one badge exists: `week_warrior_v1`, threshold 7-day streak, from `RULESETS["v1"]` (`rules.py`, `badge_id`, `badge_threshold`). Derive locked = `{ruleset.badge_id} - badges_issued`; progress = best streak / `badge_threshold`. Names/descriptions/icons must be UI-owned. |
| Cohort leaderboard rows (rank, name, XP) | `LeaderboardService.get_top_k("cohort", cohort_id, epoch, k)` and `get_surrounding("cohort", cohort_id, epoch, subject_id, window)` (`leaderboard/service.py:15,25`) returning `RankedLearner(rank, subject_id, score, first_reached_at, scope_type, scope_id, epoch)`. | No display name (see b). Score is **epoch (ISO-week) XP**, not lifetime XP. `subject_id` is the learner aggregate UUID string (verified in probe), not the simulator id like `learner_0000`. |
| Global leaderboard rows | Same, with `("global", "global", epoch, ...)`. Own rank: `get_learner_rank(scope_type, scope_id, epoch, subject_id)` (`service.py:38`). | Same as above. |
| In-app notifications | Partial. Persisted: table `notification_dispatches(dispatch_id, proposal_id, learner_id, channel, category, dispatched_at, payload_json)` in `NotificationStore` (`notifications/store.py:40`). Related: `notification_suppressions`, `notification_dlq`, `notification_rescheduled`. | See (e). No per-learner list reader exists. `NotificationStore.get_dispatch_history_since(learner_id, since_iso, channel)` returns **timestamps only**, no payload/category. UI needs a direct `SELECT ... FROM notification_dispatches WHERE learner_id=? AND channel='in_app' ORDER BY dispatched_at DESC`. No read/unread flag exists. |
| Display name | NOT AVAILABLE | See (b). |
| Learner id used for lookups | Learner UUID (`Learner.id`) | Leaderboard `subject_id` = `str(learner.id)`. The mapping from a human id (`learner_0000`) to UUID is **not persisted**; it exists only in the in-memory return of `ingest_workload` (`rebuild.py:ingest_workload`). |

## Specific questions

**(a) Does the engine have a "level" concept?**
No. `grep -rniE "\blevel"` over `src/` and `simulator/` matches only a docstring. No level thresholds, no level field in `Learner`, rulesets, projections or the leaderboard. Only XP exists (`xp_total`), plus rule-driven badge/quest thresholds. A level and "XP to next level" would have to be a UI-side pure function of `xp_total` with a table the UI defines (assumption: this is acceptable for the demo; it should be shown as a presentation rule, not an engine fact).

**(b) Display names or only IDs?**
Only IDs. No `display_name`/`username`/`nickname` anywhere in `src/`, `simulator/`, `tests/` or `docs/` (grep returned nothing). Learner identity is the aggregate UUID; the leaderboard `subject_id` is that UUID string; simulator/workload ids look like `learner_0000` / `L00000`. For a UI, names must be supplied by a UI-owned lookup (e.g. a JSON file mapping UUID to name) or generated deterministically from the id. `docs/problem-boundaries`-style fairness language (cohorts.py: "strictly excluding demographic or sensitive profile attributes") suggests keeping names out of the engine on purpose; I did not check that doc.

**(c) How is a cohort chosen, and how do I get the global ranking?**
- Chosen by the caller, then recorded as a fact: `Learner.assign_cohort(cohort_id, membership_epoch, ...)` stores `Learner.cohort_memberships[epoch] = {cohort_id, membership_epoch, grouping_rule_version, assigned_at}` (`learner.py:607`). The id itself comes from `assign_activity_volume_cohort(subject_id, epoch, prior_week_lessons, num_cohorts, salt)` (tier casual <3 / regular <10 / intensive, plus hash bucket; id looks like `cohort_act_2026-W42_regular_003`) or `assign_module_cohort(subject_id, epoch, module_id, ...)` in `leaderboard/cohorts.py:67,92`. The synthetic harness skips both and assigns `cohort_a`/`cohort_b` round-robin (`workload.py`).
- Lookup for one learner: either `learner.cohort_memberships[epoch]["cohort_id"]` (aggregate) or `SQLiteLeaderboardStore.get_member_cohort(epoch, subject_id)` (read model; verified returns `cohort_a`). `get_member_cohorts` returns a list if there is more than one.
- Cohort definitions have `min_size=500`, `max_size=5000` defaults (`models.py`); a tiny demo cohort will sit in `forming`/auto-created status, which does not block reads.
- Global ranking: `LeaderboardService.get_top_k("global", "global", epoch, k)`; the scope id for global is the literal string `"global"` (`projection.py` uses `get_entry("global","global",epoch,id)`). Own rank: `get_learner_rank("global","global",epoch,subject_id)` returns `None` if the learner has no row.
- Every leaderboard call **requires an explicit epoch** string like `2026-W42` (`get_calendar_week(ts)` produces it). The projection refuses to guess an epoch.

**(d) Per-day completion record usable for a 7-day strip?**
Yes, derivable. `Learner.activity_dates` is a set of local ISO dates (local to the learner's timezone, computed at completion time by `local_activity_date`). `Learner.streak_outcomes[date]` has per-date `(streak, freezes_remaining, freezes_consumed, ...)`. The replay equivalent is `LearnerReplayState.activity_dates` (a sorted tuple). Gaps: (1) frozen days are not stored as dates (see table); (2) a date appears in `activity_dates` only if the completion carried an `activity_date` (or `occurred_at`+`timezone`); a completion with neither does not mark a day; (3) the window is `[as_of_date-6, as_of_date]` and the UI must choose the timezone for "as_of_date" (the aggregate's `timezone_history[-1]["timezone"]` is the latest known).

**(e) Where are in-app notifications stored and how are they read?**
- Written by `NotificationDispatcher.dispatch(...)`, whose `ChannelType.IN_APP` adapter is `InAppChannelAdapter` (`channels/in_app.py`). That adapter only appends to an **in-memory list** `delivered_messages`, which dies with the process. The durable trace is the `notification_dispatches` row written via `NotificationStore.record_dispatch` (payload as JSON).
- Read: no per-learner reader returns rows. Options: raw SQL on `notification_dispatches` (columns above), or `get_dispatch_history_since` for timestamps only. Suppressed ones: `get_suppression_entries(proposal_id=None)` (all rows, filter in Python). DLQ: `get_dlq_entries(resolved=None)`.
- **Nothing in the engine produces notification proposals from learner events.** `NotificationProposal(` is constructed only in `dispatcher.py` (replay/release paths) and `scale_and_recovery/outage.py`. Streak-risk / remediation / quest messages are not generated by any component I read. A demo must build proposals itself and call the dispatcher, or insert rows. Payload shape is free-form (`Mapping[str, Any]`), so the UI cannot assume a `title`/`body` field. Category enum: `streak_risk, remediation, quest_update, social_nudge, general`.
- The probe DB had **no notifications DB at all**; the harness does not create one.

**(f) Seeding a small demo dataset, and the fixed "as of"**
- Simulator/pilot: not suitable (finding 2).
- Suitable: `generate_synthetic_workload(WorkloadProfile(...))` (`scale_and_recovery/workload.py`) + `FullStateRebuildHarness(events_db, leaderboard_db, epoch).initialize_live_system(); ingest_workload(workload); close()`. This drives real `Learner` aggregates (`assign_cohort`, `complete_lesson`) and the real `LeaderboardProjection.apply` over the notification log. Verified end to end with `population_size=6, cohort_count=2, events_per_learner=10, seed=42, epoch="2026-W42"`: it created `events.db` (one table, `stored_events`) and `lb.db` (8 tables), 203 leaderboard watermark.
- What this seeding does NOT give: freezes (none acquired; `freezes=0`), quests (none assigned), badges (none, streaks max 5 < 7), checkpoint attempts/remediation, notifications, UUID-to-name mapping, a notification DB. A UI demo needs a **separate seeding script** that, after the harness, calls `Learner.acquire_freeze`, `assign_quest("lesson_starter_v1", assigned_at=...)`, ideally `record_checkpoint_attempt` + `run_remediation`, and builds notifications. (All via public aggregate/service methods; I did not write or run that.)
- Workload timing is deterministic from `base_start_iso="2026-10-12T08:00:00Z"` (Monday of ISO week 2026-W42). In the probe, events ran `2026-10-12T12:00:00Z` to `2026-10-17T11:00:00Z` for 6 learners x 10 events; the first learner had streak 5 over `2026-10-12..16`. A longer `events_per_learner` extends the span (each learner advances 4-20h per event).
- **Recommended fixed "as of" (assumption):** the UI must never call the wall clock (the engine forbids it: `reassign_cohort` raises "guessing with datetime.now is prohibited", `expire_due_quests` requires tz-aware `as_of`, `rehydrate` requires explicit epoch). Use the end of the seeded span, e.g. `2026-10-18T00:00:00Z` for the probe parameters (end of `2026-W42`, the day after the last event), or compute it as `max(occurred_at over the workload) rounded up to the next day 00:00Z`. Keep it a single constant shared by all reads, and pass the same value to `get_summary(as_of=...)` and to the 7-day window.
- Gotcha: the real-clock late window. `ingest_lesson_completed` rejects events older than 72h versus the clock (`IngestConfig.late_window`, `tests/ingestion/support.py` docstring). The harness bypasses ingestion (calls `complete_lesson` directly), so seeded 2026-10 dates are fine; going through `ingest_*` needs `late_window=None` or an injected `clock`.

## Item 4: opening the SQLite stores read-only from a separate process

Setup: the repo is a plain package (`pyproject.toml` `[tool.setuptools.packages.find] include = ["src*"]`), and `src` is not importable unless the repo root is on `sys.path` or the package is installed. In my probe `import src` failed until I used `PYTHONPATH=.`. Python in `.venv` is 3.14.6 (requires >=3.11), `eventsourcing==9.5.5`, `jsonschema>=4.0`.

Imports:
```python
import sqlite3
from uuid import UUID
from src.state_machine.learner import Learner, LearnerApplication
from src.state_machine.projection import (
    rebuild_learner_state, LearnerQuestProjection, LearnerStreakProjection, fold_streak_and_freezes,
)
from src.state_machine.quest_definitions import quest_definition_for
from src.state_machine.rules import get_ruleset, calculate_streak, local_activity_date
from src.leaderboard.store import SQLiteLeaderboardStore
from src.leaderboard.service import LeaderboardService
from src.leaderboard.cohorts import get_calendar_week
from src.notifications.store import NotificationStore   # do NOT instantiate read-only; see below
```

Event store (learner state), verified working and verified write-blocked:
```python
app = LearnerApplication(env={
    "PERSISTENCE_MODULE": "eventsourcing.sqlite",
    "SQLITE_DBNAME": f"file:{path}/events.db?mode=ro&uri=true",
})
learner = app.repository.get(UUID(learner_id))       # full Learner state
```
Result: reads work (`xp 1100`, `streak_outcomes` per date, `completed_lesson_count 11`). A deliberate `complete_lesson` + `save` raised `OperationalError: attempt to write a readonly database`. A second variant using the plain path plus `"CREATE_TABLE": "n"` also opened and read, but it is a read-write connection, so it is not enforced read-only; prefer the `mode=ro` URI.

Leaderboard store: `SQLiteLeaderboardStore(f"file:{path}/lb.db?mode=ro")` **fails** (`unable to open database file`: it connects without `uri=True`, then runs `PRAGMA journal_mode=WAL` and `CREATE TABLE`). Working approach (verified): build the connection yourself and attach it to an uninitialised instance:
```python
conn = sqlite3.connect(f"file:{path}/lb.db?mode=ro", uri=True)
store = SQLiteLeaderboardStore.__new__(SQLiteLeaderboardStore)
store.connection = conn
store._transaction_depth = 0
service = LeaderboardService(store)
```
This skips `__init__` (so no schema creation/migration) and relies on private attributes; it is a hack that breaks if `__init__` gains more fields. Read methods verified through it: `get_all_epochs`, `get_top_k` (global and cohort), `get_member_cohort`, `get_watermark`. Cleaner alternative for the UI: query `leaderboard_entries` directly with its documented ordering `score DESC, first_reached_at ASC, subject_id ASC`.

Notification store: `NotificationStore(db)` has the same problem (WAL pragma + `CREATE TABLE` on open) and, unlike the leaderboard, there is no per-learner reader anyway, so use `sqlite3.connect("file:...?mode=ro", uri=True)` and SQL directly. I did not run this one because no notification DB exists in the probe.

Other read-only facts:
- Both DBs run in WAL mode. A `mode=ro` reader needs the `-shm` file to exist or be creatable by the directory permissions; it worked in the probe with the writer closed. Concurrent writer plus read-only reader: not tested (uncertainty).
- Do not open the same DB read-write from two connections casually; the app default busy timeout is 30s (`evidence.py` `BUSY_TIMEOUT_SECONDS`, leaderboard `busy_timeout=30000`).
- Learner ids for listing: `SELECT DISTINCT originator_id FROM stored_events` returns **hex without dashes** (e.g. `c11e6d7b718648a891dcb8a1d2638566`); `UUID(...)` accepts it, but the leaderboard `subject_id` is the dashed form `str(uuid)`. Convert before ranking lookups.
- `stored_events` is the only table in the event DB (events, topic strings like `src.state_machine.learner:Learner.LessonCompleted`, state BLOB). The ingestion evidence store is a separate DB (`EvidenceStore`), not needed for the home page.

## Tests
None added or run (repo test suite not touched). Verification here is empirical probes only:
- Probe 1: seeded a 6-learner dataset via `FullStateRebuildHarness` into the scratchpad. Output: `learners [('learner_0000', UUID('c11e6d7b-...')), ...]`, `last event 2026-10-17T11:00:00Z first 2026-10-12T12:00:00Z`, `xp 1100 streak 5 freezes 0 dates [2026-10-12 .. 2026-10-16] badges {} cohort {'2026-W42': {'cohort_id': 'cohort_a', ...}}`, tables `events.db ['stored_events']`, `lb.db [8 leaderboard_* tables]`.
- Probe 2: `SQLiteLeaderboardStore(ro uri)` -> `FAIL OperationalError unable to open database file`; raw-connection attach -> `epochs ['2026-W42']`, top-3 global with `score=1100`, `cohorts of top1 cohort_a`, `watermark 203`; `LearnerApplication` ro URI -> `opened`, `log ok [1]`.
- Probe 3: aggregate read OK; write attempt -> `write blocked: OperationalError attempt to write a readonly database`; `count(distinct originator_id) = 6`.
- The commands were `PYTHONPATH=. .venv/bin/python <scratchpad>/probe{,2,3}.py <scratchpad>/demo`.

## Assumptions
- "Today" for the UI is a single fixed `as_of` constant, not the wall clock (justified by the engine's own rules above).
- Level/XP-to-next-level, badge catalogue text, and display names are UI-owned presentation data.
- Leaderboard "XP" means epoch XP (weekly), matching what the engine ranks, not lifetime XP; the home page may want to label both.
- "Weekly quest" means `lesson_starter_v1` (target 3, 7-day default deadline); "remediation quest" means `remediation_v1` (target 1).

## Uncertainty / not verified
- Whether `Learner.freezes_remaining` equals the UI-relevant "balance" when a freeze is acquired after the last activity: `_update_streak` recomputes it from `freeze_acquisitions` minus consumed, but `acquire_freeze` increments independently; I read both and did not run a scenario.
- Frozen-day derivation is my reading of `calculate_streak` and `consumed` semantics; not executed against a freeze scenario.
- Quest and remediation read paths were read, not run (no quests existed in the probe data).
- Did not verify the `notification_dispatches` read via SQL or the dispatcher's in-app flow end to end; schema from source only.
- Did not read `reconciliation.py`, `abuse/`, `experiments/` or `docs/*` beyond a README grep, so I can't rule out an existing helper I missed there. Grep for `display_name|nickname|username|full_name` and `\blevel` was repo-wide over `src`, `simulator`, `tests`, `docs` (names) and `src`, `simulator` (level).
- Concurrent writer plus read-only reader on WAL files was not tested.
- Python 3.14 in `.venv` vs CI version not checked.
