# Week 4 Fix 8b: Actions across a rule-version change

Status: **done. Not committed.**

- Week 3 review row 26 (sixth part): test new actions across a version
  change, historical replay and a late arrival.
- `tests/ingestion/test_rule_version_change.py` adds 2 tests: one sequential
  scenario covering all five steps, and one check that the `v2` registration
  does not leak. **Both pass.** Every hand-written value matched on the first
  run.
- `docs/event-contracts.md` gains an "Effective rule version" section.
- No source code changed, and no existing test or assertion changed.
- Suite excluding `tests/leaderboard` and `tests/notifications`: **212 passed,
  0 failed, 4 skipped**. After Fix 8a it was 210 passed, 4 skipped. See
  "Suite run note" for one run that hung.
- This builds on the uncommitted Fix 6a–8a working tree.

## Problem

Fix 8a made the rule version a trusted ingress setting, but each of its tests
used one version at a time. Nothing showed what happens to a single learner
across a switch:

- whether old awards keep their version after a restart and replay;
- whether an old message redelivered after the switch is still a duplicate;
- which version a late-arriving event gets.

## Scenario and hand-written expected values

There is one learner on one SQLite file. `v2` is registered with the
`test_rule_selection.py` pattern: `xp_amount=150`, a copy of v1's
`quest_targets`, `badge_threshold=7`, `badge_id="week_warrior_v1"`.

| # | Delivery | `occurred_at` | `ingested_at` | Config | `dedup_key` | Result | XP | `xp_total` |
|---|---|---|---|---|---|---|---|---|
| 1 | `cmp-1` / `…08b1` | 2026-09-01 09:00Z | 09-01 09:01Z | v1 (default) | `…08b1:v1` | accepted | 100 | 100 |
| 1 | `cmp-2` / `…08b2` | 2026-09-02 09:00Z | 09-02 09:01Z | v1 (default) | `…08b2:v1` | accepted | 100 | 200 |
| — | *config switched to v2 (notionally on 2026-09-04)* | | | | | | | |
| 1 | `cmp-3` / `…08b3` | 2026-09-05 09:00Z | 09-05 09:01Z | v2 | `…08b3:v2` | accepted | 150 | **350** |
| 2 | *close everything; reopen on the same files* | | | | | | | 350 |
| 3 | redeliver `cmp-1` / `…08b1` | 2026-09-01 09:00Z | 09-01 09:01Z | v2 | `…08b1:v1` | duplicate, `duplicate_event_id` | 0 | 350 |
| 4 | `cmp-4` / `…08b4` (late) | **2026-09-03 09:00Z** | 09-06 09:00Z | v2 | `…08b4:v2` | accepted | **150** | **500** |

### What each step asserts

1. **Version switch.** `xp_total == 350`. The stored `XPAwarded` facts, in
   order, are exactly `("xp:cmp-1", 100, "v1")`, `("xp:cmp-2", 100, "v1")`,
   `("xp:cmp-3", 150, "v2")`.
2. **Restart and replay.** After reopening:
   - `repository.get(...).xp_total == 350`;
   - `rebuild_learner_state(events).xp_total == 350`;
   - the first two awards are still `(100, "v1")`.

   During this step `RULESETS["v1"]` is replaced (by monkeypatch) with a rule
   set worth **999 XP**. If anything re-derived the old awards from the
   current rule set, the total would move away from 350. It does not. v1 is
   restored right after.
3. **Redelivery under v2.** The first message's key ends in `":v1"` and the
   config is v2. The result is `("duplicate", "duplicate_event_id")`. The
   stored event list is identical before and after, and `xp_total` stays
   350. The key is judged against the bound version from Fix 8a.
4. **Late arrival.** `cmp-4` has `occurred_at` 2026-09-03, before the switch,
   and arrives after it. It is accepted under **v2** with 150 XP, giving
   `xp_total == 500`. The full award list is v1, v1, v2, v2 with amounts 100,
   100, 150, 150.
5. **Traces.** `trace_delivery` on each delivery returns the matching
   `provenance.action_id`, `provenance.rule_version` (`v1`, `v1`, `v2`, `v2`),
   `provenance.dedup_key` (`<event_id>:<version>`) and
   `xp_awards == (XPAwardedFact(reward_id, amount, version),)`. The request
   asked for the three step-1 deliveries; the late `cmp-4` is traced as well.
   - `…08b1`'s trace also shows the step-3 evidence row
     `("duplicate", "duplicate_event_id")`.
   - `…08b4`'s provenance keeps `occurred_at "2026-09-03T09:00:00+00:00"` and
     `ingested_at "2026-09-06T09:00:00+00:00"`, which shows that the version
     follows acceptance and not event time.

`test_unregistered_version_cannot_be_configured_after_teardown` checks that
after the scenario `"v2" not in RULESETS`, `RULESETS["v1"].xp_amount == 100`,
and `IngestConfig("v2")` raises `ValueError`. So the monkeypatches do not leak
into other tests.

## Doc change: `docs/event-contracts.md`, "Effective rule version"

The new section, after "Producer and idempotency rules", states the policy
that these tests pin:

- The applying version is the trusted ingress configuration in force when the
  event is **accepted**. It is never taken from the envelope and never derived
  from `occurred_at`.
- It is persisted on the provenance row (`delivery_ids.rule_version`) and on
  the `XPAwarded` fact.
- Historical facts keep their version on replay.
- A redelivery of an already-accepted event is judged against the version it
  was accepted under. A new `event_id` must use the current version.

## Before / after

| | Before | After |
|---|---|---|
| Tests covering a version switch on one learner | none | 1 scenario test (5 steps) + 1 isolation test |
| `docs/event-contracts.md` | no statement of which version applies | "Effective rule version" section |
| Source code | — | unchanged |
| suite (excluding leaderboard and notifications) | 210 passed, 4 skipped | 212 passed, 4 skipped |

## Suite run note

The first full-suite run after adding these tests **hung**: no output after
more than 2 minutes, one pytest process, no child processes. I stopped it
before a stack could be captured, so the stuck test is not known. Four later
runs of the same command all finished normally with 212 passed, 4 skipped,
in about 2.7 s each. One of them was a verbose run, which showed every test
passing.

The hang did not reproduce. The suite contains multiprocessing tests on
shared SQLite files (`test_concurrent_*` in
`tests/ingestion/test_ingestion_service.py`), which are the most likely
source of an intermittent stall. The new tests are single-process. This is
recorded as an open observation, not a known cause.

## Known limits

- **Steps are sequential.** The scenario is one test because each step
  depends on the state the previous step left. A failure at one step stops
  the later ones. Each step is marked with a comment in the test.
- **Streaks are not asserted.** The late arrival back-fills 2026-09-03 into
  the learner's activity dates and can produce streak revisions. This test
  asserts XP only; streak behaviour for late arrivals is covered in
  `tests/state_machine/test_streak_updates.py`.
