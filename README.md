# Gamification, Social Cohort, Notification and Retention Experimentation Engine

> **Changelog (2026-11-14):** M5 follow-up: the streak-risk reminder generator no longer treats a stale streak as at risk: a learner whose streak a lesson today could not extend (by the same rule `calculate_streak` uses, where a freeze can bridge one missed day) gets no reminder, reason `streak_already_broken`. It also applies the exp_007 exclusions: platform tenure under 3 x 24 hours (measured from the learner's Created event timestamp) is `excluded_tenure`, and opt-out of the push channel or the streak_risk category is `excluded_opted_out` (no proposal is built and the dispatcher is not called). The sweep reason `completed_today` is now `already_active_today`. No acceptance wording or thresholds changed.

> **Changelog (2026-11-14):** M5: notification proposals can carry an optional `experiment_id` and `variant`, stored on the proposal, rescheduled and dead-letter rows. When the daily cap suppresses a proposal that has an `experiment_id`, one row is written to `notification_guardrail_violations` (unique per proposal and reason). A streak-risk reminder generator, `src/notifications/streak_risk.py`, plans exp_007 treatment (`variant_a`) reminders at 20:00, 21:00, 21:30 and 21:45 learner local time for learners with a streak and no lesson on their local date, and `run_streak_risk_sweep` hands the latest due reminder to the dispatcher. The sweep is caller-run: nothing schedules it. Guardrail violations are recorded but not yet read by an M7 runner, so the experiment is not yet invalidated automatically. No acceptance wording or thresholds changed.

> **Changelog (2026-11-14):** M3 follow-up: a small cohort now merges into the NEAREST cohort that has room (same tier first, then same module, then lowest id); late arrivals count toward cohort sizes (existing members are never moved, and with automatic buckets a late group first joins an existing same-tier cohort); when bucket counts are not passed the builder chooses them from group size; a leaderboard poller (`poll_once`, `run_until_idle`) and a weekly cycle (`run_weekly_cycle`: catch up, finalize last week's cohorts, build this week's, poll) exist. All are run by the caller; nothing schedules them, and the cycle does not seal this epoch's cohorts. Demo labels now read "Total XP" and "XP this week". The AT-7 wording and all numbers are unchanged.

> **Changelog (2026-11-14):** M3: the weekly cohort builder (`build_weekly_cohorts`) is implemented and documented. It assigns learners to a cohort for an ISO-week epoch from the prior-week lesson count (casual/regular/intensive) and the module prefix of `lesson_id` (`<module_id>/<lesson>`, a convention; no prefix gives `general`), applies the 30-day inactivity filter, splits cohorts above `max_size` and merges cohorts below `min_size` with `merge_undersized_cohorts`, and records each assignment with `grouping_rule_version` `cohort_builder_v1`. It is invoked by the synthetic rebuild harness and the demo seed, not by a schedule; sealing, reconciliation and running the builder each week remain caller-invoked. The AT-7 wording and all numbers are unchanged.

> **Changelog (2026-11-14):** M3/M8: the synthetic workload and demo seed no longer assign cohorts round-robin. Lesson ids are now `<module_id>/<lesson>`, the workload carries prior-week history (lessons in the ISO week before the epoch, ingested as ordinary events before cohorts are built), and cohorts come from `build_weekly_cohorts` (prior-week lesson tier plus module prefix; small populations use `min_size=2` and one hash bucket). The AT-15 clarification in docs/traceability-matrix.md was updated with the new counts; the AT-15 acceptance wording and thresholds are unchanged.

> **Changelog (2026-11-14):** M1: engine-produced events and notifications now carry correlation and causation ids, and `trace_delivery` follows them. Events the engine creates while handling one delivery (XP, streak, badge, quest progress, completion and expiry, remediation decision and quest assignment) carry the delivery's `correlation_id` and use the delivery's `event_id` as `causation_id`; sweeps and cohort assignment have their own default correlation ids; notifications carry the ids of the attempt that triggered them. Events stored before this change have none and still replay. No acceptance-test wording, number or threshold changed.

> **Changelog (2026-11-09):** Demo backend (`webapp/demo_backend.py`): `create_sandbox` seeds fresh SQLite stores and `Sandbox` exposes `submit_lesson_completed`, `submit_checkpoint`, `set_clock`/`get_clock`, and `reset`; the demo clock is per-learner. After every accepted event the leaderboard projection and the remediation notification hook run synchronously inside the same lock. Seeded learners carry no ingestion evidence; all data is synthetic. The web UI can now be run in a writable DEMO_MODE (see "Running the web UI" below).

> **Changelog (2026-11-07):** M3 wording: cohort admission is now atomic (member count, capacity check and write share one write-locked SQLite transaction); the "capacity check not transactional" known gap was removed.

> **Changelog (2026-11-07):** M7 wording: AT-18 (policy comparison) now passes on the synthetic simulator (`tests/simulator/test_policy_comparison.py`); eligibility, decision and delivery are simulator stand-ins, and exposure is still not consumed by the metric.

> **Changelog (2026-11-06):** M7 wording: experiment assignment is recorded as `ExperimentAssigned` and treatment-surface exposure as `ExperimentExposed` (AT-12 and AT-13 passing on synthetic fixtures); the two M7 placeholder tests now pass; exposure is not yet consumed by the metric.

> **Changelog (2026-11-02):** M8 wording: the AT-15 wipe destroys all derived state and rebuilds from the retained event log with a fresh application instance; the synthetic population is small (15 learners).

> **Changelog (2026-11-02):** Added ADR-0007 (rule-based remediation policy) to the ADR list; the M1 description now names both ingested event types.

> **Changelog (2026-10-12):** Documented the current single-process eventsourcing and SQLite architecture; marked Kafka/Redpanda, Redis, and PostgreSQL as deferred options.

An event-sourced gamification engine with deterministic XP, streaks, quests, badges, notifications, and experiments. Rule-based abuse detection (M6) is implemented for synthetic fixtures as a retroactive, flag-only pass, and XP decay exists as an opt-in rule set (`v2`); neither is validated on real data. The current implementation runs as a single process using the `eventsourcing` library with SQLite as its durable event store.

## Architecture

```mermaid
flowchart LR
    subgraph Producers["Event producers"]
        Learning["Learning application"]
        Simulator["Simulator / test generators"]
    end

    subgraph Runtime["Single-process gamification engine"]
        Validate["Ingestion validation<br/>JSON Schema + producer checks"]
        Ingest["Ingestion service<br/>deduplication + evidence"]
        Aggregate["Learner aggregate<br/>deterministic XP, streaks,<br/>quests, badges, freezes"]
        Rules["Versioned rule sets"]
        EventStore[("Eventsourcing event store<br/>SQLite")]
        Projections["Read-model projections<br/>completion, streak, quest"]
        LeaderboardProjection["Leaderboard projection"]
        LeaderboardAPI["Leaderboard service API"]
        LeaderboardDB[("SQLite leaderboard<br/>read model")]
        Reconciler["Reconciliation service<br/>watermark + differential repair"]
        Experiment["Experiment assignment<br/>and outcome metrics"]
        EvidenceDB[("SQLite ingestion<br/>evidence")]
    end

    Learning --> Validate
    Simulator --> Validate
    Validate --> Ingest
    Ingest --> EvidenceDB
    Ingest --> Aggregate
    Rules --> Aggregate
    Aggregate <--> EventStore
    EventStore --> Projections
    EventStore --> LeaderboardProjection
    LeaderboardProjection --> LeaderboardDB
    LeaderboardDB --> LeaderboardAPI
    LeaderboardDB -. applied-event log to pinned offset .-> Reconciler
    Reconciler <--> LeaderboardDB
    EventStore --> Experiment
    EventStore -. replay / rebuild .-> Aggregate

    Notifications["Notification engine"]
    Intervention["Intervention policy (remediation)<br/>stored decisions + remediation quest"]
    Sweep["Quest expiry sweep"]
    Abuse["Abuse detection (flag-only)"]
    Kafka["Kafka / Redpanda"]:::deferred
    Redis["Redis"]:::deferred
    Postgres["PostgreSQL"]:::deferred

    EventStore -. deferred scale-out .-> Kafka
    LeaderboardDB -. deferred cache .-> Redis
    EventStore -. deferred production store .-> Postgres
    Projections --> Notifications
    EventStore -. retroactive pass, flag-only .-> Abuse
    IngestionEvidence["Ingestion evidence"] -. read by .-> Abuse
    Aggregate -. invoked, reads attempts and prior decisions .-> Intervention
    Intervention -. decision + quest, one save .-> Aggregate
    Aggregate -. invoked, reads overdue quests .-> Sweep
    Sweep -. QuestExpired at deadline .-> Aggregate

    classDef deferred stroke-dasharray: 5 5,fill:#f5f5f5,color:#666;
```

The implemented path runs in one process: producers submit versioned events, ingestion validates and deduplicates them, and the learner aggregate emits durable domain events to the SQLite-backed eventsourcing store. Rebuildable projections provide learner summaries and leaderboard scores; the leaderboard service reads the SQLite leaderboard model and the reconciliation service repairs divergence against the projection's own applied-event log up to a pinned offset (see [ranking-consistency.md](docs/ranking-consistency.md) section 6). Experiment assignment and metrics consume event history. In-app and simulated-push notifications are implemented; a retroactive, flag-only abuse detection pass (`src/abuse`, see [abuse-detection.md](docs/abuse-detection.md)) reads stored facts and ingestion evidence outside ingestion and never changes learner state; Kafka/Redpanda, Redis, and PostgreSQL are deferred extension points rather than active runtime dependencies.

## Documentation

- [Problem Boundaries](docs/problem-boundaries.md)
- [Abuse Detection (M6)](docs/abuse-detection.md)
- [Literature and Landscape Analysis](docs/literature-landscape.md)
- [Metrics Framework](docs/metrics-framework.md)
- [Traceability Matrix and Acceptance Tests](docs/traceability-matrix.md)
- [Event Contracts](docs/event-contracts.md)

The architecture diagram above is the repository's single high-level architecture description. There is no separate architecture document, to avoid duplicate or conflicting descriptions.

## ADRs

- [ADR-0001: Event Pipeline vs. PostgreSQL Outbox](docs/adr/0001-event-sourcing-vs-outbox.md)
- [ADR-0002: Kafka/Redpanda Event Pipeline vs. PostgreSQL](docs/adr/0002-kafka-redpanda-vs-postgres.md)
- [ADR-0003: Rule-Based Abuse Detection](docs/adr/0003-rule-based-abuse-detection.md)
- [ADR-0004: Fixed A/B with Optional Bandit](docs/adr/0004-bandit-vs-fixed-ab.md)
- [ADR-0005: Eventsourcing and SQLite First](docs/adr/0005-eventsourcing-sqlite-first.md)
- [ADR-0006: Decay in the Reward Path, Detection Stays Flag-Only](docs/adr/0006-decay-in-reward-path.md)
- [ADR-0007: Rule-Based Remediation Policy with Stored Decisions](docs/adr/0007-rule-based-remediation-policy.md)

## How to Run Locally

1. Create and activate a Python 3.11+ virtual environment. For the exact pinned versions, use `pip install -r requirements-lock.txt && pip install -e . --no-deps` instead of the steps below.
2. Install the project and its development dependencies defined in `pyproject.toml`:

   ```bash
   python -m pip install -e ".[dev]"
   ```

3. Run the tests:

   ```bash
   python -m pytest
   ```

   `pytest -m integration` runs the packaging test.

Kafka/Redpanda, Redis, and PostgreSQL setup instructions will be added only if measured scale or reliability needs require those components.

## Running the web UI

A learner home page serves seeded demo data (UI layer in `webapp/`, not engine state). By default it is **read-only**: the existing endpoints only serve pre-seeded SQLite snapshots and no events can be submitted through the UI.

```bash
python -m pip install -e ".[web]"
```

```bash
python -m webapp.seed
```

```bash
python -m uvicorn webapp.app:app --port 8000
```

Then open <http://localhost:8000/>.

### DEMO_MODE — writable sandbox

Set `DEMO_MODE=1` together with `DEMO_SANDBOX_PATH` to run a writable sandbox copy of the stores. `DEMO_SANDBOX_PATH` is required when `DEMO_MODE=1`; if its live profile is absent, the server seeds a fresh golden snapshot into `<DEMO_SANDBOX_PATH>/golden`, copies it to `<DEMO_SANDBOX_PATH>/live`, and routes all reads to the live copy. POST endpoints become available to submit events against the live stores.

```bash
DEMO_MODE=1 DEMO_SANDBOX_PATH=/tmp/demo-sandbox \
  python -m uvicorn webapp.app:app --port 8000
```

**Demo clock** — the demo clock is **per-learner**. Set a learner's simulated "now" to any UTC instant and subsequent submissions use that instant as `occurred_at` and `ingested_at`:

```bash
# Read learner clock
curl http://localhost:8000/api/demo/learners/<id>/clock

# Set learner clock
curl -X POST http://localhost:8000/api/demo/learners/<id>/clock \
     -H "Content-Type: application/json" \
     -d '{"now_utc":"2026-10-20T10:00:00Z"}'

# Submit a lesson completion
curl -X POST http://localhost:8000/api/demo/learners/<id>/lesson-complete \
     -H "Content-Type: application/json" \
     -d '{"lesson_id":"lesson_intro_1","completion_kind":"lesson"}'

# Submit a checkpoint attempt
curl -X POST http://localhost:8000/api/demo/learners/<id>/checkpoint \
     -H "Content-Type: application/json" \
     -d '{"assessment_id":"quiz_1","passed":false}'

# Reset sandbox to seeded state
curl -X POST http://localhost:8000/api/demo/reset
```

After every accepted event the leaderboard projection and (for failed checkpoints) the remediation notification hook run synchronously inside the same submit lock. Seeded learners carry no ingestion evidence; all data is synthetic.

## Reproducing the evidence

- `requirements-lock.txt` pins the exact dependency versions used for the recorded test runs.
- `scripts/capture_evidence.sh` writes the commit, environment and full pytest output to `docs/evidence/<short-sha>/`; run it only on a clean, committed tree.
- `.github/workflows/ci.yml` runs the same install and test steps on GitHub Actions.

## Milestone Status

| Milestone | Description | Status |
|-----------|-------------|--------|
| M1 | Canonical engagement events | ☐ (partial: lesson_completed and checkpoint_attempted ingested; ID binding, late window, rule selection tested; engine-produced events and notifications carry correlation and causation ids and trace_delivery follows them) |
| M2 | Deterministic gamification | ☑ (slice done: XP, streak/freezes, one quest, one badge, replay) |
| M3 | Cohorts and leaderboards | ☐ (leaderboard duplicate, restart, offset and time handling tested; weekly cohort builder implemented (prior-week lesson tier, lesson-id module prefix, 30-day inactivity filter, split, merge into the nearest cohort, late arrivals counted toward sizes, bucket counts chosen from group size); leaderboard poller and weekly cycle available (invoked by the synthetic harness and demo seed); known gaps: the builder, poller, weekly cycle, sealing and reconciliation are caller-invoked, not scheduled, and member count covers learners assigned for the current weekly epoch; no scheduled notification-log polling; reconciliation rebuilds from the projection's applied-event log) |
| M4 | Intervention/quest policy | ☐ (implemented for synthetic fixtures: checkpoint_attempted ingestion, rule-based remediation policy with stored decisions, remediation_v1 quest (remediation completions only), deadline expiry sweep; policy and sweep invoked, not scheduled; an opt-in notification wrapper (`src/interventions/notification_hook.py`) sends one remediation notice per assign decision (push by default, deduped by proposal id), still invoked and not scheduled) |
| M5 | Multi-channel notifications | ☐ (in-app, simulated-push and simulated-email slice implemented; email shares the push guardrails: the 5-per-24h cap and quiet hours 22:00–08:00; the exp_007 streak-risk reminder generator (`src/notifications/streak_risk.py`) and the experiment variant flag on notification proposals exist, and cap breaches of experiment proposals are recorded as guardrail violations; the generator skips learners whose streak can no longer be extended (same continuation rule as `calculate_streak`, a freeze can bridge one missed day) and applies the exp_007 exclusions (platform tenure under 3 days from the learner's Created event, opt-out of push or of streak_risk); the reminder sweep is run by the caller, nothing schedules it (it can record one decision row per learner outcome in the experiment store), and the recorded violations are read by the M7 runner (`run_experiment` counts rows with reason `daily_cap_exceeded` per learner per UTC day; a treatment-arm breach invalidates the run)) |
| M6 | Abuse detection | ☐ (implemented for synthetic fixtures; flag-only; thresholds unvalidated on real data; XP decay is opt-in ruleset `v2`) |
| M7 | Experimentation and metrics | ☐ (experiment assignment recorded as `ExperimentAssigned` and treatment-surface exposure as `ExperimentExposed` (AT-12 and AT-13 passing; synthetic fixtures; control learners never produce exposure events); the experiment runner `run_experiment`, the eligibility and decision stores, the manual entry point `scripts/run_experiment.py` (`assign`, `sweep`, `report`, run by the caller, nothing schedules them; notification preferences come from a JSON file, not the event log) and the real-log reproduction check exist and are tested; it reports per-arm mastery (completions derived from the stored attempts), exposure, fatigue (messages per learner per day over each learner's observed window of at most 14 days, delivered-over-cap and opt-out counts), a treatment-minus-control contrast and a sample-ratio check, and marks the run invalid with `invalid_reasons` (treatment cap breach, treatment delivered-over-cap, gaming share, sample ratio, missing eligibility records, no eligible learners, mixed assessment versions) while still reporting every number; acting on that verdict is the caller's job and is not automated; exposure is not consumed by the metric (a per-arm diagnostic, control None); simulator implemented for synthetic data (pilot v1 and the generator functions, see `simulator/SPEC.md`); AT-18 passing, simulator-backed synthetic) |
| M8 | Scale and recovery | ☑ (pinned synthetic workload, multi-learner full-state rebuild AT-15 (the wipe destroys all derived state, i.e. the leaderboard and projections, and rebuilds from the retained event log with a fresh application instance; the event log itself is not wiped; synthetic population is small: 15 learners, 843 replayed entries), leaderboard p50/p95/p99 latency bounds, DLQ outage recovery drain, raw-log experiment reproduction) |

M1 means events from the learning app are validated against versioned schemas before acceptance (currently `lesson_completed` and `checkpoint_attempted` are ingested), while M2 means XP, streaks, freezes, one quest, and one badge are computed deterministically and can be rebuilt exactly from the event history. M3 means synthetic learners are deterministically grouped into bounded cohorts (500–5,000 members; the 30-day inactivity filter and nearest-cohort merging run inside the weekly cohort builder; the builder, a leaderboard poller and a weekly cycle are caller-run and not scheduled) and ranked at cohort and global levels via SQLite projections with exact tie-breaking, measurable latency, and differential reconciliation (AT-7).

## Planned Placeholder Tests

| Test | Milestone | Status |
|------|-----------|--------|
| `test_point_farming` | M6 | passing (AT-6; synthetic fixtures, flag-only) |
| `test_learning_remediation` | M4 | passing (AT-17; synthetic fixtures; policy and sweep invoked, not scheduled) |
| `test_notification_fatigue` | M5 | planned placeholder |
| `test_opt_out_enforcement` | M5 | planned placeholder |
| `test_quiet_hour_delay` | M5 | planned placeholder |
| `test_retry_dlq` | M5 | planned placeholder |
| `test_leaderboard_reconciliation` | M3 | passing (AT-7) |
| `test_full_state_rebuild` | M2, M7, M8 | passing (AT-15; multi-learner full-state disaster recovery) |
| `test_assignment_vs_exposure` | M7 | passing (AT-13; synthetic fixtures) |
| `test_exposure_is_logged_only_after_treatment_surface_view` | M7 | passing (AT-13; synthetic fixtures) |
| `test_policy_comparison` | M7 | passing (AT-18; simulator-backed synthetic) |
