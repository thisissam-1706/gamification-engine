# Problem Boundaries and Assumptions

> **Changelog (2026-10-12):** Centralized the notification quiet-hours window and daily notification cap for cross-document reference.

* **Supported Rewards:** The primary reward mechanism is Experience Points (XP), a fixed per-ruleset amount (`xp_amount` in `rulesets.json`). A decay model that diminishes returns on repeated lessons is implemented as the opt-in rule set `v2` (`rulesets_decay.json`, registered with `register_decay_rulesets()`); the default rule set `v1` remains flat.
* **Streak Model:** Daily streaks are strictly calculated using the learner’s localized IANA timezone to determine day boundaries, incorporating a “freeze” inventory that protects users from losing their streak during inactivity.
* **Cohorts and Leaderboards:** Learners are deterministically assigned to scoped cohorts. Leaderboard projections are SQLite-backed (M3); Redis-backed leaderboards are deferred until measurements justify them.
## Notification Channels and Constraints

Interventions are delivered via push notifications. The daily notification cap is **5 messages per 24-hour period per learner**, and localized quiet hours are **22:00–08:00** in the learner’s timezone; both constraints prevent notification fatigue.

* **Synthetic-Population Size:** The system assumes a high-volume synthetic user base for future peak-load and P99 measurements; Kafka partition concurrency is not part of the current implementation.
* **System Exclusions:** Rule-based abuse detection (M6) exists but is flag-only: it is retroactive, never runs inside ingestion, never changes XP, streaks or leaderboards, and never confirms a signal or corrects XP (see [abuse-detection.md](abuse-detection.md)); an independent consumer and separate event-stream deployment are future extensions.
* **Interface Boundaries (Dashboard Scope):** The dashboard is strictly an internal, read-only debugging tool to inspect raw event logs, cohort states, and (planned) abuse flags; the M6 signals are stored in SQLite but no dashboard is implemented. It is not a learner-facing product and explicitly excludes UI styling, push updates, or interactive features to keep focus entirely on M1–M8 backend milestones.
