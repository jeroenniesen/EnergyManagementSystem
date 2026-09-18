# Layered test hardening

**Status:** Approved design

Add deterministic property-style and contract coverage around the architecture boundaries already
implemented. Use the existing pytest stack without adding a dependency or changing production code.

## Scope

- Planner/safety invariants across generated bounded inputs.
- API contract matrix for empty, stale, unavailable, and unauthorized responses.
- Dry-run, AUTO fallback, command cap, and shutdown end-to-end safety checks.

## Non-goals

No production behavior, dependency, schema, or deployment changes.

