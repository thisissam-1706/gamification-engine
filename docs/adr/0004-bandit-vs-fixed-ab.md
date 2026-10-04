# 0004 - Contextual Multi-Armed Bandit vs Fixed A/B Testing

**Status**: Accepted
**Date**: 2026-09-26

**Context**: 
Choosing the allocation policy for notification experiments to improve student engagement without falling into the pitfalls of typical bandit algorithms that exploit easy-to-measure proxies (like email opens) rather than actual learning outcomes.

**Decision**:
Selected a deterministic fixed A/B allocation as the core baseline for notification policy selection. A contextual multi-armed bandit (with propensity logging and an exploration floor) is retained as an optional, later-stage extension. 
*Note: This policy applies to notification experiments; core reward rules (XP amounts, decay rates) stay fixed and rule-versioned.*

**Consequences**:
*Positive Impacts*:
- Aligns with the minimum viable prototype requirement to establish a working, provable baseline before introducing adaptive complexity.
- Fixed A/B provides a clear benchmark to later measure if the added complexity of a bandit algorithm actually yields better learning outcomes.
- Retaining the bandit design (with propensity logging and ε-greedy minimums) ensures we have a safe transition plan if the baseline proves insufficient.

*Negative Impacts*:
- Delays the implementation of adaptive, learning-first policy allocation.

**Alternatives Considered**:
- We originally proposed making the contextual bandit the core engine and rejecting fixed A/B. This was overridden based on feedback to ensure we first prove a simple rule-based baseline and only incur the operational overhead of a bandit if justified by a runnable comparison.
