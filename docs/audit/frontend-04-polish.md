# Frontend 04: Polish and run instructions

Builds on [frontend-03-page.md](frontend-03-page.md). No tests written or modified. Nothing under `src/` or `tests/`, `requirements-lock.txt`, `.github/` or `pyproject.toml` was touched this step. No git write commands were run.

## Changes
| File | Change |
|---|---|
| `webapp/static/app.js` | Nav pills: click handler smooth-scrolls to the section, focuses it, sets the clicked pill active (`aria-current`), and returns to Home when scrolled back to the top. New Refresh handler and a "Refreshing..." state. New "as of" footer renderer. |
| `webapp/static/index.html` | Refresh button next to the avatar (before the learner select); `data-target` on the pills; `tabindex="-1"` on the Quests, Badges and Leaderboard cards so they can take focus; `<footer class="page-foot">` with the as-of line. |
| `webapp/static/styles.css` | `.refresh` button styles, `.page-foot` styles (incl. mobile padding). |
| `webapp/views.py` | Added a top-level `as_of` key (ISO, `...Z`) to the home response, so the page can show which instant the demo data is for. Additive. |
| `README.md` | Added a "Running the web UI" section between "How to Run Locally" and "Reproducing the evidence". Nothing else in the README changed (diff below). |

Before: nav pills were plain anchors (they worked, but the active pill never changed), the toggle worked, there was no Refresh button and no as-of line, and the README did not mention the web UI. After: as above.

## README diff
```diff
diff --git a/README.md b/README.md
index 5801200..ecf093d 100644
--- a/README.md
+++ b/README.md
@@ -112,6 +112,24 @@ The architecture diagram above is the repository's single high-level architectur
 
 Kafka/Redpanda, Redis, and PostgreSQL setup instructions will be added only if measured scale or reliability needs require those components.
 
+## Running the web UI
+
+A read-only learner home page runs on seeded demo data (UI layer in `webapp/`, not engine state). From the repository root, in an activated virtual environment:
+
+```bash
+python -m pip install -e ".[web]"
+```
+
+```bash
+python -m webapp.seed
+```
+
+```bash
+python -m uvicorn webapp.app:app --port 8000
+```
+
+Then open <http://localhost:8000/>.
+
 ## Reproducing the evidence
 
 - `requirements-lock.txt` pins the exact dependency versions used for the recorded test runs.
```

## Step 1: nav pills and toggle
- **Toggle:** already switched rows client-side. Verified at 900px: Cohort shows "Cohort A" with `Arjun Reddy / You / Vikram Rao / Kabir Singh / Rohan Iyer`; Global shows "All learners" with `Arjun Reddy / You / Vikram Rao / Sneha Kulkarni / Tara Bose`; `aria-pressed` flips; `performance.timeOrigin` unchanged (no reload).
- **Pills (900px, scrolling forced to instant, see uncertainty):** Quests scrolls to `scrollY 399` with the section 16px from the top; Badges `scrollY 657`, section top 16; Leaderboard `scrollY 835` (the end of the page, so its top is 56); each time the clicked pill is the only one with `aria-current` and the section has focus. Home scrolls to 0. Scrolling back to the top returns the active pill to Home (confirmed after a screenshot forced a frame; see uncertainty).
- At 1360px the whole page is only ~60px taller than the viewport, so Quests/Badges/Leaderboard are already on screen and the pills mainly move focus and the active state.
- **Behaviour change:** the active pill follows clicks, not scroll position. In the two-column layout several sections sit near the top, so a scroll-spy would mark the wrong one (I tried it first, and Leaderboard went active at load at 1360px, so I removed it).

## Step 2: layout at 1360, 900 and 390px
Measured in the built-in browser pane with a script (`scrollWidth` vs `innerWidth`, plus every element whose box leaves the viewport):

| Width | `scrollWidth` / `innerWidth` | Elements outside viewport | Columns |
|---|---|---|---|
| 1360 | 1346 / 1361 (scrollbar) | none | side by side |
| 900 | 885 / 900 | none | stacked |
| 390 | 391 / 391 | none | stacked, both top cards 359px wide |

No horizontal overflow at any of the three widths, so no layout fix was needed. At 390 the header wraps to four rows (brand, pills, bell + user + Refresh, select), checked in a screenshot.

## Step 3: Refresh and as-of line
- Refresh re-fetches the current learner (`GET .../home`): button disabled and labelled "Refreshing..." during the request, back to "Refresh" after; exactly 1 new `/home` request per click. It re-renders everything, including the as-of line, and keeps the Cohort/Global choice.
- Footer text (from the API `as_of`, formatted in UTC): `Seeded demo data, as of Fri, 16 Oct 2026, 18:00 UTC. Not live.` It is the last element on the page (its bottom equals the document height).

## Step 5: README commands in a clean shell
Run with `env -i HOME=$HOME PATH=/usr/bin:/bin bash --noprofile --norc <script>` from the repo root, in a **brand-new venv** (system Python 3.12.3) so the install command is tested for real. The script ran the three README commands as written, then curl:
```
$ which python
/tmp/claude-1000/-home-sampadram-Sampadram-IITH-SEM-7-DATGC-project-gamification-engine/05b05cf7-cc04-46b5-b926-6d350354e009/scratchpad/cleanvenv/bin/python
Python 3.12.3
$ python -m pip install -e ".[web]"
  Stored in directory: /tmp/pip-ephem-wheel-cache-immwba38/wheels/ff/a2/38/766608205451883fd7ee0848b44315caf812884245cfc9e6ff
Successfully built gamification-engine
Installing collected packages: typing_extensions, rpds-py, idna, h11, click, attrs, annotated-types, annotated-doc, uvicorn, typing-inspection, referencing, pydantic-core, opentelemetry-api, eventsourcing, anyio, starlette, pydantic, jsonschema-specifications, jsonschema, fastapi, gamification-engine
Successfully installed annotated-doc-0.0.5 annotated-types-0.8.0 anyio-4.16.0 attrs-26.1.0 click-8.5.0 eventsourcing-9.5.5 fastapi-0.162.0 gamification-engine-0.1.0 h11-0.16.0 idna-3.20 jsonschema-4.26.0 jsonschema-specifications-2025.9.1 opentelemetry-api-1.45.2 pydantic-2.14.2 pydantic-core-2.50.2 referencing-0.37.0 rpds-py-2026.9.1 starlette-1.10.0 typing-inspection-0.4.4 typing_extensions-4.16.0 uvicorn-0.54.0
$ python -m webapp.seed
{
  "learners": 12,
  "as_of": "2026-10-16T18:00:00Z",
  "epoch": "2026-W42",
  "data_dir": "/home/sampadram/Sampadram/IITH/SEM 7/DATGC/project/gamification-engine/webapp/demo_data"
}
$ python -m uvicorn webapp.app:app --port 8000   (background)
--- server log
INFO:     Started server process [13833]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
$ curl -si http://localhost:8000/ | head -5
HTTP/1.1 200 OK
date: Tue, 03 Nov 2026 06:09:21 GMT
server: uvicorn
content-type: text/html; charset=utf-8
accept-ranges: bytes
$ curl -s http://localhost:8000/api/learners | head -c 120
[{"id":"c6bb39cc-7ede-5881-83ca-1f84ebd502c1","display_name":"Aarav Mehta"},{"id":"062566b7-92cc-58c1-baa3-02fe4ffd8f5d"
```
Exit status 0. (The `pip install` tail line is trimmed to the last 4 lines.) This venv sits in the session scratchpad, not in the repo; `pip install -e` refreshed the git-ignored `gamification_engine.egg-info/` and `build/` in the repo, and the seed regenerated `webapp/demo_data/` (identical output, since seeding is deterministic).

## Step 6: what the page shows that is derived in the UI layer, not engine state

**Straight from the engine** (for contrast): lifetime XP, streak count as of last activity, freezes remaining, which dates had activity, quest assignment/progress/target/deadline, earned badges, leaderboard ranks, scores and tie order, the ruleset version (`applied_rule_versions`), and the dispatched in-app notification rows.

**Derived or invented in `webapp/views.py` (marked "UI-only, not engine state" in the code):**
| Shown on the page | Where it comes from |
|---|---|
| **Level**, **XP to next level**, ring fill (`level_start_xp`, `next_level_xp`) | Formula on lifetime XP: XP needed to reach level L = `50*L*(L-1)`. The engine has no level concept. |
| **Display names** and **initials** | A fixed list in `seed.py`, stored in `ui_profile.json`. The engine has only learner UUIDs. |
| **Cohort name** ("Cohort A") | UI map from the engine cohort id `cohort_a`. |
| **Quest titles** ("Weekly lesson sprint"), quest "kind" (weekly / comeback) | UI map from engine quest ids. The page's comeback-quest description sentence is also UI text. |
| **Quest XP reward** | Not shown: the engine grants no quest XP, so the API returns `null`. |
| **Quest "days left"** | `ceil((deadline - as_of) / 1 day)`, computed in the UI layer. |
| **Badge name** ("Week Warrior") and the **locked** state | Name is a UI map; "locked" means "not in the engine's issued badges". Only one badge exists in the engine, so only one is shown. |
| **7-day strip** | Monday-first calendar week of the as-of date. "done" = engine activity date; "frozen" is **inferred** from how many freezes the next active day consumed (the engine stores a count, not the frozen dates); "today", "missed" and "future" come from comparing dates to the as-of day. |
| **Streak day count shown** | Engine value, but forced to 0 if the last activity was before yesterday (the engine never expires a streak). |
| **Leaderboard "tie rule" text** | Plain-English description written in the UI layer of the engine's ordering (score, earlier first-reached, then learner id). |
| **Leaderboard rows** | Top 5 per scope plus your own row if outside the top 5; XP shown is **this ISO week's** XP (what the engine ranks), which can differ from lifetime XP on the XP card. |
| **Notification settings** | Quiet hours are the dispatcher's default constructor values; "push on/off" is a seeded flag, because the engine does not persist learner preferences. |

**Derived in the browser (`app.js`):** the caption under the streak ("Complete a lesson today...", based only on whether a "today" cell exists), "N freezes left" wording, progress-ring fraction, "Lv N", "All learners" subtitle, bell count (= number of inbox items), notification icon and colour per kind, the "as of" line, and empty-state messages.

**Demo-only (seeded, not engine behaviour):**
- The population and its events come from the synthetic workload generator, with scripted cases (freeze, missed day, badge, remediation quests).
- The **notifications** are written by the seed script through the real dispatcher; the engine itself never creates notifications from learner events. Their titles and body text are authored in `seed.py`.
- The weekly quest is assigned by the seed script; the engine does not auto-assign it.
- "Now" is the fixed `2026-10-16T18:00:00Z`.

## Tests
None written or modified. Manual checks only, as above.

## Assumptions
- `python -m uvicorn ...` (used in the README) is equivalent to the `uvicorn` command used earlier; both put the repo root on the import path when run from the repo root.
- The README's install command is for an activated virtual environment, as the neighbouring section assumes.

## Uncertainty / not verified
- **Smooth scrolling could not be observed in the built-in browser pane**: even a plain `window.scrollTo({behavior: "smooth"})` did not move the page there. I verified the pill logic with scrolling forced to instant, so the target positions, focus and active state are confirmed but the smooth animation is not. The pane also only delivers `scroll` events while it is rendering frames, so the "back to Home at the top" check passed only after a screenshot forced a frame.
- 900px and 390px were emulated viewports in one Chromium pane, not real devices; other browsers were not tried.
- Keyboard-only navigation and a screen reader were not tried.
- Nothing was checked at widths between those three (for example 640 to 760, where the header wraps differently).
- The clean-shell run used a new venv with network access to PyPI; an offline install would fail.
