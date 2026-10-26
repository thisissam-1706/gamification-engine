# Week 5 - M6 rule-based abuse detection (implemented for synthetic fixtures, flag-only)

## Summary
M6 is implemented for synthetic fixtures and is flag-only. It is **not** claimed complete or validated: every
threshold is a conservative placeholder checked only against seeded synthetic personas.

- XP decay exists as an opt-in, sha-256-pinned rule set `v2` (100, 50, 30, 10, 0), decided at `Learner.complete_lesson`
  and stored on `XPAwarded` (`repeat_index`, `completion_kind`). `v1` is unchanged and still the default.
- `src/abuse` holds pinned rules (`abuse_rules.json` v1), pure detectors (replay, duplicate_exploit,
  impossible_timing, scripted_farming), an idempotent SQLite signal store, a retroactive service with shadow mode,
  the cohort gaming guardrail and the evaluation code.
- Detection never changes XP, streaks, leaderboards or any other state, and never produces `confirmed`.
- Full suite: baseline `421 passed, 4 skipped, 1 deselected`; now `556 passed, 3 skipped, 1 deselected`
  (+135 new, the skipped placeholder became real tests, no existing test edited).
- One deviation from the brief (see Blocker and Assumptions): ruleset `v2` is shipped in `rulesets_decay.json` and is
  opt-in, not an entry in `rulesets.json`, because two existing tests forbid a second default rule set.

## Baseline (Phase 0)
Command: `python -m pytest -q` (venv `.venv`, Python 3.14)

```
421 passed, 4 skipped, 1 deselected in 10.84s
```
- No failures. `.pytest_cache/v/cache/lastfailed` listed
  `test_assignment_matches_golden_hash_buckets[research subject-experiment alpha-variant_a]`;
  it passes in the baseline run, so it is a stale cache entry, not a live failure.
- 1 deselected = the packaging integration test (`-m integration`). 4 skipped include the `test_point_farming` placeholder.

## Blocker found in Phase 0 (decision recorded)
Two existing tests make a second entry in `src/state_machine/rulesets.json` impossible without editing them:
- `tests/state_machine/test_rules_immutable.py::test_packaged_file_loads_exact_v1` asserts
  `list(load_rulesets()) == ["v1"]` and `load_rulesets() == dict(RULESETS)`.
- `tests/ingestion/test_rule_version_change.py::test_unregistered_version_cannot_be_configured_after_teardown`
  asserts `"v2" not in RULESETS` and that `IngestConfig("v2")` raises.

The brief forbids editing existing tests, so `v2` lives in a separate pinned file `src/state_machine/rulesets_decay.json` and
is registered only on request (`register_decay_rulesets()`). Everything else in the brief (digest rule, strict loader,
`decay` field, `xp_for_completion`) is implemented as specified.

A second collision was found and fixed in my code: `tests/ingestion/test_provenance.py::test_old_shape_lesson_completed_event_still_replays`
pins the exact stored key set of `LessonCompleted`. My first design added `completion_kind` to that event. I moved
`completion_kind` and `repeat_index` onto `XPAwarded` instead and fold the eligible-repeat count from `XPAwarded`
(a `completion_lessons` map is filled when `LessonCompleted` is folded). `LessonCompleted` keeps its exact shape.

## Changes (file by file)
Modified
- `src/state_machine/rules.py`: optional `RuleSet.decay` (validated, read-only, in the digest only when present);
  `_parse_rulesets` shared loader accepting optional `decay` but still rejecting unknown keys and digest mismatches;
  `load_decay_rulesets`, `register_decay_rulesets`, `DECAY_RULESETS_FILE`; pure `xp_for_completion`. `xp_amount_for` untouched.
- `src/state_machine/learner.py`: `complete_lesson(..., completion_kind=None)`; decides N and the amount once; state
  `eligible_lesson_counts`, `completion_lessons`; `_award_xp(..., repeat_index=None, completion_kind=None)`.
- `src/ingestion/service.py`: both `complete_lesson` call sites pass `envelope["payload"].get("completion_kind")`.
- `src/schemas/events/xp_awarded.schema.json`: optional `repeat_index` (integer >= 0) and `completion_kind`.
- `pyproject.toml`: package-data for `rulesets_decay.json` and `src.abuse` `abuse_rules.json`.
- Docs: `README.md`, `docs/traceability-matrix.md` (M6 row + clearly marked clarification note below the AT list; AT wording
  untouched), `docs/problem-boundaries.md`, `docs/event-contracts.md`, `docs/metrics-framework.md` (pointer under 5.3).
- `tests/abuse/test_point_farming.py`: skipped placeholder replaced by real AT-6 tests.

New
- `src/state_machine/rulesets_decay.json` (v2). `src/abuse/`: `abuse_rules.json`, `rules.py`, `detectors.py`, `store.py`,
  `service.py`, `guardrail.py`, `evaluation.py`.
- Tests: `tests/state_machine/test_xp_decay.py`, `tests/abuse/{test_abuse_rules,test_detectors,test_signal_store_and_service,test_guardrail,test_evaluation}.py`,
  helpers `tests/abuse/{harness,fixtures,run_evaluation}.py`.
- Docs: `docs/abuse-detection.md`, `docs/adr/0006-decay-in-reward-path.md`, this file.

## Before / After
| | Before | After |
|---|---|---|
| Suite | 421 passed, 4 skipped, 1 deselected | 556 passed, 3 skipped, 1 deselected |
| Default rule sets | `v1` | `v1` (same digest `ba6d0a38...4430`); `v2` opt-in |
| XP on a repeat lesson | flat 100 | `v1` flat 100; `v2` 100/50/30/10/0 |
| `XPAwarded` fields | reward_id, completion_id, xp_amount, rule_version | + optional repeat_index, completion_kind (old streams replay) |
| Abuse detection | none (`src/abuse` empty) | rules, detectors, store, service, guardrail, evaluation |
| `test_point_farming` | skipped | 3 real tests |

## Tests (new and changed)
Changed: only `tests/abuse/test_point_farming.py` (the permitted placeholder). No other existing test touched.
New (counts from the final run): `test_xp_decay.py` 34, `test_abuse_rules.py` 25, `test_detectors.py` 34,
`test_signal_store_and_service.py` 14, `test_guardrail.py` 13, `test_evaluation.py` 12, `test_point_farming.py` 3 = 135.
Cover: AT-6 numbers, eligible-kind counting, v1 identical, replay and old-shape streams, v1 digest, tamper detection,
leaderboard sums decayed amounts, per-detector threshold boundaries (below/at/above), benign near-misses, determinism
and input-shuffle independence, evidence refs non-empty and resolvable, schema validation of every payload, never
`confirmed` (also a store CHECK), idempotent store, no side effects (event count, learner state, evidence rows,
leaderboard scores bit-identical), rebuild/reopen equality, shadow writes nothing, empty db, learner-order independence,
guardrail 15% boundary and UTC week handling, seeded persona evaluation.

Test-first record: for each step the new tests were run first and failed for the right reason (missing module/symbol:
`ImportError DECAY_RULESETS_FILE`, `ModuleNotFoundError src.abuse.rules`, `src.abuse.detectors`, `src.abuse.service`,
`src.abuse.guardrail`). Exception, stated honestly: for Phase 5 I wrote `fixtures.py`/`evaluation.py` and ran them once
to see the table before writing `test_evaluation.py`; the thresholds were not changed afterwards, and the tests include a
"dead detector would fail" check and a different-seed check.
Mistakes found while going green (all in my new tests, not product code): wrong "earliest evidence" expectation, a farming
near-miss that legitimately tripped the metronome rule, and a notification-log `limit` above `section_size` in a helper.

## Exact commands run with real output
Baseline:
```
$ python -m pytest -q
421 passed, 4 skipped, 1 deselected in 10.84s
```
After Phase 1 first attempt:
```
1 failed, 453 passed, 4 skipped, 1 deselected in 10.78s
FAILED tests/ingestion/test_provenance.py::test_old_shape_lesson_completed_event_still_replays
```
After the fix (and after later phases):
```
454 passed, 4 skipped, 1 deselected   (end of Phase 1)
514 passed, 4 skipped, 1 deselected   (end of Phase 2)
528 passed, 4 skipped, 1 deselected   (end of Phase 3)
556 passed, 3 skipped, 1 deselected in 16.29s   (final)
```
Determinism:
```
$ python -m pytest tests/abuse tests/state_machine/test_xp_decay.py -q          -> 135 passed in 5.54s
$ python -m pytest tests/abuse tests/state_machine/test_xp_decay.py -q -p no:randomly -> 135 passed in 5.44s
```
(`pytest-randomly` is not installed, so `-p no:randomly` is a no-op.)
Packaging (run on a copy of the tree under the scratchpad, because the integration test runs `pip wheel .` in the repo and
would write to the forbidden `build/` and `*.egg-info`):
```
$ python -m pytest -m integration -q          (in the copy)   -> 1 passed, 559 deselected in 4.81s
$ pip wheel . --no-deps --no-build-isolation; venv; pip install wheel; python -I (outside checkout)
src.__file__ -> .../v/lib/python3.14/site-packages/src/__init__.py
abuse rules digest d9b25324f8e5, decay rule sets ['v2']
register_decay_rulesets() -> ['v2'], xp_for_completion v2 N=0..4 -> [100, 50, 30, 10, 0], RULESETS -> ['v1', 'v2'] (after opt-in)
schemas ok
```
Evaluation (`python -m tests.abuse.run_evaluation`, seed 20261026):

| signal type | TP | FP | FN | precision | recall |
|---|---|---|---|---|---|
| replay | 1 | 0 | 0 | 1.00 | 1.00 |
| impossible_timing | 1 | 0 | 0 | 1.00 | 1.00 |
| scripted_farming | 2 | 0 | 0 | 1.00 | 1.00 |
| duplicate_exploit | 2 | 0 | 0 | 1.00 | 1.00 |

- benign false positives: 0 of 4 benign personas
- designed attackers fully detected: 6 of 6
- evidence-backed rate: 1.00 (7 signals)
- detection latency: 0.009 s for 477 stored events (12 learners); informational, not a pass/fail number

| borderline persona | ground truth | detected | labeled outcome reproduced |
|---|---|---|---|
| perfectly_regular_human | none | scripted_farming | yes |
| slow_bot_with_jitter | scripted_farming | none | yes |

## Assumptions
1. `v2` placement: see Blocker. Opt-in registration keeps `RULESETS == {"v1"}` by default.
2. `completion_kind` and `repeat_index` are recorded on `XPAwarded`, not `LessonCompleted` (existing test pins its shape).
   Detectors therefore read the kind from the award; a completion with no award has kind None (treated as a lesson).
3. `repeat_index` is always recorded (also under `v1`, where nothing decays) so repeat detection and the guardrail work
   on the default rule set. This adds the key to newly stored `v1` `XPAwarded` events; XP, order and event counts are unchanged.
4. `review`/`remediation` record `repeat_index = 0` (no repeat position), so their full-price XP never counts as repeat XP.
5. Explicit `xp_amount=` passed to `complete_lesson` still overrides the rule (existing behaviour); N is still recorded.
6. Window comparisons are inclusive (<=) and "short gap" is strictly below the minimum, as the brief's boundary wording implies.
7. `duplicate_exploit` counts distinct resubmission `event_id`s (the brief's persona is one completion_id under 4 new
   event_ids, which a count of distinct completion_ids could never reach 3 with).
8. One signal per (learner, rule id) per run; evidence is the first qualifying run of exactly the threshold count (the earliest
   row for the any-row rules), so later data does not change an existing signal id.
9. `detected_at` defaults to the latest fact time scanned, never a clock; callers may pass their own.
10. Store `status` is CHECK-constrained to `suspected`; allowing `confirmed` later needs a migration (intentional).
11. Guardrail: cohorts come from the passed mapping (all reported, zero XP not breached); unmapped learners are ignored;
    comparison is exact (`Fraction`), so 15/100 is not a breach.
12. Persona "bursty human" uses 90-180 s gaps, which makes six lessons span a little over ten minutes; the gap range was kept.
13. Learner ids are random UUIDs, so signal ids differ between independent fixture builds; "identical across two runs" compares
    persona-normalised signatures (type, rule, evidence refs, window).
14. Packaging was verified on a copy of the tree to avoid touching `build/` and `*.egg-info`.

## Uncertainty / known limitations
See `docs/abuse-detection.md` (Limitations). Key points: `occurred_at` is producer-claimed; no duration field; the evidence
index collapses identical redeliveries; thresholds unvalidated on real data; no confirmation or XP correction; bots with
jitter evade; perfectly regular humans are flagged; shared accounts and league manipulation are out of scope; detection is
a whole-log batch pass without a cursor; `v2` decay needs deployments to opt in. The evaluation is 12 synthetic personas, so
its 1.00 figures say little about real precision or recall.

## Open questions for the human
1. **Move `v2` into `rulesets.json`?** Requires editing two existing tests: `test_packaged_file_loads_exact_v1`
   (`list(loaded) == ["v1"]`) and `test_unregistered_version_cannot_be_configured_after_teardown` (`"v2" not in RULESETS`;
   switching it to an unregistered name such as `"v9"` keeps its intent). I did not touch them.
2. Should repeat_index be recorded for `v1` events (current choice) or only when decay is configured?
3. Is "distinct resubmission event_ids" the intended meaning of `min_distinct_resubmitted_completions`?
4. Who owns the future "confirm a signal" / XP correction action and its authorization model?
5. Real-data calibration of every threshold; the 15% share and the 50% / 20 XP schedule were taken from AT-6 as given.
6. Not verified: nothing was run against a non-synthetic database; CI workflow not run; `scripts/capture_evidence.sh` deliberately not run.

## Requirement checklist (sections 3-7)
| Requirement | Proved by |
|---|---|
| AT-6 numbers 100/50/30/10/0 | `test_xp_decay.py::test_at6_decay_numbers`, `test_aggregate_applies_at6_sequence`, `test_point_farming.py` |
| N counts only eligible kinds; review/remediation exempt | `test_review_and_remediation_neither_decay_nor_advance_n`, `test_n_counts_only_eligible_kinds_and_lessons_are_independent` |
| v1 byte-identical behaviour; v1 digest unchanged | `test_v1_events_unchanged_for_repeats`, `test_v1_digest_unchanged_and_v2_not_in_default_registry`, existing `test_v1_digest_is_golden` |
| Replay of v2 history; old-style streams; no recompute | `test_replay_of_v2_history_rebuilds_identical_state`, `test_replay_never_recomputes_xp_when_rules_change`, `test_old_style_events_without_kind_or_repeat_index_still_replay` |
| Tampered decay raises RuleConfigError; unknown keys rejected | `test_tampered_decay_values_without_new_digest_are_rejected`, `test_unknown_keys_are_still_rejected` |
| Independent N per lesson | `test_n_counts_only_eligible_kinds_and_lessons_are_independent` |
| Both ingestion call sites pass the kind | `test_ingestion_passes_completion_kind_and_decays` (first-attempt path); retry path changed identically, not separately exercised (no test forces the IntegrityError retry with a kind) |
| Leaderboard sums decayed amounts | `test_leaderboard_sums_the_decayed_amount` |
| Rules loader strict/pinned/immutable | `test_abuse_rules.py` |
| Detector boundaries, near-misses, determinism, exemptions, evidence, never confirmed | `test_detectors.py` |
| Store idempotent; service side-effect free; rebuild equal; shadow; empty db; order independence | `test_signal_store_and_service.py` |
| Guardrail 15% boundary, isolation, null repeat_index | `test_guardrail.py` |
| Personas, 0 benign FP, recall 1.0, evidence 100%, borderline as labeled, repeatable | `test_evaluation.py` |
| Docs, ADR 0006, README, traceability, packaging | files listed under Changes; packaging commands above |
| `v2` literally in `rulesets.json` | **not done** - see Blocker / Open question 1 |
