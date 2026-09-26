# 0001 - PostgreSQL-Based Event Sourcing vs Outbox Pattern

**Status**: Accepted
**Date**: 2026-09-26

**Context**: 
Choosing the underlying architecture to fulfill the requirement to rebuild state from history and prevent destructive state mutations. We needed to evaluate the PostgreSQL outbox pattern vs an event pipeline.

**Decision**:
We rejected Method A (PostgreSQL-Based Event Sourcing & Outbox Pattern) in favor of Method B (Event Pipeline & CQRS).

**Consequences**:
*Positive Impacts (Avoided negatives of Method A)*:
- Avoided concurrency bottlenecks (row-level locking), where multiple concurrent events saturate connection pools leading to latency spikes and query timeouts.
- Avoided late event brittleness, where integrating offline events requires brittle, custom SQL logic to manually roll back incorrect downstream triggers.
- Prevented a coupled infrastructure monolithic bottleneck where gamification, leaderboards, and abuse detection compete for the same database compute resources.

*Negative Impacts*:
- We must accept the operational overhead and learning curve of running an event broker (Method B).

**Alternatives Considered**:
Method A: PostgreSQL-Based Event Sourcing & Outbox Pattern.
Strengths evaluated included:
- Atomic Consistency: The database natively prevents race conditions during concurrent updates via ACID transactions.
- Familiar Tooling: Developers familiar with SQL can build the initial schema and API rapidly without managing distributed infrastructure.
