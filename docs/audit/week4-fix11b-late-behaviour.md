# Week 4 Fix 11b: Late-event behaviour across the window

Status: **done. Not committed.**

- `tests/ingestion/test_late_window_behaviour.py` adds 1 scenario test on one
  SQLite file with a fixed clock. It covers the acceptance case "Out-of-Order
  Within 72h", an out-of-window delivery after a restart, and the clock time
  on provenance rows. It **passed on the first run**: every value below was
  derived by hand and written here before the test was run, and all matched.
- `docs/event-contracts.md` gains a "Late-event window" section.
- No source code changed, and no existing test or assertion changed.
- Suite excluding `tests/leaderboard` and `tests/notifications`: **261 passed,
  0 failed, 4 skipped**, in 2 of 2 runs. After Fix 11a it was 260 passed,
  4 skipped; the difference is the 1 new test.

## Hand-derived expected values (written before running)

### Inputs

- One learner, created and saved: the `Created` event is originator version 1.
- `_envelope` sets `payload.timezone = "Asia/Kolkata"` (UTC+05:30). Each
  `occurred_at` below is 10:00Z, which is 15:30 local time on the **same
  calendar date**, so `local_activity_date` returns that date.
- No freezes have been acquired, so `freeze_acquisitions` is empty.
- Rule `v1`: `xp_amount` 100, `badge_threshold` 7.
- `IngestConfig("v1", late_window=72h, clock=FixedClock)`.

| Step | Delivery | `occurred_at` | Clock (receive time) | Lateness | Expected |
|---|---|---|---|---|---|
| A | `cmp-0922` / `…0c01` | 2026-09-22T10:00Z | 2026-09-22T10:05Z | 5 min | accepted |
| B | `cmp-0921` / `…0c02` (envelope `ingested_at` set to 2026-10-30, which is irrelevant) | 2026-09-21T10:00Z | 2026-09-24T09:00Z | 71 h | accepted (inside 72 h) |
| C | restart both stores; `cmp-0922b` / `…0c03` | 2026-09-22T11:00Z | 2026-09-25T12:00Z | 73 h | rejected `outside_late_window` |

### Step A: `cmp-0922`, activity date 2026-09-22

- `calculate_streak({2026-09-22}, [])` gives `{2026-09-22: (1, 0, 0)}`: the
  first date, so streak 1, `remaining = 0 - 0 = 0`, `used = 0`.
- `previous_streak = self.new_streak = 0`.
- Loop over `[2026-09-22]`: `old` is None and `is_current` is True, so one
  StreakUpdated is emitted with revision 1 and supersedes None.

Events:

| v | Event | Fields |
|---|---|---|
| 2 | LessonCompleted | `completion_id "cmp-0922"`, `activity_date "2026-09-22"`, `source_event_id "…0c01"` |
| 3 | XPAwarded | `"xp:cmp-0922"`, 100, `"v1"` |
| 4 | StreakUpdated | `completion_id "cmp-0922"`, `activity_date "2026-09-22"`, `new_streak 1`, `freezes_remaining 0`, `freezes_consumed 0`, `previous_streak 0`, `revision 1`, `supersedes None`, `rule_version "v1"` |

After A: `new_streak 1`, `last_activity_date "2026-09-22"`,
`streak_revisions {"2026-09-22": 1}`, `xp_total 100`.

### Step B: `cmp-0921`, activity date 2026-09-21, arriving out of order

- **Late-window check:** 2026-09-24T09:00Z − 2026-09-21T10:00Z = 2 days
  23 hours = **71 h**, which is ≤ 72 h, so it continues.
- `calculate_streak({2026-09-21, 2026-09-22}, [])`:
  - 2026-09-21: the first date, so `(1, 0, 0)`.
  - 2026-09-22: `missing_days = 1 - 1 = 0`, so streak 2, giving `(2, 0, 0)`.
- `previous_streak = self.new_streak = 1`.
- Loop over the sorted dates:
  - **2026-09-21:** `old` is None and `is_current` is True, so it is emitted
    with revision 1, supersedes None, and
    `previous_streak = previous_streak = 1`. Values `(1, 0, 0)`.
    - The handler leaves `new_streak` alone, because "2026-09-21" is earlier
      than `last_activity_date` "2026-09-22".
    - `new_streak == 1 and previous_streak > 1` is false (1 > 1 is false),
      so there is no `streak_before_break`.
  - **2026-09-22:** `old = (1, 0, 0, 0, "streak:<id>:2026-09-22:1", None)`.
    The date is not earlier than 2026-09-21, and it is not current. `changed`
    is true, because `(1, 0, 0)` differs from `(2, 0, 0)`.
    - So it is emitted with `revision = 1 + 1 = 2`,
      `supersedes = "streak:<id>:2026-09-22:1"` and
      `previous_streak = old[3] = 0`. Values `(2, 0, 0)`.
    - The handler sets `new_streak = 2`, because the date is not earlier than
      `last_activity_date`.
- **Badge:** `max(new_streak) = 2 < 7`, so no badge.
- **Break check:** `outcomes["2026-09-21"][0] == 1 and previous_streak (1) > 1`
  is false.

Events:

| v | Event | Fields |
|---|---|---|
| 5 | LessonCompleted | `completion_id "cmp-0921"`, `activity_date "2026-09-21"`, `source_event_id "…0c02"` |
| 6 | XPAwarded | `"xp:cmp-0921"`, 100, `"v1"` |
| 7 | StreakUpdated | `completion_id "cmp-0921"`, `activity_date "2026-09-21"`, `new_streak 1`, `freezes_remaining 0`, `freezes_consumed 0`, `previous_streak 1`, `revision 1`, `supersedes None` |
| 8 | StreakUpdated | `completion_id "cmp-0921"`, `activity_date "2026-09-22"`, `new_streak 2`, `freezes_remaining 0`, `freezes_consumed 0`, `previous_streak 0`, `revision 2`, `supersedes "streak:<learner id>:2026-09-22:1"` |

After B:

- `xp_total 200`
- `new_streak 2`
- `freezes_remaining 0`
- `last_activity_date "2026-09-22"`
- `activity_dates {"2026-09-21", "2026-09-22"}`
- `streak_revisions {"2026-09-21": 1, "2026-09-22": 2}`
- `streak_before_break None`
- no badge
- 8 stored events
- `rebuild_learner_state`: `xp_total 200`, `new_streak 2`,
  `freezes_remaining 0`, `activity_dates ("2026-09-21", "2026-09-22")`
- provenance `received_at`: …0c01 → `"2026-09-22T10:05:00+00:00"`,
  …0c02 → `"2026-09-24T09:00:00+00:00"` (from the clock, not
  `ingested_at` 2026-10-30)

### Step C: out-of-window input after a restart

- **Late-window check:** 2026-09-25T12:00Z − 2026-09-22T11:00Z = 3 days
  1 hour = **73 h**, which is more than 72 h, so the delivery is rejected
  with `outside_late_window`.
- The aggregate is not loaded, so there are no new events (still 8), and
  `xp_total` stays 200 with all streak facts unchanged.
- Evidence for the learner is exactly one row: `("rejected",
  "outside_late_window", "cmp-0922b", "…0c03", original_ref None, received_at
  "2026-09-25T12:00:00+00:00")`.
- `trace_delivery(…0c03)` returns `None`, because no `delivery_ids` row
  exists.
- The provenance row of `…0c01` still has `received_at
  "2026-09-22T10:05:00+00:00"`, and `trace_delivery(…0c02)` still resolves to
  `cmp-0921`.

**Correction made while writing the test, before the first run.** The draft's
last bullet said "the trace of `…0c01` shows `received_at`". But
`DeliveryProvenance` (`trace.py`) does not expose `received_at`; Fix 11a added
it to the `delivery_ids` row and `DeliveryBinding` only. The test therefore
reads `evidence.delivery_binding(…).received_at`. Requirement 3 asks about
"the provenance row", which is that row. No expected value changed.

## Test (`tests/ingestion/test_late_window_behaviour.py`)

`test_late_event_behaviour_across_the_window` uses the real
`ingest_lesson_completed` (not the unbounded test helper) with
`IngestConfig("v1", late_window=timedelta(hours=72), clock=FixedClock(...))`.

| Requirement | Assertions (all hand-written) |
|---|---|
| 1. Within-window late arrival | A then B are both `("accepted", None)`. The StreakUpdated facts, as `(v, completion_id, activity_date, new_streak, freezes_remaining, freezes_consumed, previous_streak, revision, supersedes, rule_version)`, equal exactly `(4, cmp-0922, 2026-09-22, 1, 0, 0, 0, 1, None, v1)`, `(7, cmp-0921, 2026-09-21, 1, 0, 0, 1, 1, None, v1)` and `(8, cmp-0921, 2026-09-22, 2, 0, 0, 0, 2, "streak:<id>:2026-09-22:1", v1)`. The event types in order are Created, LessonCompleted, XPAwarded, StreakUpdated, LessonCompleted, XPAwarded, StreakUpdated, StreakUpdated. Aggregate: `xp_total` 200, `new_streak` 2, `freezes_remaining` 0, `last_activity_date` 2026-09-22, both activity dates, `streak_revisions {09-21: 1, 09-22: 2}`, `streak_before_break` None, no badges. `rebuild_learner_state` gives 200 / 2 / 0 and both dates. B's envelope `ingested_at` (2026-10-30) played no part. |
| 2. Out-of-window input after a restart | Both stores are closed and reopened on the same file. C (73 h) gives `("rejected", "outside_late_window")`. The stored events equal the 8 from before, and `xp_total` 200, `new_streak` 2 and the revisions are unchanged. `trace_delivery(…0c03)` is `None` and `delivery_binding(…0c03)` is `None`. The evidence list is exactly one row `("rejected", "outside_late_window", "cmp-0922b", "…0c03", None, "2026-09-25T12:00:00+00:00")`. |
| 3. Clock on the provenance row | `delivery_binding(…0c01).received_at == "2026-09-22T10:05:00+00:00"` and `delivery_binding(…0c02).received_at == "2026-09-24T09:00:00+00:00"`. Both are the clock's times, not the envelopes' `ingested_at`. The first is checked again after the restart. |

## Doc change: `docs/event-contracts.md`, "Late-event window"

This section follows "Rule sets". It states:

- Lateness is measured from the envelope `occurred_at` to the server receive
  time from the trusted ingress clock (`IngestConfig.clock`). The envelope
  `ingested_at` is provenance only.
- More than 72 h is rejected as `outside_late_window`, with evidence and no
  state change (no completion, reward, streak update or delivery binding).
- Exactly 72 h is accepted.
- A redelivery of an already-accepted event is a duplicate
  (`duplicate_event_id`) regardless of age.
- One line: a legitimate learner offline for more than 72 h loses that
  completion's reward under this policy. This is a product decision that can
  be revisited.

## Before / after

| | Before | After |
|---|---|---|
| Out-of-order arrival within 72 h, tested end to end with exact streak revisions | not covered | covered: accepted, streak 1 → 2 through the 09-22 revision 2 superseding revision 1 |
| Out-of-window delivery after a restart, tested | not covered | covered: rejected, no state change, no trace, one evidence row |
| `docs/event-contracts.md` late policy | not stated | "Late-event window" section |
| suite (excluding leaderboard and notifications) | 260 passed, 4 skipped | 261 passed, 4 skipped |

## Observations

- **`previous_streak` on revisions.** The revision event for 2026-09-22 (v8)
  carries `previous_streak 0`. It copies `old[3]`, the original event's
  `previous_streak`, rather than the streak before the out-of-order arrival
  (which was 1). This is how `complete_lesson` builds revisions today, and the
  test pins it as observed. Whether a revision should carry the original
  `previous_streak` is a modelling question; it was not changed here.
- **Trace does not show `received_at`.** `trace_delivery` does not expose
  `received_at`. Adding it to `DeliveryProvenance` would change the exact
  `DeliveryProvenance(...)` assertions in `test_trace.py` and
  `test_rule_digest.py`, so it was left for a separate decision.
