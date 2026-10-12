# 0005 - Eventsourcing and SQLite First

> **Changelog (2026-10-12):** Added the current architecture decision for the eventsourcing library and SQLite-first implementation.

**Status**: Accepted
**Date**: 2026-10-12

**Context**:
The Week 2 review confirmed that the engine needs durable event history, deterministic replay, and a runnable implementation within the current project scope. The codebase is a single-process Python application using `eventsourcing==9.5.5` and SQLite. Kafka/Redpanda, Redis, and PostgreSQL are not part of the current implementation.

**Decision**:
Use the `eventsourcing` library with SQLite as the event store and run the current engine in one process. Defer Kafka/Redpanda, Redis, PostgreSQL, and independent consumers until measured workload, latency, durability, or operational requirements demonstrate a need for them.

**Rationale**:
- The existing library provides event streams, aggregate rehydration, atomic event persistence, and replay support without introducing new infrastructure.
- SQLite keeps the first implementation runnable and reviewable while preserving the event history needed for deterministic state reconstruction.
- Deferring distributed infrastructure avoids paying its operational complexity before the system has measurements that justify it.

**Consequences**:
- The current architecture is intentionally single-process and SQLite-backed.
- Scaling or isolating consumers later will require a measured migration plan and updated architecture decision.
- ADR-0002 remains historical; this ADR supersedes its current-architecture recommendation.
