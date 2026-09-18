# Replay and EV economic snapshot migration

**Status:** Approved design

Replay and EV advice still call legacy export valuation directly. This slice routes both through
`EconomicSnapshot` compatibility factories while preserving their public configuration, simulation
math, recommendation logic, schemas, and user-facing copy.

## Scope

- Add factories from `ReplayConfig` and EV export settings.
- Replace direct export valuation in replay and EV advice.
- Add parity tests for export models, fees, negative prices, and missing prices.

## Non-goals

No battery behavior, replay schema, EV control, recommendation wording, or API route changes.

