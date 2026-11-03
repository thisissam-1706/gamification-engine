# Frontend 02: Read-only JSON API for the learner home page

Builds on [frontend-01-recon.md](frontend-01-recon.md). No tests written or modified. Nothing under `src/` or `tests/`, `requirements-lock.txt` or `.github/` was touched. No git write commands were run.

## Changes
| File | Change |
|---|---|
| `pyproject.toml` | Added only `[project.optional-dependencies] web = ["fastapi", "uvicorn"]` (4 lines, after `dev`). Nothing else changed. |
| `webapp/__init__.py` | New, docstring only. |
| `webapp/seed.py` | New. `seed()` builds the demo dataset into `webapp/demo_data/`. Exposes `AS_OF`, `EPOCH`, `learner_uuid(i)`. `python -m webapp.seed` runs it. |
| `webapp/views.py` | New. `build_home(learner_id, as_of)`, `list_learners()`. Read-only. |
| `webapp/app.py` | New. FastAPI app, 2 GET endpoints, localhost-only CORS. |
| `webapp/demo_data/` | Generated: `events.db`, `leaderboard.db`, `notifications.db`, `ui_profile.json` (+ `-wal`/`-shm` once opened). |

Before: no `webapp/`, no `web` extra. After: as above. `git diff --stat`: `pyproject.toml | 4 ++++`.

Environment change (not in the repo): `.venv/bin/pip install fastapi uvicorn` (fastapi 0.162.0, uvicorn 0.54.0, starlette 1.10.0, pydantic 2.14.2) because neither was installed.

`.gitignore` was **not** edited: it ignores no data folders (only caches, venvs, build output, `*.pdf`, `reviews/`, `ai_documented_changes/`). So `webapp/demo_data/` currently shows as untracked; add it yourself if you do not want to commit it.

## Design
- **Seeding.** The simulator/pilot code cannot produce lessons, XP or streaks (recon finding 2), so `seed.py` uses `generate_synthetic_workload` (12 learners, 2 cohorts, seed 42, 10 events each) and drives real `Learner` aggregates through public methods, then `LeaderboardProjection.apply` over the notification log (same as the AT-15 harness) and `NotificationDispatcher` for in-app notifications. Learner ids are `uuid5(fixed namespace, "learner_NNNN")`, created with `Learner._create(Learner.Created, id=...)`, so ids are stable across reseeds.
- **Scripted overlays** (the workload alone gives 5 straight days for everyone): learner 1 has a freeze and no activity on 10-14 (frozen day); learner 2 has no freeze and no activity on 10-15 (missed day, streak resets); learner 0 gets lessons on 10-10 and 10-11 (7-day streak, badge); learners 3 and 4 fail a checkpoint on 10-15 and `run_remediation` assigns the remediation quest (3 then completes it). Weekly quest `lesson_starter_v1` is assigned to everyone on 2026-10-15. Workload events after the as-of instant are dropped.
- **As-of:** `AS_OF = "2026-10-16T18:00:00Z"` (Friday of ISO week 2026-W42, after the last seeded event). This differs from the `2026-10-18T00:00:00Z` the recon suggested, because the week strip needs a "today" with future days after it.
- **Read-only.** Event store opened with `file:...?mode=ro&uri=true` (writes fail with "attempt to write a readonly database", verified in recon). Leaderboard and notifications via `sqlite3.connect(..., uri=True)` with `mode=ro`. Leaderboard store uses the recon's `__new__` workaround because `SQLiteLeaderboardStore.__init__` creates tables. Connections are opened and closed per request. SHA-256 of all three `.db` files was identical before and after the curl run.
- **Engine reads used:** `LearnerApplication.repository.get` / `events.get`, `LearnerQuestProjection.rebuild/get_summary(as_of=)`, `quest_definition_for`, `get_ruleset`, `LeaderboardService.get_top_k/get_learner_rank`, `get_calendar_week`, and one raw `SELECT` on `notification_dispatches` (no per-learner reader exists).

## UI-only derivations (marked in `views.py`, not engine state)
| Item | Rule |
|---|---|
| level, xp_to_next | XP to reach level L = `50*L*(L-1)` (L2=100, L3=300, L4=600, L5=1000, L6=1500), on lifetime `xp_total`. |
| display_name, cohort_name | Fixed name list in `seed.py`, stored in `ui_profile.json`. |
| initials | First letters of first two words. |
| quest kind/title | Map from engine quest id (`lesson_starter_v1` -> weekly, `remediation_v1` -> remediation). |
| quest `xp_reward` | Always `null`: the engine grants no XP for quests, and I did not invent a value. |
| badge name | `week_warrior_v1` -> "Week Warrior". Only one badge exists in the engine, so `badges` has one item. |
| week strip | Monday-first calendar week of the as-of date in the learner's timezone. `future` = after today; `done` = in `activity_dates`; `frozen` = the N days before an active date whose `streak_outcomes` consumed N freezes; `today` = today with no activity; else `missed`. |
| streak.days | `new_streak` if last activity was today or yesterday, else 0 (the engine never expires a streak). |
| quest days_left | `ceil((deadline - as_of) / 1 day)`, floored at 0. |
| notification_settings | quiet hours = dispatcher constructor defaults (22 to 7); `push_enabled` from the seeded `LearnerPreferences`, which the engine does not persist. |
| leaderboard rows | Top 5 per scope; the learner's own row is appended if outside the top 5. `is_you` marks it. |

## Output keys vs your spec
All requested keys are present. Additions you did not ask for: `quests[].status` (`assigned` or `completed`, needed because a finished quest has to be distinguishable), `leaderboard.*_rows[].learner_id`, `.is_you`. `quiet_hours` is `{start_hour, end_hour}`. Expired quests are omitted.

## Tests
None written or modified (per the rules). Verification was manual only: the curl run below, a determinism check, and a read-only check.

## Commands and output
Setup and server:
```
PYTHONPATH=. .venv/bin/python -m webapp.seed
PYTHONPATH=. .venv/bin/uvicorn webapp.app:app --port 8000
```
`seed` output:
```
{ "learners": 12, "as_of": "2026-10-16T18:00:00Z", "epoch": "2026-W42", "data_dir": ".../webapp/demo_data" }
```
Notification store after seeding: `dispatched|23`, `notification_dispatches` 23 rows, 0 suppressions.

**Determinism:** seeded, dumped `build_home` for all 12 learners, reseeded, dumped again: `cmp` reports identical (sha256 `76e79068...30a97`, 29,590 bytes). Caveat: the event store's `Created` timestamps and dispatch ids use wall clock/uuid4, so the `.db` files are not byte-identical; the API output is.

**Read-only:** `sha256sum webapp/demo_data/*.db` before and after the requests below: unchanged ("DB checksums unchanged after requests").

**All 12 learners (as-of strip first 5 days, from the two-reseed dump):**
```
Ananya Das       5 [done x5]                      weekly assigned 2/3
Priya Nair       4 [done, done, frozen, done, done] weekly completed
Arjun Reddy      4 [done x4, today]               weekly completed
Vikram Rao       5 [done x5]                      remediation assigned 0/1 + weekly completed
Rohan Iyer       1 [done, done, done, missed, done] weekly completed
Ishaan Gupta     3 [missed, done, done, done, today] weekly assigned 2/3
Sneha Kulkarni   5 [done x5]                      remediation completed + weekly completed
Aarav Mehta      7 [done x5]                      badge earned
(others: 5-day streaks, weekly completed)
```
All five week states (`done`, `frozen`, `today`, `missed`, `future`) appear.

Real curl output (saved by the run, `python3 -m json.tool` for readability):
```
$ curl -s http://localhost:8000/api/learners
[
    {
        "id": "c6bb39cc-7ede-5881-83ca-1f84ebd502c1",
        "display_name": "Aarav Mehta"
    },
    {
        "id": "062566b7-92cc-58c1-baa3-02fe4ffd8f5d",
        "display_name": "Ananya Das"
    },
    {
        "id": "5e45c482-1318-52ec-8b1c-f116f08db37f",
        "display_name": "Arjun Reddy"
    },
    {
        "id": "afabc3f3-e17b-568b-b824-8123953874f8",
        "display_name": "Diya Menon"
    },
    {
        "id": "b1102b23-33f4-570e-b81d-e3b9ac243cfc",
        "display_name": "Ishaan Gupta"
    },
    {
        "id": "1d6b3c14-06bc-5524-9b40-7007ff5ed83e",
        "display_name": "Kabir Singh"
    },
    {
        "id": "bca1da5f-f106-526d-9bf3-35d91e61b374",
        "display_name": "Meera Pillai"
    },
    {
        "id": "4871b5d7-1673-5416-8a37-48353d80e6a4",
        "display_name": "Priya Nair"
    },
    {
        "id": "a9ec8603-758d-5d9e-bfce-e60a1d311ff6",
        "display_name": "Rohan Iyer"
    },
    {
        "id": "bc056f39-1fae-5982-80d8-82e8650f7f40",
        "display_name": "Sneha Kulkarni"
    },
    {
        "id": "a4b67f76-95be-50c1-8157-55022bf4e70d",
        "display_name": "Tara Bose"
    },
    {
        "id": "8da11022-e214-5ce4-ba9d-57d72dcb0448",
        "display_name": "Vikram Rao"
    }
]

$ curl -s http://localhost:8000/api/learners/c6bb39cc-7ede-5881-83ca-1f84ebd502c1/home
{
    "learner": {
        "id": "c6bb39cc-7ede-5881-83ca-1f84ebd502c1",
        "display_name": "Aarav Mehta",
        "initials": "AM",
        "level": 5,
        "xp": 1300,
        "xp_to_next": 200,
        "ruleset": "v1"
    },
    "streak": {
        "days": 7,
        "freezes_left": 0,
        "week": [
            "done",
            "done",
            "done",
            "done",
            "done",
            "future",
            "future"
        ]
    },
    "quests": [
        {
            "kind": "weekly",
            "title": "Weekly lesson sprint",
            "status": "completed",
            "progress": 3,
            "target": 3,
            "xp_reward": null,
            "days_left": 6
        }
    ],
    "badges": [
        {
            "name": "Week Warrior",
            "earned": true
        }
    ],
    "leaderboard": {
        "cohort_name": "Cohort A",
        "cohort_rows": [
            {
                "rank": 1,
                "learner_id": "5e45c482-1318-52ec-8b1c-f116f08db37f",
                "display_name": "Arjun Reddy",
                "xp": 1200,
                "is_you": false
            },
            {
                "rank": 2,
                "learner_id": "c6bb39cc-7ede-5881-83ca-1f84ebd502c1",
                "display_name": "Aarav Mehta",
                "xp": 1100,
                "is_you": true
            },
            {
                "rank": 3,
                "learner_id": "8da11022-e214-5ce4-ba9d-57d72dcb0448",
                "display_name": "Vikram Rao",
                "xp": 1000,
                "is_you": false
            },
            {
                "rank": 4,
                "learner_id": "1d6b3c14-06bc-5524-9b40-7007ff5ed83e",
                "display_name": "Kabir Singh",
                "xp": 900,
                "is_you": false
            },
            {
                "rank": 5,
                "learner_id": "a9ec8603-758d-5d9e-bfce-e60a1d311ff6",
                "display_name": "Rohan Iyer",
                "xp": 800,
                "is_you": false
            }
        ],
        "global_rows": [
            {
                "rank": 1,
                "learner_id": "5e45c482-1318-52ec-8b1c-f116f08db37f",
                "display_name": "Arjun Reddy",
                "xp": 1200,
                "is_you": false
            },
            {
                "rank": 2,
                "learner_id": "c6bb39cc-7ede-5881-83ca-1f84ebd502c1",
                "display_name": "Aarav Mehta",
                "xp": 1100,
                "is_you": true
            },
            {
                "rank": 3,
                "learner_id": "8da11022-e214-5ce4-ba9d-57d72dcb0448",
                "display_name": "Vikram Rao",
                "xp": 1000,
                "is_you": false
            },
            {
                "rank": 4,
                "learner_id": "bc056f39-1fae-5982-80d8-82e8650f7f40",
                "display_name": "Sneha Kulkarni",
                "xp": 1000,
                "is_you": false
            },
            {
                "rank": 5,
                "learner_id": "a4b67f76-95be-50c1-8157-55022bf4e70d",
                "display_name": "Tara Bose",
                "xp": 900,
                "is_you": false
            }
        ],
        "tie_rule": "Ranked by XP this week. Ties go to whoever reached that XP first, then to the lower learner ID."
    },
    "inbox": [
        {
            "kind": "quest_update",
            "title": "Weekly quest complete",
            "body": "You finished 3 lessons this week.",
            "created_at": "2026-10-16T12:00:00Z"
        },
        {
            "kind": "general",
            "title": "7-day streak",
            "body": "Keep it going tomorrow.",
            "created_at": "2026-10-16T09:00:00Z"
        },
        {
            "kind": "general",
            "title": "Badge earned: Week Warrior",
            "body": "You kept a 7-day streak.",
            "created_at": "2026-10-16T08:00:00Z"
        }
    ],
    "notification_settings": {
        "quiet_hours": {
            "end_hour": 7,
            "start_hour": 22
        },
        "push_enabled": true
    }
}

$ curl -s http://localhost:8000/api/learners/4871b5d7-1673-5416-8a37-48353d80e6a4/home   (streak and week only)
{"days": 4, "freezes_left": 0, "week": ["done", "done", "frozen", "done", "done", "future", "future"]}

--- error cases
$ curl -si 'http://localhost:8000/api/learners/00000000-0000-0000-0000-000000000000/home'
HTTP/1.1 404 Not Found
content-type: application/json
{"error":"learner_not_found","message":"No learner with id '00000000-0000-0000-0000-000000000000'"}
$ curl -si 'http://localhost:8000/api/learners/not-a-uuid/home'
HTTP/1.1 404 Not Found
content-type: application/json
{"error":"learner_not_found","message":"No learner with id 'not-a-uuid'"}
$ curl -si 'http://localhost:8000/api/learners/c6bb39cc-7ede-5881-83ca-1f84ebd502c1/home?as_of=garbage'
HTTP/1.1 422 Unprocessable Content
content-type: application/json
{"error":"invalid_as_of","message":"as_of must be an ISO-8601 timestamp with a timezone"}
--- as_of override Mon 2026-10-12T12:00Z (streak/quests/inbox)
{"streak": {"days": 7, "freezes_left": 0, "week": ["done", "future", "future", "future", "future", "future", "future"]}, "quests": [{"kind": "weekly", "title": "Weekly lesson sprint", "status": "completed", "progress": 3, "target": 3, "xp_reward": null, "days_left": 10}], "inbox": []}
--- CORS
Origin: http://localhost:5173
access-control-allow-origin: http://localhost:5173
Origin: http://evil.example.com
(no access-control-allow-origin header)
```

## Assumptions
- Lifetime XP (`learner.xp`, drives level) differs from leaderboard XP (this ISO week only). Aarav shows 1300 vs 1100 because 200 XP was earned in week 2026-W41 (10-10, 10-11). That is intentional and the tie rule text says "this week".
- A non-UUID id returns 404 (not 422), matching "unknown learner".
- `as_of` must carry a timezone; naive or unparsable values return 422 `invalid_as_of`.
- Per-request open/close is acceptable for a demo (about a second for 12 learners was not measured).

## Uncertainty / known limits
- **`as_of` earlier than the seeded one is only partly consistent.** XP, streak, badges and leaderboard come from the engine's latest state, not a replay up to `as_of`. Only the week strip (future days hidden), quest expiry/days_left, inbox filtering and the leaderboard epoch follow `as_of`. In the override example above, the streak still says 7 and the quest still says completed on 10-12.
- `get_summary(as_of=)` marks an unfinished quest expired only after its deadline; I did not test an `as_of` past a deadline.
- The weekly quest's 7-day window is the engine default, not a calendar week.
- Opening the event store read-only still creates `events.db-wal`/`-shm` files next to the DB (SQLite WAL bookkeeping, no data changes). A first attempt that converted the seeded DBs to rollback-journal mode broke `LearnerApplication`, which runs `PRAGMA journal_mode=WAL` on open; the final seed leaves WAL on.
- The `__new__` leaderboard hack depends on private attributes (`connection`, `_transaction_depth`) of `SQLiteLeaderboardStore`.
- Concurrent writer plus API reader on WAL files was not tested.
- Frozen-day derivation relies on `consumed` counts; verified for one freeze scenario only (Priya).
- `Learner._create(Learner.Created, id=...)` is a semi-private eventsourcing API used so ids are deterministic; it worked on eventsourcing 9.5.5 and may not elsewhere.
- No browser/frontend consumer exists yet, so CORS was checked with curl `Origin` headers only.
