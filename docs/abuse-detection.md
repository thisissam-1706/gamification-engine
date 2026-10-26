# Abuse Detection (M6)

> **Status: implemented for synthetic fixtures, flag-only.** The thresholds are conservative placeholders
> checked only against seeded synthetic personas. Nothing here is validated on real learner data, and M6 is not
> claimed complete or validated.

## Purpose

Find behaviour that looks like reward abuse (replayed or resubmitted events, impossible completion speed,
scripted or repeated farming) and record it as evidence-backed **suspected** signals for a human to review. This
implements [ADR 0003](adr/0003-rule-based-abuse-detection.md) (rule-based, retroactive, asynchronous,
replay-compatible, evidence-traceable) and AT-6 together with [ADR 0006](adr/0006-decay-in-reward-path.md).

## Architecture

```
ingestion ──► event store (Learner.LessonCompleted, XPAwarded)      ┐
          └─► ingestion evidence (duplicates, rejections)           ├─► AbuseDetectionService.run()
                                                                    ┘        │ pure detectors (src/abuse/detectors.py)
                                                   AbuseSignalStore (abuse_signals) ◄─┘  (INSERT OR IGNORE)
```

- The detector pass is **separate from ingestion**. It runs after the fact and never inside `ingest_lesson_completed`.
- It reads stored facts through the application's notification log (the same public path the leaderboard
  projection consumes) and `EvidenceStore.list()`. It never writes to the event store or the evidence store.
- Detectors (`src/abuse/detectors.py`) are pure functions over frozen dataclasses (`CompletionFact`, `EvidenceFact`)
  with no database and no clock. Every threshold comes from `src/abuse/abuse_rules.json`.
- **Flag-only.** Detection never changes XP, streaks, badges, quests or leaderboards. Signals are always
  `suspected`; the store rejects any other status. `confirmed` and any XP correction need a future, explicitly
  authorized action.
- `run(shadow=True)` returns signals without writing, to trial other thresholds or rule versions
  (`compare_runs` diffs two shadow runs).

## Rules (`abuse_rules.json`, default version `v2`, SHA-256 pinned)

Windows are inclusive (a span exactly equal to the window counts). A "short" gap is strictly below the minimum.

| Signal type / rule id | Fires when | Thresholds (v1 and v2) | Rationale |
|---|---|---|---|
| `replay` / `replay.distinct_redelivery` | at least N distinct redelivered `event_id`s (`duplicate_event_id`) within the window | 5 events, 60 min | One redelivery is normal at-least-once behaviour. A burst of distinct replays is not. |
| `replay` / `replay.conflicting_reuse` | any `conflicting_event_id_reuse` row | on, 1 row | An event_id reused for different content is not a transport retry. |
| `duplicate_exploit` / `duplicate_exploit.resubmitted_completion` | at least N distinct new `event_id`s carrying an already accepted `completion_id` (`duplicate_completion_id`) within the window | 3 resubmissions, 60 min | A client retry or two is plausible; repeated fresh event_ids for one completion looks like reward re-claiming. |
| `duplicate_exploit` / `duplicate_exploit.reward_field` | any `reward_field_in_source_event` rejection | on, 1 row | A source event must never carry reward fields. |
| `impossible_timing` / `impossible_timing.short_gaps` | at least 3 short gaps whose span fits in the window | min gap lesson 20 s, review 10 s, remediation 20 s (default 20 s); 3 short gaps; 10 min | Two quick taps happen; a run of sub-minimum gaps does not. The later completion's kind picks the minimum. |
| `impossible_timing` / `impossible_timing.burst` | more than `max_completions` completions inside the window | 20 completions, 10 min | An absolute ceiling on throughput. |
| `scripted_farming` / `scripted_farming.repeat` | at least N completions with `repeat_index >= 1` (lesson kind) inside the window | 10 repeats, 24 h | Repeating finished lessons many times a day is the farming pattern decay targets. |
| `scripted_farming` / `scripted_farming.metronome` | at least N consecutive lesson-kind completions whose gaps have coefficient of variation (pstdev/mean) <= max_cv and mean gap >= minimum | 12 events, CV <= 0.05, mean >= 1 s | Humans vary; schedulers do not. |

The rules in the second table exist only in abuse rules **v2** (the default); `v1` is kept unchanged (same digest) and
simply has no such sections, so it runs only the original rules. The XP rule set `v2` (decay) is a different, unrelated `v2`.

| Signal type / rule id | Fires when | v2 thresholds | Rationale |
|---|---|---|---|
| `scripted_farming` / `scripted_farming.orphan_review` | review/remediation completions of at least N distinct lessons that have no earlier `lesson`/absent-kind completion of the same lesson | 3 lessons | A review of something never learned is a mislabel, not spaced repetition. A lesson completion after the review does not rescue it. |
| `scripted_farming` / `scripted_farming.review_repeat` | more than N review/remediation completions of one lesson inside the window | 3 per lesson, 24 h | Spaced repetition is spread over days; four reviews of one lesson within a day is farming through the exempt label. |
| `impossible_timing` / `impossible_timing.receive_burst` | more than N completions **received** (trusted `received_at`) inside the window | 40 completions, 10 min | Claimed times can be spoofed; the server's receive time cannot. The threshold is high so honest offline sync is not flagged. |
| `impossible_timing` / `impossible_timing.future_dated` | at least N completions claiming an `occurred_at` strictly later than `received_at` + tolerance | 3 completions, 5 min tolerance | A client cannot complete something in the future; a little clock skew is tolerated. |

Reviews and remediation are excluded from the repeat and metronome rules (spaced repetition repeats lessons by
design). `cohort_gaming_threshold` (0.15) is used by the guardrail below.

## Signal IDs and evidence

- `signal_id = abuse:{rule_id}:{rule_digest[:12]}:{learner_id}:{sha256(sorted evidence_refs)[:16]}`. It is deterministic:
  the same facts and rules always give the same id, so re-running is idempotent, and changed rule content gives
  new ids instead of rewriting old ones.
- `evidence_refs` are stable strings: `event:{event_id}`, `completion:{completion_id}`, `evidence:{evidence_id}`.
  A signal never has empty evidence. Where a window fires, the evidence is the first qualifying run (exactly the
  threshold count), which later data does not disturb.
- `Signal.to_payload()` is exactly the `abuse_signal_raised` schema payload (`signal_id`, `signal_type`, `status`,
  `evidence_refs`). Rule id, version, digest and detected window are stored beside it in `abuse_signals`.
- `detected_at` in the store is supplied by the caller; by default it is the latest fact time scanned, never a clock.

## XP decay (AT-6)

Decay is a reward rule, not a detector. Rule set `v2` adds `"decay": {"first_repeat_percent": 50, "step_xp": 20}`
to `v1`'s content. With `N` prior accepted `lesson`/absent-kind completions of the same lesson by the same
learner, XP is `E` for `N = 0` and `max(0, E*first_repeat_percent//100 - step_xp*(N-1))` after that: 100, 50, 30,
10, 0. `review` and `remediation` neither decay nor advance `N`. The amount and `N` are decided in
`Learner.complete_lesson` and stored on `XPAwarded` (`repeat_index`, `completion_kind`); replay never recomputes.
The XP rule set `v2` is opt-in (`register_decay_rulesets()`), and the XP rule set `v1` remains the default and is unchanged.

## Cohort gaming guardrail (metrics-framework 5.3, exp_007)

`cohort_gaming_report(awards, cohort_of, "YYYY-Www", rules)` returns per cohort the total XP, repeat XP (awards with
`repeat_index >= 1`), share, and `breached = share > 0.15` (exactly 15% is not a breach; zero XP gives share 0).
The ISO week is taken from `occurred_at` in UTC. "Decayed XP" is read as XP awarded on repeat completions, which
under `v2` is the already-decayed amount. It is a pure report; it is not wired into the experiment runner.

## Evaluation (seeded synthetic personas)

Sixteen personas (seed 20261026) are driven through the real ingestion path with an injected clock; five are benign,
nine are designed attackers and two are borderline. Measured by `python -m tests.abuse.run_evaluation`:

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

Benign: steady learner, bursty-but-human, spaced-repetition reviewer (including a 12 s review gap), flaky-network
learner (one redelivery, one event delivered 30 h late), honest offline sync (10 completions claimed 5 minutes apart, all received within a minute). Attackers: fast completer, metronomic bot, lesson
farmer, replay attacker, duplicate exploiter, reward-field injector, review-label spoofer (15 reviews of one lesson),
orphan reviewer (reviews of five never-learned lessons), timestamp spoofer (60 completions claimed 3-9 minutes apart,
received 2 s apart).

Borderline cases are documented, not tuned away:

- **slow bot with jitter** is an *expected miss*: a bot with a roughly two-minute cadence and random jitter,
  completing distinct lessons, trips none of the rules.
- **perfectly regular human** is an *expected false positive*: a person on a fixed 10-minute schedule looks
  like a metronome.

The tests assert 0 benign false positives, recall 1.0 on the designed attackers, an evidence-backed rate of 100%
(each ref is resolved against the stores, not the detector's own facts), the two borderline behaviours, and identical
results across two runs. These describe the synthetic fixtures only. Latency is reported for information and is only
bounded by a very generous sanity check.

## Gaps found and fixed

Two bypasses were found after the first version, both closed by flagging (nothing is blocked, **XP is still paid**):

1. **Review-label bypass.** `completion_kind="review"` (or `remediation`) is exempt from XP decay and from the repeat and
   metronome rules, so labelling 15 repeats of one lesson as reviews earned 1500 XP with no signal. `detect_review_misuse`
   now flags `orphan_review` and `review_repeat` (rules v2).
2. **Spoofed timestamps.** Every timing rule used the producer-claimed `occurred_at`. A client claiming gaps of 3-9 minutes
   but delivering 60 completions within two minutes earned 6000 XP with no signal. `detect_receive_timing` now flags
   `receive_burst` and `future_dated` using the trusted receive time (`delivery_ids.received_at`, set by the ingestion clock).

Both are flag-only: signals stay `suspected`, XP is not revoked, and the original fixtures' results are unchanged. Abuse
rules `v1` still lacks these checks by design (it is immutable); the default is now `v2`.

## How to run

```bash
python -m pytest tests/abuse
python -m tests.abuse.run_evaluation
```

```python
service = AbuseDetectionService(application, evidence_store, AbuseSignalStore(db_path), rules_version="v2")  # the default
summary = service.run()                 # stores new signals, returns a RunSummary
trial = service.run(shadow=True, rules_version=other_rules)   # writes nothing
```

## Limitations

- `occurred_at` is **producer-claimed**. A lying producer defeats every timing rule.
- The lesson-completed payload has **no duration field**, so speed is inferred from gaps between `occurred_at` values.
- The evidence index **collapses identical redeliveries** of the same event into one row, so repeats of one event
  cannot be counted; the replay rule counts distinct event_ids instead.
- `duplicate_exploit` counts distinct resubmission event_ids; a client that legitimately retries with fresh event_ids
  three times within an hour would be flagged.
- Thresholds are **unvalidated on real data**; the evaluation is synthetic and small. Precision and recall here are not
  estimates of real-world performance.
- **No automatic corrections.** Nothing is clawed back, nothing is confirmed. `confirmed` and XP corrections are a
  future authorized-action phase.
- Not covered: shared accounts, sandbagging or league manipulation, and bots that add jitter or spread repeats thin
  (see the borderline cases). The cadence rule will flag perfectly regular humans.
- A receive burst cannot separate spoofing from a very large honest offline backlog; the threshold is deliberately high,
  so slower spoofing slips through.
- `completion_kind` is still **producer-claimed**. The review rules catch the common misuse patterns, not every mislabel
  (for example a few reviews spread over days after a real completion).
- **Flagged XP is not revoked.** Detection only records `suspected` signals.
- `future_dated` relies on an accurate server clock.
- The guardrail reads "decayed XP" as repeat-completion XP, and decay only exists for deployments that opt in to `v2`.
- Detection is a batch pass over the whole notification log; there is no incremental cursor or scheduler yet.
