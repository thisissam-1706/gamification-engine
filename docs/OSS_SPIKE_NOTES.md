# What eventsourcing gave us

Eventsourcing 9.5.5 provides aggregate event decorators, ordered SQLite event
streams, repository rehydration, and atomic application saves for source and
derived domain events.

# Gaps we filled ourselves

The application supplies business identities, deterministic streak and
freeze decisions, append-only corrections, quest and badge state, timezone
history, JSON payload contracts, producer binding, a validated ingestion
boundary with durable duplicate/rejected evidence, and SQLite staging/swap
projection rebuilding. Replay only folds stored fields and never calls
decision rules. Concurrent duplicate completions are retried into one
duplicate result, duplicate quest completions are recorded as durable
evidence, and external derived-event envelopes are rejected at ingestion.
Kafka and bandit experimentation remain deferred.

# Remaining verification limits

- SQLite save atomicity was verified directly: an injected exception after the
  first pending event leaves neither the completion nor its XP award stored.
- Ingestion accepts only `learning.lesson_completed` source events. Quest
  assignment from an upstream producer is not implemented; streak, badge, XP,
  quest progress/completion/expiry, and freeze events are derived inside the
  engine and external envelopes for them are rejected.
- Evidence rows are stored separately from learner events and are not consumed
  by replay, so rebuilds leave learner state, business-event counts, and event
  counts unchanged.
- Leaderboards, notifications, abuse detection, experiments, the simulator,
  and the seven-day TTL cache remain later milestones. Business uniqueness
  currently comes from the durable `completion_id` check and evidence rows.

# Decision

Keep the SQLite eventsourcing core for this OSS spike. Continue with explicit
application-level contracts and projections; defer Kafka integration and
bandit-based experimentation until the event and replay guarantees are stable.
