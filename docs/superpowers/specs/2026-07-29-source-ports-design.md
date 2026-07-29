# Source ports and adapter conformance

**Status:** Approved design

Consolidate the decision-critical source protocols into a typed ports module and prove that current
mock/live adapters satisfy them. Runtime wiring and behavior remain unchanged.

## Scope

- Define/re-export `SourcePort`, `BatteryPort`, `PricePort`, and `ForecastPort` protocols.
- Preserve existing protocol aliases for compatibility.
- Add adapter-conformance tests for mock, HomeWizard/live, Tibber, Forecast.Solar, and Indevolt
  drivers where construction is hermetic.
- Keep battery writes behind the existing `BatteryDriver`/single-writer path.

## Non-goals

No adapter rewrite, network behavior, control behavior, schema, or deployment changes.

