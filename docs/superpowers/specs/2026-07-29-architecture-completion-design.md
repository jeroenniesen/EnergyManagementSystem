# Architecture completion batch

**Status:** Approved design

This batch completes the remaining architectural hardening as five isolated, behavior-preserving
slices: command execution/reconciliation boundaries, typed runtime callbacks, complete nested API
contracts, static adapter signature checks, and E2E safety-test gaps.

## Invariants

- Preserve battery AUTO fallback, dry-run, reserve/dwell/switch caps, writer fencing, and one-writer ownership.
- Preserve API paths, status codes, JSON keys, adapter behavior, and deployment.
- Production behavior changes are prohibited; tests and type boundaries are the primary deliverables.

