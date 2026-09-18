# Storage and application lifecycle hardening

**Status:** Approved design

Make `StorageContext` the authoritative persistence shutdown boundary. It will visit every
repository, call optional close hooks, isolate failures, and be invoked by the FastAPI lifespan
after background tasks and shutdown audit work stop. Synchronous cache/control-state stores remain
per-operation SQLite users; this slice does not redesign their connections.

## Scope

- Include `control_state` in application storage wiring.
- Close all repositories with optional `close()` methods, collecting failures.
- Wire lifespan shutdown to the storage boundary.
- Add normal, repeated, partial-failure, and repository-accounting tests.

## Non-goals

No control, battery, schema, or deployment changes.

