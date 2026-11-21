# 0006 - Decay Lives in the Reward Path; Abuse Detection Stays Flag-Only

**Status**: Accepted (updated 2026-11-21: rule set `v3`, XP hold)
**Date**: 2026-10-26

**Context**:
AT-6 asks for diminishing XP on repeated lessons (E = 100, step 20, 3rd completion earns 30) and for a flag when repeat XP exceeds 15% of a cohort's weekly XP. [ADR 0003](0003-rule-based-abuse-detection.md) fixed abuse detection as rule-based, retroactive, asynchronous and replay-compatible. Decay changes how much XP is awarded, which is a different kind of thing from flagging suspicious behaviour afterwards.

**Decision**:
- Decay is a **reward rule**. It is part of rule set `v2` (E, `first_repeat_percent`, `step_xp`), is decided once at the decision boundary (`Learner.complete_lesson`), and the decided amount and the repeat count `N` are stored on the `XPAwarded` fact. Replay and projections only fold stored facts. Rule sets stay immutable, versioned and SHA-256 pinned.
- `v2` is opt-in (registered with `register_decay_rulesets()`, kept in `rulesets_decay.json`). `v1` stays flat and remains the default, so existing behaviour, digests and tests are unchanged.
  - *Update (2026-11-21):* `rulesets_decay.json` also holds `v3`, which is `v2` plus `floor_xp: 5` (100, 50, 30, 10, 5, 5, ...). It is opt-in in the same way; the demo uses it for new rewards. `v2` is unchanged.
- Abuse detection stays **retroactive and flag-only** (ADR 0003). It reads stored facts and ingestion evidence in a separate pass, emits `suspected` signals with evidence references, and never changes XP already awarded, streaks, leaderboards or any existing learner state. It never produces `confirmed`; confirming a signal or correcting XP is a future, explicitly authorized action.
  - *Update (2026-11-20):* the opt-in XP hold (ADR 0003 amendment) is the one place a signal affects rewards: it decides the *next* lesson rewards within 24 hours of a scripted-farming flag as 0 XP at the decision boundary, stored on the fact like decay. Nothing already awarded changes.
- The cohort gaming guardrail reports the share of weekly XP awarded on repeat completions (`repeat_index >= 1`) per cohort and flags `share > 15%`. It is a report, not an enforcement.

**Consequences**:
*Positive*:
- Reward amounts remain deterministic and auditable: a historical award never changes because a rule file did.
- Detection can be re-run, trialled in shadow mode with other thresholds, and rebuilt from the event history without side effects.

*Negative*:
- Decay only reduces future awards; it does not claw back XP already awarded, and flagged learners keep what they earned until a human acts.
- Decay is only active for deployments that opt in to `v2`; under `v1` the guardrail still measures repeats but nothing is decayed.
- The 15% threshold, the 50% / 20 XP schedule and all detector thresholds are conservative placeholders checked only against synthetic data.

**Alternatives Considered**:
- *Decay as a detection-only concept* (award flat XP, later flag or discount repeats). Rejected: it would make the awarded amount depend on a retroactive process, break "a reward is decided once", and make the leaderboard disagree with the stored facts until a correction ran.
- *Applying XP clawback when abuse is detected.* Rejected for now: it needs an authorized confirmation step, compensating events and leaderboard reconciliation, and false positives (see the perfectly regular human in [abuse-detection.md](../abuse-detection.md)) would silently take XP from honest learners. Left as a future phase.
- *Editing `v1` to add decay.* Rejected: published rule sets are immutable, and stored facts name a version that must keep meaning the same content.
