# 0004 - Contextual Multi-Armed Bandit vs Fixed A/B Testing

**Status**: Accepted
**Date**: 2026-09-26

**Context**: 
Choosing the allocation policy for notification experiments to improve student engagement without falling into the pitfalls of typical bandit algorithms that exploit easy-to-measure proxies (like email opens) rather than actual learning outcomes.

**Decision**:
Selected a contextual multi-armed bandit for notification policy selection with propensity logging and a minimum exploration floor, rather than fixed A/B allocation. 
*Note: This bandit policy is NOT used for core reward rules (XP amounts, decay rates), which stay fixed and rule-versioned.*

**Consequences**:
*Positive Impacts*:
- Allows adaptive policy selection while mitigating the risk of the bandit converging on an arm before statistically significant evidence is available (as surfaced in literature review paper 4).
- Mitigation 1: Propensity score logging on every Experiment Assignment event allows for inverse-propensity scoring re-weighting, instead of trusting raw bandit outcomes at face value.
- Mitigation 2: Capping exploitation with a minimum exploration floor (e.g., ε-greedy with ε ≥ 0.1) for the first two weeks ensures no arm is starved of data before having enough exposures to trust the comparison.

*Negative Impacts*:
- Slightly more complex assignment and evaluation infrastructure compared to simple fixed A/B testing.

**Alternatives Considered**:
- Fixed A/B testing was considered but rejected in favor of the adaptive nature of a bandit.
- A naive bandit algorithm was considered but rejected due to the risk of exploiting too early based on proxies.
