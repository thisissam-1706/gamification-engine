# Week 4 pilot 5: results of the simulated exp_007 pilot (rerun after exposure-order change)

Synthetic data only; nothing here demonstrates real-world causality. Model not changed or tuned. This replaces the earlier pilot-5 results, which were generated before the pilot A2 change (reminders on days 1-3; exposure only counts if it precedes the would-be attempt).

## Commands and output

Whole suite (plain `python` has no pytest, so the project venv was used):

    .venv/bin/python -m pytest -q

Full output:

```
ss........s............................................................. [ 16%]
.....................................................................s.. [ 33%]
........................................................................ [ 50%]
........................................................................ [ 67%]
........................................................................ [ 84%]
.................................................................        [100%]
421 passed, 4 skipped, 1 deselected in 10.71s
```

Result: **421 passed, 4 skipped, 1 deselected, 0 failed.** No failures to list; the 4 skips and 1 deselected were not investigated.

Pilot runs (plain `python`, repo root; each printed nothing and exited 0):

    python -m simulator.run_pilot --setting null --seed 7
    python -m simulator.run_pilot --setting positive --seed 7
    python -m simulator.run_pilot --setting harmful --seed 7
    python -m simulator.run_pilot --setting cap_stress --seed 7
    python -m simulator.run_pilot --setting null --seeds 1-20
    python -m simulator.run_pilot --setting positive --seeds 1-20
    python -m simulator.run_pilot --setting harmful --seeds 1-20
    python -m simulator.run_pilot --setting cap_stress --seeds 1-20

Outputs: `docs/evidence/pilot/` (n=4000 default; 84 entries, about 457 MB). The 20-seed runs took about 45 s together.

## Table 1: 20-seed summaries (seeds 1-20, n=4000)

| setting | mean diff | min | max | conclusive fraction | mean exposure rate (T) | missing-outcome rate T | missing-outcome rate C | cap-breach learners (T, total) | opt-outs T | opt-outs C | skipped seeds | SRM alerts |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| null | 0.0085 | -0.0198 | 0.0281 | 0.0000 | 0.6978 | 0.3000 | 0.3048 | 0 | 863 | 0 | [] | 0 |
| positive | 0.0510 | 0.0229 | 0.0782 | 0.8000 | 0.6978 | 0.2687 | 0.3048 | 0 | 863 | 0 | [] | 0 |
| harmful | -0.0307 | -0.0560 | -0.0072 | 0.3500 | 0.6978 | 0.3314 | 0.3048 | 0 | 4208 | 0 | [] | 0 |
| cap_stress | 0.0085 | -0.0198 | 0.0281 | 0.0000 | 0.6984 | 0.3000 | 0.3048 | 26201 | 20370 | 0 | [] | 0 |

Per-seed differences:

- null: +0.008, +0.009, -0.013, +0.000, -0.003, +0.026, +0.009, +0.014, +0.023, +0.011, +0.016, -0.011, -0.010, +0.028, +0.002, +0.021, -0.020, +0.028, +0.019, +0.013
- positive: +0.046, +0.055, +0.024, +0.046, +0.048, +0.069, +0.056, +0.052, +0.062, +0.049, +0.060, +0.035, +0.036, +0.065, +0.043, +0.062, +0.023, +0.078, +0.058, +0.055
- harmful: -0.045, -0.036, -0.045, -0.039, -0.037, -0.017, -0.033, -0.032, -0.018, -0.029, -0.022, -0.049, -0.046, -0.007, -0.026, -0.025, -0.056, -0.021, -0.013, -0.018
- cap_stress: +0.008, +0.009, -0.013, +0.000, -0.003, +0.026, +0.009, +0.014, +0.023, +0.011, +0.016, -0.011, -0.010, +0.028, +0.002, +0.021, -0.020, +0.028, +0.019, +0.013

## Table 2: seed 7 (n=4000), treatment (T) / control (C)

| item (T / C) | null | positive | harmful | cap_stress |
|---|---|---|---|---|
| **Funnel (learners)** | | | | |
| assigned | 2031 / 1969 | 2031 / 1969 | 2031 / 1969 | 2031 / 1969 |
| excluded_tenure | 192 / 170 | 192 / 170 | 192 / 170 | 192 / 170 |
| excluded_consent | 189 / 159 | 189 / 159 | 189 / 159 | 189 / 159 |
| eligible | 1650 / 1640 | 1650 / 1640 | 1650 / 1640 | 1650 / 1640 |
| decided_send_reminders | 1650 / 0 | 1650 / 0 | 1650 / 0 | 1650 / 0 |
| decided_no_action | 0 / 1640 | 0 / 1640 | 0 / 1640 | 0 / 1640 |
| any_message_sent | 1650 / 0 | 1650 / 0 | 1650 / 0 | 1650 / 0 |
| any_message_delivered | 1649 / 0 | 1649 / 0 | 1649 / 0 | 1650 / 0 |
| exposed | 1158 / 0 | 1158 / 0 | 1158 / 0 | 1159 / 0 |
| exposed_before_attempt | 1083 / 0 | 1083 / 0 | 1083 / 0 | 1091 / 0 |
| exposed_after_attempt | 75 / 0 | 75 / 0 | 75 / 0 | 68 / 0 |
| mature | 1360 / 1363 | 1360 / 1363 | 1360 / 1363 | 1360 / 1363 |
| immature | 290 / 277 | 290 / 277 | 290 / 277 | 290 / 277 |
| attempted | 993 / 983 | 1030 / 983 | 949 / 983 | 993 / 983 |
| completion_logged | 935 / 937 | 972 / 937 | 895 / 937 | 935 / 937 |
| mastered | 559 / 548 | 623 / 548 | 502 / 548 | 559 / 548 |
| **Fix B buckets** | | | | |
| buckets.mature_with_outcome | 935 / 937 | 972 / 937 | 895 / 937 | 935 / 937 |
| buckets.mature_no_outcome | 425 / 426 | 388 / 426 | 465 / 426 | 425 / 426 |
| buckets.missing_no_attempt | 367 / 380 | 330 / 380 | 411 / 380 | 367 / 380 |
| buckets.missing_attempt_no_completion | 58 / 46 | 58 / 46 | 54 / 46 | 58 / 46 |
| buckets.mature_invalid | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| buckets.immature | 290 / 277 | 290 / 277 | 290 / 277 | 290 / 277 |
| buckets.headline_denominator | 1360 / 1363 | 1360 / 1363 | 1360 / 1363 | 1360 / 1363 |
| headline_rate | 0.4110 / 0.4021 | 0.4581 / 0.4021 | 0.3691 / 0.4021 | 0.4110 / 0.4021 |
| **ledger_summary** | | | | |
| ledger.conflicting | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| ledger.excluded | 381 / 329 | 381 / 329 | 381 / 329 | 381 / 329 |
| ledger.failed | 376 / 389 | 349 / 389 | 393 / 389 | 376 / 389 |
| ledger.immature | 290 / 277 | 290 / 277 | 290 / 277 | 290 / 277 |
| ledger.invalid_ordering | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| ledger.mastered | 559 / 548 | 623 / 548 | 502 / 548 | 559 / 548 |
| ledger.missing_no_attempt | 367 / 380 | 330 / 380 | 411 / 380 | 367 / 380 |
| ledger.missing_no_completion | 58 / 46 | 58 / 46 | 54 / 46 | 58 / 46 |
| **Delivery** | | | | |
| messages_sent | 4901 / 0 | 4901 / 0 | 4731 / 0 | 22597 / 0 |
| delivered | 4407 / 0 | 4407 / 0 | 4253 / 0 | 20320 / 0 |
| provider_failed | 494 / 0 | 494 / 0 | 478 / 0 | 2277 / 0 |
| suppressed_cap | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| suppressed_consent | 49 / 0 | 49 / 0 | 219 / 0 | 12053 / 0 |
| opt_outs | 42 / 0 | 42 / 0 | 214 / 0 | 1056 / 0 |
| max_sent_in_24h | 1 / 0 | 1 / 0 | 1 / 0 | 7 / 0 |
| cap_breach_learners | 0 / 0 | 0 / 0 | 0 / 0 | 1322 / 0 |
| **Contrast / SRM** | | | | |
| difference (T-C) | 0.0090 | 0.0560 | -0.0329 | 0.0090 |
| 95% CI | [-0.0279, 0.0459] | [0.0189, 0.0932] | [-0.0695, 0.0036] | [-0.0279, 0.0459] |
| conclusive | False | True | False | False |
| SRM p (assigned), alpha | 0.3269, 0.001 | 0.3269, 0.001 | 0.3269, 0.001 | 0.3269, 0.001 |
| SRM p (eligible), alpha | 0.8616, 0.001 | 0.8616, 0.001 | 0.8616, 0.001 | 0.8616, 0.001 |

`exposed_before_attempt` / `exposed_after_attempt` come from the exposure record flag (first delivered reminder <= would-be attempt time), counted over eligible learners; they sum to `exposed`.


## Against the expected pattern (reported, model not changed)
- **null:** mean +0.0085, range -0.020 to +0.028, 0 of 20 conclusive. Near 0, no conclusive seeds, as expected. The small positive mean is arm-to-arm
  sampling noise (effect is 0.0 and each arm has different learners); the same offset appears in every setting because of the common random numbers.
- **positive: +0.051 (raw), higher than the expected "slightly below +0.04".** 16 of 20 seeds conclusive (0.80). Net of the null offset it is about +0.042.
  Explanation (arithmetic from the model, not tested): the effect applies on two channels (attempt and pass probability, +0.05 each), so it compounds; and the
  early-attempter dilution is small, because attempts fall on days 1-13 while the first reminder is day 1 at 09:00, so only attempts on day 1 before 09:00 beat it
  (75 of 1158 exposed treatment learners at seed 7, 6.5%). Compared with the previous run (no ordering rule, +0.054) the ordering rule lowered the mean by only about 0.003.
- **harmful: -0.031 (raw), slightly above -0.04 as expected;** net of the null offset about -0.039. 7 of 20 seeds conclusive (0.35). Opt-outs are about 4.9x null (4208 vs 863, 20-seed totals).
- **cap_stress:** treatment has cap breaches (26201 learner-seeds in total; at seed 7, 1322 learners, max 7 sent in 24h) and control has none. Its difference equals null's (effect 0.0, same draws), as expected.
  `suppressed_cap` is 0 because the cap is not enforced in this setting.
- Mean exposure rate (treatment) is identical in null, positive and harmful (0.6978; delivery does not depend on effect) and 0.6984 in cap_stress. Control is never messaged or exposed in any setting.
- No SRM alerts in any run (seed 7: p = 0.327 assigned, 0.862 eligible, alpha 0.001).

## Other observations
- `docs/evidence/pilot/` is about 457 MB (84 entries, 9 files per run). Check before adding it to git.
- Single-seed conclusions are unstable (harmful: 7 of 20 conclusive; seed 7 harmful CI [-0.070, 0.004] is not conclusive).
- Missing-outcome rate is about 0.30 in both arms, mostly mature learners with no attempt, who count as not mastered.
- Delivery counts match the earlier (pre-A2) run: moving reminders from day 0 to day 1+ shifts times but not statuses.

## Assumptions
- `python -m simulator.run_pilot` run with plain `python` (miniconda, stdlib only); pytest with `.venv/bin/python`.
- Default n=4000 and `--out docs/evidence/pilot`, as the commands give no other values.
- `simulator/SPEC.md` already had the new Status line and Not implemented list from the earlier pilot-5 run; its first Limits bullet was replaced in pilot A2 with the exposure-order wording.
  I did not revert it to "exposure at any time affects outcome", which is no longer true of the model.

## Uncertainty
- 20 seeds is a small sample; conclusive fractions are imprecise.
- The explanation of positive > 0.04 is reasoning from the model definition, not an experiment (no single-channel run).
- The 4 skipped and 1 deselected tests were not investigated.
- No git commands that change state were run.
