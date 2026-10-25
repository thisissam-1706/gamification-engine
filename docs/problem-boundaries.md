# Problem Boundaries and Assumptions

> **Changelog (2026-10-12):** Centralized the notification quiet-hours window and daily notification cap for cross-document reference.

* **Supported Rewards:** The primary reward mechanism is Experience Points (XP), a fixed per-ruleset amount (`xp_amount` in `rulesets.json`). A decay model that diminishes returns on repeated lessons is a planned part of M6 and is not implemented.
* **Streak Model:** Daily streaks are strictly calculated using the learner’s localized IANA timezone to determine day boundaries, incorporating a “freeze” inventory that protects users from losing their streak during inactivity.
* **Cohorts and Leaderboards:** Learners are deterministically assigned to scoped cohorts. Leaderboard projections are SQLite-backed (M3); Redis-backed leaderboards are deferred until measurements justify them.
## Notification Channels and Constraints

Interventions are delivered via push notifications. The daily notification cap is **5 messages per 24-hour period per learner**, and localized quiet hours are **22:00–08:00** in the learner’s timezone; both constraints prevent notification fatigue.

* **Synthetic-Population Size:** The system assumes a high-volume synthetic user base for future peak-load and P99 measurements; Kafka partition concurrency is not part of the current implementation.
* **System Exclusions:** Rule-based abuse detection is planned (M6) but not implemented; an independent consumer and separate event-stream deployment are future extensions.
* **Interface Boundaries (Dashboard Scope):** The dashboard is strictly an internal, read-only debugging tool to inspect raw event logs, cohort states, and (planned, M6) abuse flags. It is not a learner-facing product and explicitly excludes UI styling, push updates, or interactive features to keep focus entirely on M1–M8 backend milestones.
