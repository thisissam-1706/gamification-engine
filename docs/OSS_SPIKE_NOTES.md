# What eventsourcing gave us

Eventsourcing 9.5.5 provides aggregate event decorators, ordered SQLite event
streams, repository rehydration, and atomic application saves for source and
derived domain events.

# Gaps we filled ourselves

The application supplies business identities, deterministic streak and
freeze decisions, append-only corrections, quest and badge state, timezone
history, JSON payload contracts, producer binding, duplicate/rejected evidence,
and staged projection rebuilding. Replay only folds stored fields and never
calls decision rules. Kafka and bandit experimentation remain deferred.

# Decision

Keep the SQLite eventsourcing core for this OSS spike. Continue with explicit
application-level contracts and projections; defer Kafka integration and
bandit-based experimentation until the event and replay guarantees are stable.
