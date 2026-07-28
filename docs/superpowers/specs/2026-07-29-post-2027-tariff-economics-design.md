# Post-2027 tariff economics design

## Goal

Make battery planning and reporting value electricity using the household's actual import and
export economics. The EMS must account for asymmetric grid fees without changing its safety model:
plans remain high-level intent plus target SoC and deadline, and the vendor remains responsible for
instantaneous battery control.

## Contract

The tariff layer accepts a raw electricity price and the configured `grid_fees.*` policy, then
returns effective import and export values. Import adds the configured import fee when the provider's
total does not include all grid fees. Export subtracts the configured export fee and is never treated
as an import-equivalent opportunity. Existing settings retain their current meaning and zero fees
remain backward compatible.

Negative prices are preserved. A negative import price can make charging economically attractive;
an export value below zero is not an instruction to export. The planner may only select a charge or
self-consumption intent that is already permitted by its existing reserve, deadline, validator, and
dry-run gates.

## Architecture

`ems/tariffs.py` contains the pure normalization function and a small immutable value object. The
planner receives normalized slots, while reporting and savings use the same function so the UI and
control decisions cannot disagree. The API exposes the applied policy and a short explanation in
plan/report metadata. No device writer, control loop, or carbon signal is changed.

## Acceptance criteria

1. Zero-fee settings reproduce current planner and reporting values exactly.
2. Import fees increase effective import cost only when `tibber_total_includes_all` is false.
3. Export fees reduce export value and never turn export into a charge opportunity.
4. Negative import prices remain negative; negative export values are represented honestly and do not
   cause forced export.
5. Planner, savings, report, API, and frontend tests cover one-sided fees, both-sided fees, legacy
   settings, and missing price data.
6. The feature is read-only with respect to battery hardware and remains compatible with dry-run.
