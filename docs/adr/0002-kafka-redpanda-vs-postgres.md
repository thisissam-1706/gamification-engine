# 0002 - Kafka/Redpanda Event Pipeline vs Postgres CQRS

**Status**: Accepted
**Date**: 2026-09-26

**Context**: 
Choosing the event processing architecture for the gamification engine to deliver the core requirements of provable replay and deterministic state reconstruction, within an 8-week timeline and a 3-person team capacity.

**Decision**:
Selected Method B: Event Pipeline & CQRS (Kafka/Redpanda + Redis) over a single PostgreSQL instance.

**Consequences**:
*Positive Impacts*:
- Provable replay and deterministic state reconstruction: resetting a consumer offset and re-folding events serves as the replay mechanism itself.
- Parallel development: M3 (leaderboards), M5 (notifications), and M6 (abuse detection) represent independent read paths off the same event log. This enables parallel development, testing, and debugging across our 3-person team without serializing work on a shared database schema.
- Lock-free concurrency: Kafka partitions ensure all events for a userId flow into a single partition processed sequentially, eliminating race conditions and avoiding database row-level locking overhead.
- Native replayability and late-event resolution: In-memory recalculations for late events can occur and append newly calculated events without complex cross-table SQL rollbacks.

*Negative Impacts*:
- Eventual consistency: Read models update milliseconds after events are appended, requiring client applications to tolerate slight read delays.
- Operational overhead: Higher infrastructure complexity to deploy and maintain a message broker cluster and manage consumer group offsets.

**Alternatives Considered**:
Method A (PostgreSQL-Based Event Sourcing & Outbox Pattern), which was rejected due to lock bottlenecks, custom SQL patching requirements, and coupled infrastructure.
