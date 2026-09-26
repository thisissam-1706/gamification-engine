# Contributing

## Branch Naming
- `feat/<milestone>-<short-desc>` (e.g., `feat/m1-ingestion-dedup`)
- `fix/<issue>-<desc>`
- `docs/<topic>`

## Commit Message Style
Use Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`) with milestone tag in scope.
Example: `feat(m1): add dedup filter to ingestion pipeline`

## ADRs
New ADRs go in `docs/adr/` with the next sequential number. Use the standard format:
- Status
- Context
- Decision
- Consequences
- Alternatives Considered

## How to Add a New Event Type
1. Add placeholder schema in `schemas/events/`
2. Define payload in design doc
3. Add event type to `event_type` enum/pattern
4. Create corresponding consumer handler in `src/`
5. Add acceptance test in `tests/`
