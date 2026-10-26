# Week 5 M6 gap fixes

## Step: abuse rule set v2 (config only, no detector changes)

### Changes
- `src/abuse/abuse_rules.json`: added `v2` = v1's content + `review_misuse` {max_reviews_per_lesson 3, window_hours 24,
  orphan_min_lessons 3}, `receive_burst` {max_completions 40, window_minutes 10}, `future_dated` {tolerance_minutes 5,
  min_completions 3}. v2 sha256 (`24ff24b1...a4dbf0ad`) was computed with the loader's `content_digest`, not by hand.
  v1 is untouched (`d9b25324...adbff`).
- `src/abuse/rules.py`: dataclasses `ReviewMisuseRules`, `ReceiveBurstRules`, `FutureDatedRules`; optional fields on `AbuseRules`
  (None when absent); they join `content()`/digest only when present; strict parsing (exact keys, positive non-bool ints,
  unknown/missing keys rejected); `DEFAULT_ABUSE_RULES_VERSION = "v2"` defined once and used by `get_abuse_rules`,
  `AbuseDetectionService` and `cohort_gaming_report` defaults (the three places found by grep in `src`).
- `tests/abuse/test_abuse_rules_v2.py`: new.
- `tests/abuse/test_signal_store_and_service.py` line 98: one assertion changed (see Tests).

### Before/After
- Default abuse rules version: "v1" hard-coded in three places -> one constant, "v2".
- Registry: {v1} -> {v1, v2}. Detectors untouched; they ignore the new sections, so v1 and v2 give identical signals
  (different ids, since ids include the rule digest).

### Tests
- New `test_abuse_rules_v2.py`: 62 tests. Red run: collection `ImportError: cannot import name 'DEFAULT_ABUSE_RULES_VERSION'`; green after implementing.
- Existing assertion edited (it pinned the default version): `test_store_rows_carry_every_field_and_a_passed_in_detected_at`,
  `"v1"` -> `"v2"` for `row.rule_version` of a service run that uses the default rules.
- NOT edited, needs your decision: `tests/abuse/test_abuse_rules.py::test_packaged_v1_has_the_specified_defaults` asserts
  `list(ABUSE_RULES) == ["v1"]`. That pins the registry contents, not the default version/digest, so it is outside the
  permitted edit and currently fails.

### Exact commands and real output
```
$ python -m pytest tests/abuse/test_abuse_rules_v2.py -q   (before implementing)
E   ImportError: cannot import name 'DEFAULT_ABUSE_RULES_VERSION' from 'src.abuse.rules'
1 error in 0.08s
$ python -m pytest tests/abuse/test_abuse_rules_v2.py -q   (after)
62 passed in 0.18s
$ python -m pytest -q   (after v2, before the one permitted edit)
FAILED tests/abuse/test_abuse_rules.py::test_packaged_v1_has_the_specified_defaults
FAILED tests/abuse/test_signal_store_and_service.py::test_store_rows_carry_every_field_and_a_passed_in_detected_at
2 failed, 616 passed, 3 skipped, 1 deselected in 15.74s
$ python -m pytest -q   (after the permitted edit)
1 failed, 617 passed, 3 skipped, 1 deselected
```

### Assumptions
- Default for `AbuseDetectionService` and the guardrail follows the new constant, as asked ("everywhere you found").
- New-section values are all positive ints; there is no upper bound validation.
- Docs (`docs/abuse-detection.md`) still describe v1 only; not updated in this step.

### Uncertainty
- The remaining failure is a registry-pin assertion; I stopped there rather than edit it.
- The new sections are config only; no detector reads them yet.

## Step: detect misuse of the review/remediation exemption

### Changes
- `src/abuse/detectors.py`: new `detect_review_misuse(rules, completions)`, called from `detect_all`. Two rules, both
  `signal_type="scripted_farming"`, driven by `rules.review_misuse` (returns nothing when that section is None, i.e. v1):
  - `scripted_farming.orphan_review`: distinct lessons with an exempt-kind (review/remediation) completion and no earlier
    lesson/None-kind completion of that lesson (earlier = (occurred_at, completion_id)); flags at >= `orphan_min_lessons`.
    Evidence: the first `orphan_min_lessons` orphan completions by time.
  - `scripted_farming.review_repeat`: more than `max_reviews_per_lesson` exempt completions of one lesson within
    `window_hours` (inclusive). Evidence: the first qualifying run (max+1 completions), one signal per lesson.
  `detect_scripted_farming`, decay and XP code are untouched.
- `tests/abuse/test_review_misuse.py`: new (18 tests).

### Before/After
- Before: 15 reviews of one lesson paid 1500 XP and raised no signal. After: under the default rules (v2) that produces a
  `review_repeat` suspected signal; XP, state and event count are unchanged by running detection.
- Under v1 rules nothing new is produced.

### Tests
- Red run: collection `ImportError: cannot import name 'detect_review_misuse' from 'src.abuse.detectors'`; then 18 passed.
- Covers: orphan boundary (3 flags, 2 not) for review and remediation; review after a lesson not orphan; later lesson does
  not rescue; kind None counts as lesson; review_repeat 4 in 24h flags, 3 does not, >24h does not, lessons independent,
  normal completions not counted; v1 off; empty input; naive timestamps as UTC; identical timestamps (ordered by
  completion_id); shuffle-stable; schema-valid payloads; evidence refs only `completion:`/`event:`; end-to-end 15 reviews
  40-200 s apart via harness with XP ruleset v2 registered (flag-only: xp_total, aggregate version and event count equal
  before/after); benign `spaced_repetition_reviewer` persona yields no signals. No existing test edited.

### Exact commands and real output
```
$ python -m pytest tests/abuse/test_review_misuse.py -q   (before implementing)
E   ImportError: cannot import name 'detect_review_misuse' from 'src.abuse.detectors'
1 error in 0.14s
$ python -m pytest tests/abuse/test_review_misuse.py -q   (after)
18 passed in 0.27s
$ python -m pytest -q
FAILED tests/abuse/test_abuse_rules.py::test_packaged_v1_has_the_specified_defaults   (known, from the previous step: pins list(ABUSE_RULES) == ["v1"])
1 failed, 635 passed, 3 skipped, 1 deselected in 16.99s
```

### Assumptions
- Orphan evidence uses the earliest orphan completion per lesson; one orphan signal per learner per run.
- `review_repeat` counts review and remediation together per lesson.
- The persona-evaluation table is unchanged (checked: the evaluation test file still passes).

### Uncertainty
- A learner whose lesson-kind completion was rejected or happened before the data window will look orphaned; a legitimate
  review of a lesson learned elsewhere (e.g. imported history) can false-positive at 3 lessons.
- Thresholds (3 / 24 h / 3) are placeholders validated only on synthetic data.
- `docs/abuse-detection.md` does not yet describe these rules.

## Step: timing rules on the trusted receive time

### Changes
- `src/abuse/detectors.py`: `CompletionFact` gets a last optional field `received_at: datetime | None = None` (old
  constructions unchanged). New `detect_receive_timing(rules, completions)`, called from `detect_all`; both rules have
  `signal_type="impossible_timing"` and are off when their section is None (v1); completions without `received_at` are ignored:
  - `impossible_timing.receive_burst`: more than `max_completions` completions received within `window_minutes` (inclusive).
    Evidence: the first max+1 completions by receive time.
  - `impossible_timing.future_dated`: completions with `occurred_at` strictly later than `received_at + tolerance_minutes`;
    flags at >= `min_completions`. Evidence: the first `min_completions` by claimed time.
- `src/abuse/service.py` (`completion_facts`): fills `received_at` from `evidence_store.delivery_binding(event_id).received_at`
  (parsed like the other times); None when there is no event id, no binding, or no received_at.
- `tests/abuse/test_receive_timing.py`: new (15 tests). No detector other than the new one changed.

### Before/After
- Before: 60 completions claimed 3-9 min apart but delivered 2 s apart (30 h after the first claimed time) earned 6000 XP with
  no signal. After (default rules v2): one `receive_burst` suspected signal; v1 rules still give nothing; XP unchanged.

### Tests
- Red run: collection `ImportError: cannot import name 'detect_receive_timing' from 'src.abuse.detectors'`; then 15 passed.
- Covers: field order/default; burst 41 flags and 40 does not; inclusive window boundary; None received_at ignored; learners
  independent; honest offline sync (10 received in 1 min, claimed 5 min apart) not flagged; future_dated 3 at +6 min flags, 2
  does not, exactly +5 min does not count; v1 off; empty input; naive timestamps as UTC; shuffle-stable; included in `detect_all`;
  schema-valid payloads and non-empty evidence; service fills/None received_at; end-to-end through the harness with
  `random.Random(3)` (old rule set v1 finds nothing, default v2 finds only `receive_burst`, XP stays 6000).

### Exact commands and real output
```
$ python -m pytest tests/abuse/test_receive_timing.py -q   (before implementing)
E   ImportError: cannot import name 'detect_receive_timing' from 'src.abuse.detectors'
1 error in 0.13s
$ python -m pytest tests/abuse/test_receive_timing.py -q   (after)
15 passed in 0.44s
$ python -m pytest -q
FAILED tests/abuse/test_abuse_rules.py::test_packaged_v1_has_the_specified_defaults   (known since the v2-rules step; pins list(ABUSE_RULES) == ["v1"])
1 failed, 650 passed, 3 skipped, 1 deselected in 16.85s
$ grep -n "datetime.now\|time.time" src/abuse/*.py     -> no matches
```

### Assumptions
- The end-to-end "old rules raise nothing" is checked by running the v1 rule set (which has only the old rules) plus checking
  that v2 raises nothing besides `receive_burst`.
- `received_at` is read per completion from the delivery binding (one lookup per accepted completion).
- Evaluation table unchanged (12 personas still 0 benign false positives, 6/6 attackers).

### Uncertainty
- A receive burst cannot tell a spoofed timestamp from a very large honest offline backlog. The threshold (more than 40
  completions in 10 minutes) is deliberately high to keep honest sync from being flagged, which also means slower
  spoofing slips through. The signal is only "suspected" and nothing is acted on.
- `future_dated` depends on server clock accuracy; a skewed `IngestConfig.clock` would flag honest clients or hide spoofed ones.
- Rows migrated from before `received_at` was recorded have no receive time and are ignored by both rules.
- The threshold values are placeholders validated only on synthetic data; `docs/abuse-detection.md` is not yet updated.


## Step: extended evaluation and docs for the two gap fixes

### Changes
- `tests/abuse/fixtures.py`: four personas added to the code and to `PERSONAS` (same style and seeds, XP rules stay v1 since
  review/lesson XP are full price either way): `review_label_spoofer` (ATTACKER, scripted_farming), `orphan_reviewer`
  (ATTACKER, scripted_farming), `timestamp_spoofer` (ATTACKER, impossible_timing), `honest_offline_sync` (BENIGN, nothing).
- `tests/abuse/test_evaluation.py`: count/persona-set assertions updated and three new tests added (see Tests).
- `docs/abuse-detection.md`: new rules and thresholds table (abuse rules v2), "Gaps found and fixed" section, evaluation
  table and persona text, version mentions (abuse rules default v2; XP v1/v2 disambiguated), extra Limitations.
  README untouched in this step.

### Before/After
- Personas 12 -> 16 (benign 4 -> 5, attackers 6 -> 9, borderline 2). Events 477 -> 751, signals 7 -> 10.
- Precision/recall still 1.00 on every type; 0 of 5 benign false positives; 9 of 9 attackers detected; borderline unchanged.

### Tests
- Red run (after updating the expected counts, before adding personas): 4 failed, 11 passed:
  `test_every_persona_is_present_and_labeled`, `test_no_false_positives_on_benign_personas`,
  `test_every_designed_attacker_is_detected_with_its_type`, `test_gap_fix_personas_are_caught_by_their_own_rules`
  (`KeyError: 'review_label_spoofer'`). After adding personas all pass.
- New: `test_gap_fix_personas_are_caught_by_their_own_rules` (review_repeat / orphan_review / receive_burst respectively),
  `test_honest_offline_sync_is_not_flagged`, `test_the_gap_fix_attackers_evade_the_original_v1_rules` (shadow run with abuse
  rules v1: none of the three spoofers is flagged).
- Existing assertions changed in this step (counts / persona sets only):
  1. `ATTACKER_TYPES`: +3 entries.
  2. `len(PERSONAS) == len(labels) == 12` -> `16`.
  3. benign persona set: + `honest_offline_sync`.
  4. `benign_learners == 4` -> `5`.
  5. `attacker_learners == 6 and attackers_fully_detected == 6` -> `9` and `9`.

### Exact commands and real output
```
$ python -m tests.abuse.run_evaluation
| signal type | TP | FP | FN | precision | recall |
|---|---|---|---|---|---|
| replay | 1 | 0 | 0 | 1.00 | 1.00 |
| impossible_timing | 2 | 0 | 0 | 1.00 | 1.00 |
| scripted_farming | 4 | 0 | 0 | 1.00 | 1.00 |
| duplicate_exploit | 2 | 0 | 0 | 1.00 | 1.00 |

- benign false positives: 0 of 5 benign personas
- designed attackers fully detected: 9 of 9
- evidence-backed rate: 1.00 (10 signals)
- detection latency: 0.017 s for 751 stored events (16 learners); informational, not a pass/fail number

| borderline persona | ground truth | detected | labeled outcome reproduced |
|---|---|---|---|
| perfectly_regular_human | none | scripted_farming | yes |
| slow_bot_with_jitter | scripted_farming | none | yes |
$ python -m pytest -q
FAILED tests/abuse/test_abuse_rules.py::test_packaged_v1_has_the_specified_defaults
1 failed, 653 passed, 3 skipped, 1 deselected in 23.06s
```
Digests: abuse rules v1 `d9b25324f8e5578b644c579055e8414ddf658d5208cf7d2ee6b2ef10bd5adbff` (unchanged); XP rule set v1
`ba6d0a3821ebdb1358840ec7a0dd2f67136b4eeb3062d79f3369b53cda6b4430` (unchanged). Read-only `git status`/`git diff --name-only`:
nothing under `build/`, `simulator/`, `docs/evidence/` or `*.egg-info` changed.

### Every existing-test assertion changed across all four prompts
(Note: all `tests/abuse/*` files except `test_point_farming.py` are new in this work and untracked, so git cannot show these
as diffs.)
1. `tests/abuse/test_point_farming.py`: skipped placeholder replaced by real tests (permitted).
2. `tests/abuse/test_signal_store_and_service.py::test_store_rows_carry_every_field_and_a_passed_in_detected_at`:
   `row.rule_version` `"v1"` -> `"v2"` (default abuse rules version moved to v2).
3. `tests/abuse/test_evaluation.py`: the five count/set assertions listed above.
No test outside `tests/abuse/` was edited in any prompt. NOT edited and still failing:
`tests/abuse/test_abuse_rules.py::test_packaged_v1_has_the_specified_defaults` (`list(ABUSE_RULES) == ["v1"]`).

### Assumptions
- The new personas run under XP rules v1 (no decay registration needed); detection does not depend on XP amounts.
- `honest_offline_sync` delivers 3 h after the first claim, 5 s apart.

### Uncertainty
- All evaluation figures are synthetic: sixteen hand-built personas, thresholds tuned by design, not by data. 1.00 precision and
  recall mean the fixtures behave as designed, not that real abuse is caught.
- The suite is not fully green because of the registry-pin assertion above; fixing it needs one line edited in an existing
  test, which I did not do without your say.
