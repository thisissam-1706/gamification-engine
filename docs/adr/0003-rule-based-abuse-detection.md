# 0003 - Rule-Based Abuse Detection

**Status**: Accepted
**Date**: 2026-09-26

**Context**: 
Choosing between a rule-based approach and a real-time ML anomaly-detection model for abuse detection (such as scripted point farming or impossible timing exploits).

**Decision**:
Selected rule-based, retroactive, asynchronous abuse detection. 

**Consequences**:
*Positive Impacts*:
- Operates asynchronously on the event stream, so it does not interfere with real-time event ingestion.
- Compatible with deterministic replay requirements (unlike an ML model which adds latency and a training/retraining lifecycle).
- Feasible for an 8-week team building its first event-sourced system. Does not require labeled abuse data to validate a model's false-positive rate.
- Rule sets are auditable, testable, and directly traceable to the evidence-event-ID requirement.

*Negative Impacts*:
- May be less adaptive than ML models to novel abuse patterns out of the box.

**Alternatives Considered**:
A real-time ML anomaly-detection model was considered but rejected for the current scope. It is treated as a stated future extension.
