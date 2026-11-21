# Problem Boundaries and Assumptions

> **Changelog (2026-10-12):** Centralized the notification quiet-hours window and daily notification cap for cross-document reference.
>
> **Changelog (2026-11-20):** The daily cap is a calendar-day cap that resets at the learner's local midnight (previously a rolling 24-hour window, which made "the next morning" still capped).
>
> **Changelog (2026-11-21):** Rewards now name decay rule set `v3` (5 XP floor) and the pass mark; abuse detection names the opt-in 24-hour XP hold; the dashboard bullet describes the demo web UI that exists.

* **Supported Rewards:** The primary reward mechanism is Experience Points (XP), a fixed per-ruleset amount (`xp_amount` in `rulesets.json`). A decay model that diminishes returns on repeated lessons is implemented as the opt-in rule sets `v2` and `v3` (`rulesets_decay.json`, registered with `register_decay_rulesets()`; `v3` is `v2` with a 5 XP floor); the default rule set `v1` remains flat. A graded lesson (`questions_correct`/`questions_total` in the payload) with more than half the answers wrong earns 0 XP and counts for nothing (pass mark, `below_pass_mark`).
* **Streak Model:** Daily streaks are strictly calculated using the learner’s localized IANA timezone to determine day boundaries, incorporating a “freeze” inventory that protects users from losing their streak during inactivity.
* **Cohorts and Leaderboards:** Learners are deterministically assigned to scoped cohorts. Leaderboard projections are SQLite-backed (M3); Redis-backed leaderboards are deferred until measurements justify them.
## Notification Channels and Constraints

Interventions are delivered via push notifications, with email as a second simulated channel. The daily notification cap is **5 messages per learner per channel per calendar day** (in-app, push and email each have their own 5), reset at **00:00 in the learner’s timezone**, and localized quiet hours are **22:00–08:00** in the learner’s timezone; both constraints prevent notification fatigue.

* **Synthetic-Population Size:** The system assumes a high-volume synthetic user base for future peak-load and P99 measurements; Kafka partition concurrency is not part of the current implementation.
* **System Exclusions:** Rule-based abuse detection (M6) exists but is flag-only: it is retroactive, never runs inside ingestion, never changes XP already awarded, streaks or leaderboards, and never confirms a signal or corrects XP (see [abuse-detection.md](abuse-detection.md)). The one exception is opt-in: when ingestion is configured with the XP hold (`IngestConfig.xp_hold`), a stored scripted-farming signal makes new lessons within the next 24 hours earn 0 XP (the lesson is still accepted). An independent consumer and separate event-stream deployment are future extensions.
* **Interface Boundaries (Demo UI Scope):** The web UI in `webapp/` is a demonstration layer, not part of the engine and not a production product. It has a learner home page and lesson page (read-only by default; writable only in `DEMO_MODE` against a sandbox copy of the stores), browser notifications for delivered pushes, and an admin console that inspects event logs, cohorts, notifications, abuse flags, experiments and recovery and triggers engine jobs. All engine behaviour lives in `src/` and is tested without the UI; the UI only calls the engine's public functions on synthetic data.
