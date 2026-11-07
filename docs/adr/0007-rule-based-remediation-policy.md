# 0007 - Rule-Based Remediation Policy with Stored Decisions

**Status**: Accepted
**Date**: 2026-11-02

**Context**:
M4 (AT-17) asks for a learning-oriented intervention: when a learner fails their first attempt at a checkpoint assessment, offer one remediation quest, but not repeatedly, and make an unfinished quest expire. The policy has to be explainable to a learner and a reviewer, reproducible from the event history, and must not change XP, streaks or the published rule sets. [ADR 0004](0004-bandit-vs-fixed-ab.md) keeps allocation policies fixed and deterministic, and [ADR 0006](0006-decay-in-reward-path.md) keeps published rule sets immutable and decisions made once at a decision boundary.

**Decision**:
- The remediation policy is **rule-based and deterministic** (`decide_remediation` in `src/interventions/policy.py`, a pure function of the stored attempts, the stored prior decisions, `now` and a config). The rules are checked in a fixed order: unknown attempt (`KeyError`), naive `now` (`ValueError`), not the first attempt for its assessment (`not_first_attempt`), passed (`passed`), an `assign` decision inside the window (`remediated_within_window`), otherwise `assign`. The first attempt is the earliest by `(occurred_at, attempt_id)`, which does not depend on arrival order.
- Every decision, **including `no_action`**, is stored as a `RemediationDecided` fact on the learner aggregate, identified by `remediation:<attempt_id>`. Decisions are **never recomputed on replay**: re-running the policy for a decided attempt returns the stored decision, even if the clock, the config or the rules have changed.
- The frequency window is **14 days, learner-wide, measured from the `decided_at` of the last `assign`** decision. A decision exactly 14 days old is outside the window; `no_action` decisions never block. The quest **deadline is 7 days** after the decision.
- An `assign` decision assigns the **`remediation_v1` quest** (`remediation_quest:<attempt_id>`) in the **same save** as the decision. If the quest cannot be assigned, nothing is saved. The quest counts only completions of kind `remediation`.
- The quest is defined in a **separate quest-definition registry** (`src/state_machine/quest_definitions.py`), version `remediation_quests_v1`, target 1, because published rule sets are immutable and their digests are pinned. Rule-set quests fall back to the rule set and accept every completion kind, so their behaviour is unchanged.
- Quests expire through an **explicit expiry sweep** (`Learner.expire_due_quests`, run by `run_quest_expiry`): every `assigned` quest with `deadline_at < as_of` gets a `QuestExpired` fact with **`expired_at = deadline_at`**. The existing expiry on a later completion records the same `expired_at`. The sweep is idempotent and covers all quests.

**Consequences**:
*Positive*:
- Every decision, including "no intervention", is an auditable fact; a rule or configuration change cannot silently rewrite history.
- The policy is a pure function, so it is unit-testable without an event store, and the runner adds only load, record and save.
- Decision and quest are one save, so there is never a decision without its quest.
- The deadline is stamped the same way on both expiry paths, so the fact does not depend on when it was noticed.

*Negative*:
- The policy and the sweep are **invoked, not scheduled**: nothing in `src` calls `run_remediation` or `run_quest_expiry` (only tests do); until something does, no decision is made and no quest expires on its own.
- There is **no notification hook**: a learner is not told about a remediation quest.
  - *Update (2026-11-07):* a separate opt-in wrapper, `src/interventions/notification_hook.py` (`run_remediation_with_notification`), now sends one remediation notice per `assign` decision through the notification dispatcher (push by default; proposal id `remediation_notice:<attempt_id>`, so a re-run is deduplicated). It calls `run_remediation`; `policy.py` and `service.py` still do not know about notifications, and the notification outcome never changes the stored decision or quest. The wrapper is still invoked, not scheduled: nothing in `src` calls it, and `run_quest_expiry` sends no notice.
- The M7 mastery metric (`first_eligible_checkpoint_mastery`) does not yet read checkpoint events: it takes caller-supplied attempt and completion records, and nothing feeds it from `Learner.checkpoint_attempts`.
- Assign decisions stored before the quest was wired in have no quest and are not backfilled.
- The 14-day window, the 7-day deadline and the single-quest design are untuned defaults checked only against synthetic data.

**Alternatives Considered**:
- *A bandit or adaptive policy.* Rejected for now: it needs exposure logging and propensity records that do not exist yet, and a fixed rule is easier to explain and test against AT-17. It remains a later extension in the sense of ADR 0004.
- *Recomputing decisions on replay.* Rejected: a change of rules, window or clock would rewrite past decisions and quests, and the leaderboard and projections would disagree with the stored facts. The decision is made once and stored, as reward amounts are (ADR 0006).
- *Editing rule set `v1` to hold the quest.* Rejected: published rule sets are immutable and SHA-256 pinned, and stored facts name a version that must keep meaning the same content. A separate registry leaves `v1` and its digest untouched.
- *Lazy-only expiry (a quest expires only when a later completion arrives).* Rejected: a learner who never completes again would keep an `assigned` quest forever, and the stored `expired_at` would depend on when the next completion happened to arrive.
