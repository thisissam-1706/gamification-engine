# 0002 - Kafka/Redpanda Event Pipeline vs Postgres CQRS

**Status**: Accepted
**Date**: 2026-09-26

**Context**: 
Choosing the event processing architecture for the gamification engine to deliver the core requirements of provable replay and deterministic state reconstruction, within an 8-week timeline and a 2-person team capacity.

**Decision**:
Selected Method B: Event Pipeline & CQRS (Kafka/Redpanda + Redis) over a single PostgreSQL instance.

**Consequences**:
*Positive Impacts*:
- Provable replay and deterministic state reconstruction: Replaying from an earlier offset re-reads the log, but correctness also requires that applying an event is idempotent and that outputs are coordinated with offsets. Rebuild mode applies stored events and must not issue new rewards or resend notifications. If Kafka is adopted, we define how offsets, projection writes and outbound side effects are coordinated, and the log retention needed for full-history rebuilds.
- Parallel development: M3 (leaderboards), M5 (notifications), and M6 (abuse detection) represent independent read paths off the same event log. This enables parallel development, testing, and debugging across our 2-person team without serializing work on a shared database schema.
- Native replayability and late-event resolution: In-memory recalculations for late events can occur and append newly calculated events without complex cross-table SQL rollbacks.
- Kafka preserves append order within a partition, so events for one learner keyed to one partition are processed in the order they were written. It does not sort by occurred_at, so late or out-of-order events still require explicit event-time handling in the state machine.
- Concurrency: Single-partition, single-consumer processing serialises events per learner, which avoids concurrent updates to one learner's state within a consumer group. It does not remove the need for idempotent handling, duplicate protection across restarts, or coordination of writes to separate stores.

*Negative Impacts*:
- Eventual consistency: Read models update milliseconds after events are appended, requiring client applications to tolerate slight read delays.
- Operational overhead: Higher infrastructure complexity to deploy and maintain a message broker cluster and manage consumer group offsets.



**Alternatives Considered**:
Method A (PostgreSQL-Based Event Sourcing & Outbox Pattern).
- Database-backed event stores are a viable foundation to for event-sourcing and replay.
- We evaluated an existing Python event-sourcing library on SQLite (PostgreSQL later) and found it supports ordered event streams, rehydration of aggregates and atomic saves of multiple events.
