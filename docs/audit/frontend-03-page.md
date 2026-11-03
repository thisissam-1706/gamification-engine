# Frontend 03: Learner home page (static files)

Builds on [frontend-02-api.md](frontend-02-api.md). No tests written or modified. Nothing under `src/` or `tests/`, `requirements-lock.txt`, `.github/` or `pyproject.toml` was touched this step. No git write commands were run.

## Changes
| File | Change |
|---|---|
| `webapp/static/index.html` | New. Page skeleton; every value is filled by JS (no learner data in the HTML). |
| `webapp/static/styles.css` | New. Palette and sizes from the design spec, as CSS variables. |
| `webapp/static/app.js` | New. Fetches `/api/learners`, loads the first learner, renders all sections, re-renders on select change. |
| `webapp/app.py` | Added `StaticFiles(directory=webapp/static, html=True)` mounted at `/`, **after** the two `/api` routes so they win. Two imports added (`Path`, `StaticFiles`) and the docstring now mentions the page URL. |
| `webapp/views.py` | Added two keys to `learner`: `level_start_xp` and `next_level_xp` (UI-only), plus a helper `level_start_xp(level)`. See "Deviations". |

Before: `GET /` returned 404 (no static mount). After: `/` serves the page; `/styles.css` and `/app.js` are served from the same mount.

## Deviations from the spec / API contract
1. **Two extra API keys** (`learner.level_start_xp`, `learner.next_level_xp`). The progress ring needs the current level's bounds. Without them the JS would have to copy the UI-only level formula from `views.py`. They are additive; existing keys are unchanged. Aarav: `level_start_xp 1000`, `next_level_xp 1500`, `xp 1300`, so the ring is 60% full.
2. **No "+N XP" on quest tiles.** The API's `xp_reward` is `null` (the engine grants no quest XP), so the text is omitted rather than invented. If a number ever appears it renders as "+N XP".
3. **Remediation tile text.** The spec asks for "a short description"; the engine has none, so the JS uses fixed UI-only wording ("Finish a short practice lesson to bounce back from your checkpoint."). The comeback tile does not show the API `title` ("Practice quest"), following the spec's list of tile parts. A completed quest shows a "Complete" chip/label (uses the `status` key I added in step 2).
4. **Leaderboard can show 6 rows.** The API returns the top 5 plus the learner's own row if outside the top 5; all are rendered, with a "..." separator when ranks skip, so the learner is always visible.
5. **Nav pills are in-page anchors** (`#main`, `#quests`, `#leaderboard`, `#badges`), not separate pages. Only Home has a real target in the sense of being the whole page. The bell scrolls to and focuses the Inbox card.
6. **Streak caption** is generated from the week array: a `today` cell means "Complete a lesson today..."; no `today` cell means "Today's lesson is done." (the API does not say which cell is today, so a done-today and a no-today case look the same to the page).
7. Global scope subtitle "All learners" is UI-only text.

## Design spec checks
- Colours, fonts (Outfit 400-700 from Google Fonts), 20px radius cards with 24px padding, header, nav pills, 44px bell with red count badge (hidden at 0), 40px amber avatar, plain `<select>`, max-width 1280px body with `flex: 999 1 640px` / `flex: 1 1 340px` columns and 24px gap: as specified.
- Streak: 64px amber-tint square with flame, 40px count, 7 cells (36px, radius 10) with M T W T F S S. `done` amber fill, `frozen` blue tint, `today` 2px dashed violet, `missed`/`future` neutral grey. Blue chip "N freezes left".
- XP: SVG ring r=46, stroke 12, track `#EEEAFF`, "Lv N" in the centre, XP at 32px, "N XP to level N+1", violet-tint ruleset chip.
- Quests: weekly tile (small-caps label, title, 12px bar, "x of y lessons"); comeback tile (2px amber border, amber tint, "N days left" chip, amber bar `#B45309`). A tile renders only if the data has that quest kind.
- Badges: 72px circles; earned solid violet with award icon; locked 2px dashed grey with lock icon and muted label (plus screen-reader text "(locked)").
- Leaderboard: Cohort / Global segmented toggle (`aria-pressed`), subtitle, rows, "You" row with violet tint and 1px violet border, footnote from `tie_rule`.
- Inbox: tinted tile per message (`streak_risk` danger tint, `quest_update` and `remediation` amber tint, others violet tint), bold title, muted body, footer "Quiet hours 22:00 to 07:00. Push notifications on."
- Icons are all inline SVG, no emoji. No framework, no build step.

### Contrast (WCAG ratios computed with a script)
| Text / background | Ratio |
|---|---|
| `#14172B` on `#F5F6FA` | 16.38 |
| `#566079` on `#F5F6FA` / white / `#EEF0F6` | 5.81 / 6.28 / 5.51 |
| `#5B3DF5` on white / `#EEEAFF` | 6.12 / 5.20 |
| `#3B22C4` on `#EEEAFF` | 8.07 |
| `#8A4A05` on `#FFFBF1` / `#FFF1D6` | 6.64 / 6.14 |
| `#1F4E8C` on `#DCEBFF` | 6.87 |
| `#14172B` on amber `#FFB020` | 9.67 |
| white on danger `#D92D4A` (count badge) | 4.75 |
| `#566079` on `#FFFBF1` / `#EEEAFF` | 6.08 / 5.34 |
| `#D92D4A` on `#FFF1F3` | **4.32, fails**, so danger red is used only for icons (non-text); message titles use `#14172B` (16.11) |

Day-cell text on the dashed `today` cell is `#3B22C4` on white (above 8:1). The dashed grey badge border `#8A93A8` is decorative.

## Accessibility notes
Real `<button>`, `<a>` and `<select>` elements; `aria-label` on the bell (includes the count), select and ring; day cells have `role="listitem"` with an `aria-label` such as "Wednesday: protected by a freeze"; progress bars use `role="progressbar"` with values; `:focus-visible` shows a 3px violet outline; skip link; `aria-busy` on `<main>` while loading; error banner has `role="alert"`; reduced-motion respected.

## Tests
None written or modified (per the rules). Manual verification only, below.

## Commands and output
Server (from repo root):
```
PYTHONPATH=. .venv/bin/uvicorn webapp.app:app --port 8000
```
Page open at `http://localhost:8000/`.

Data safety checks:
```
$ grep -n "innerHTML\|insertAdjacentHTML\|outerHTML\|document.write" webapp/static/app.js
2:// All API text goes in through textContent. innerHTML is used only for the constant
27:    svg.innerHTML = ICONS[name]; // constant markup only
$ grep -nEi "aarav|priya|1,?300|Level [0-9]|Cohort A" webapp/static/index.html
none
```
(The only `innerHTML` assignment is the constant icon table; all API text uses `textContent`.)

curl output (saved by the run):
```
$ curl -si http://localhost:8000/ | head -9
HTTP/1.1 200 OK
date: Tue, 03 Nov 2026 06:04:53 GMT
server: uvicorn
content-type: text/html; charset=utf-8
accept-ranges: bytes
content-length: 5817
last-modified: Tue, 03 Nov 2026 06:03:25 GMT
etag: "b46add3c44ac6ef141d5ddda87dcbb81"
vary: Origin

$ curl -s -o /dev/null -w '%{http_code} %{content_type} %{size_download}B' (each)
index.html: 200 text/html; charset=utf-8 5817B
styles.css: 200 text/css; charset=utf-8 10063B
app.js: 200 text/javascript; charset=utf-8 13189B

$ curl -s http://localhost:8000/api/learners | head -c 160
[{"id":"c6bb39cc-7ede-5881-83ca-1f84ebd502c1","display_name":"Aarav Mehta"},{"id":"062566b7-92cc-58c1-baa3-02fe4ffd8f5d","display_name":"Ananya Das"},{"id":"5e4

$ curl -s http://localhost:8000/api/learners/c6bb39cc-7ede-5881-83ca-1f84ebd502c1/home  (learner block incl. new keys)
{
  "id": "c6bb39cc-7ede-5881-83ca-1f84ebd502c1",
  "display_name": "Aarav Mehta",
  "initials": "AM",
  "level": 5,
  "xp": 1300,
  "xp_to_next": 200,
  "level_start_xp": 1000,
  "next_level_xp": 1500,
  "ruleset": "v1"
}

--- unknown learner and unknown path
HTTP/1.1 404 Not Found
{"error":"learner_not_found","message":"No learner with id '00000000-0000-0000-0000-000000000000'"}
HTTP/1.1 404 Not Found
{"detail":"Not Found"}
```

### Browser verification (built-in browser pane, `http://localhost:8000/`)
- Console: no errors or warnings after load.
- Desktop-width screenshot: two columns, leaderboard and inbox on the right; "Outfit" confirmed loaded (`document.fonts.check('16px Outfit')` true).
- 375px width: single column, no horizontal scroll (`scrollWidth` 376 vs `innerWidth` 376, so 1px over from rounding; no visible overflow in the screenshot).
- Learners checked via the select (DOM readout of the week cells):
  - Aarav Mehta: 7-day streak, badge earned, Cohort A rank 2 "You", 3 inbox messages, bell shows 3.
  - Priya Nair: `done,done,frozen,done,done,future,future`.
  - Ishaan Gupta: `missed,done,done,done,today,future,future`, caption "Complete a lesson today to keep your streak."
  - Rohan Iyer: `done,done,done,missed,done,future,future`.
  - Vikram Rao: comeback tile (amber, "6 days left", 0 of 1 lesson) next to the weekly tile; badge locked with dashed circle; Global toggle shows "All learners" with Vikram as "You" at rank 3.
- Error state: stubbing `fetch` to return a 500 `{message: "boom"}` showed the banner "Could not load this learner's home page. boom"; restoring `fetch` and pressing "Try again" cleared it and reloaded Aarav.
- Empty states: stubbing the home response with no quests, badges or inbox showed "No quests right now...", "No badges yet...", "You're all caught up.", hid the bell count, and "1 freeze left" (singular).

## Assumptions
- Learner list order from the API (alphabetical by name) means the first learner loaded is Aarav Mehta.
- "Level N" in the header and "Lv N" in the ring are the UI-only level from step 2.
- Google Fonts needs network access; offline, the page falls back to the system font.

## Uncertainty / not verified
- Keyboard-only navigation and a screen reader were not tried; focus outlines and ARIA are by construction only.
- Only Chromium (the app's browser pane) was used; no Firefox/Safari.
- After the empty-state stub test, a screenshot showed normal data again rather than the empty page. The DOM readouts taken in the same call confirmed the empty states, but I did not work out why the screenshot differed (possibly the pane re-rendered or reloaded). The empty cohort leaderboard message ("No rankings yet this week.") was therefore not seen on screen.
- Sub-pixel layout was judged from scaled screenshots, not measured against a design file.
- The 1px `scrollWidth` difference at 375px is unexplained but caused no visible overflow.
- No handling for an `as_of` picker: the page always uses the API default as-of.
