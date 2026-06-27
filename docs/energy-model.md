# Energy & measurement model + data dictionary

> Companion to `../SPEC.md` §4. This is the authoritative reference for **what every number means, how it's signed, and how derived values are computed.** Get this right before trusting any planner output.

## 1. The cardinal rule

**The HomeWizard P1 meter measures *net grid flow*, not house load.** Anything that treats P1 as "house consumption" is wrong: when solar or the battery is supplying the house, P1 reads low or negative even though the house is drawing power. House load must be **reconstructed** from all sources.

## 2. Sign conventions (fixed, EMS-internal)

Every source is normalised to this one convention. Each source's *native* sign is confirmed during calibration (§5) and recorded in `config.homeassistant.entity_map` notes.

| Internal metric | Unit | Sign | Native source | Notes |
|---|---|---|---|---|
| `grid_power_w` | W | **+ import / − export** | P1 `active_power_w` | already signed correctly on HW P1 |
| `solar_power_w` | W | **≥ 0** (production) | solar kWh meter | **clamp negatives to 0** (production-only); do *not* take magnitude — a reversed-CT or inverter night-draw would mis-sign. Confirm the meter's native production sign + that its import register stays ~0 during calibration |
| `battery_power_w` | W | **+ discharge / − charge** | Indevolt power sensor | **normalise** — vendor sign may differ |
| `ev_power_w` | W | **≥ 0** (charging load) | car kWh meter | 0 when not charging |
| `soc_pct` | % | 0–100 | Indevolt SoC sensor | cluster-wide |

## 3. Derived values

```
house_load_w  = grid_power_w + solar_power_w + battery_power_w
non_ev_load_w = house_load_w − ev_power_w        # only subtract EV when ev_power_w > ev_charging_threshold_w
```

**Consistency cases — each must reconstruct to 1000 W of true house demand:**

| Scenario | grid | solar | battery | EV | `house_load_w` |
|---|---|---|---|---|---|
| grid only | +1000 | 0 | 0 | 0 | 1000 ✓ |
| solar covers + exports | −500 | 1500 | 0 | 0 | 1000 ✓ |
| battery covers | +200 | 0 | +800 | 0 | 1000 ✓ |
| charging from grid | +1500 | 0 | −500 | 0 | 1000 ✓ |
| solar + EV charging | +200 | 1500 | 0 | 700 | 1700 total, `non_ev_load_w` = 1000 ✓ |

If any case is off in the field, a **source sign is wrong** — fix it in `load_model.py` normalisation/calibration, never by patching the planner.

## 4. Raw vs. derived storage

The history store keeps them **separate** so a sign/calibration fix lets us re-derive history:

- **Raw tables:** instantaneous `grid_power_w`, `solar_power_w`, `ev_power_w`, `battery_power_w`, `soc_pct`; cumulative `*_import_kwh` / `*_export_kwh` per meter.
- **Derived tables:** `house_load_w`, `non_ev_load_w`, learned baseline (per weekday+hour), forecast-correction factor `k`, projected SoC, computed savings.

## 5. Calibration phase (before any control)

Historical HA Recorder data already contains the battery's *prior* behaviour, so the learned baseline must be reconstructed, not read raw.

1. Run read-only for several days (the M0→M2 dry-run/calibration gate, `../SPEC.md` §14).
2. Verify every sign case in §3 against reality.
3. Seed the **load baseline** (rolling avg of `non_ev_load_w` per weekday+hour, 14-day window) and the **forecast-correction factor** `k = actual_solar / forecast_solar`, clamped to `[0.7, 1.3]`.

## 6. Missing / stale input fallback

| Stale input | Behaviour |
|---|---|
| solar meter | use the solar **forecast** for `solar_power_w`; flag chart |
| car meter | assume `ev_power_w = 0`; widen load uncertainty |
| P1 (grid) | reconstruction unreliable → **fail safe to `AUTO`** + alert |
| battery SoC/power | cannot plan SoC → hold last safe mode + alert |

Any derived value computed from a stale input is flagged in the UI freshness indicators.

## 7. Data dictionary (all internal metrics)

| Name | Unit | Definition |
|---|---|---|
| `grid_power_w` | W | net grid flow, + import / − export (P1) |
| `solar_power_w` | W | PV production (solar kWh meter), ≥ 0 |
| `battery_power_w` | W | + discharge / − charge (normalised) |
| `ev_power_w` | W | EV charging load (car kWh meter), ≥ 0 |
| `soc_pct` | % | cluster state of charge |
| `house_load_w` | W | reconstructed total house demand (§3) |
| `non_ev_load_w` | W | house load excluding EV charging |
| `baseline_w[weekday,hour]` | W | learned rolling-average `non_ev_load_w` |
| `forecast_solar_p10/p50/p90_w` | W | per-slot solar forecast percentiles |
| `k_forecast` | – | rolling forecast-correction factor, clamped `[0.7,1.3]` |
| `price_total_eur_kwh` | €/kWh | Tibber `total` (+ grid-fee adjustments) per 15-min slot |
| `export_value_eur_kwh` | €/kWh | value/cost of exported energy (policy) |
| `net_benefit_eur_kwh` | €/kWh | arbitrage profitability per `../SPEC.md` §8.3 |
| `projected_soc_pct[t]` | % | planner's forward SoC curve (efficiency-aware) |
| `overnight_need_kwh` | kWh | sunset→sunrise load + `night_reserve_kwh` |
| `mode` | enum | `AUTO`/`CHARGE`/`DISCHARGE`/`IDLE` |
| `strategy` | enum | `SUMMER_SOLAR`/`WINTER_ARBITRAGE`/`MANUAL` |
| `savings_eur` | € | per-day & cumulative computed savings |
| `*_fresh` | bool/age | per-source freshness flag + last-update age |

## 8. Sequence descriptions (textual diagrams)

### 8.1 Control cycle (every `cycle_seconds`, default 300 s)
```
loop                EMS                         HA / device
 │  read raw  ───────────────────────────────►  meters, SoC, price-now
 │  reconstruct (§3), stamp freshness
 │  plan stale? ──► (see 8.2 if yes)
 │  desired = plan.mode_at(now)
 │  fresh? ──no──► failsafe AUTO ──────────────► set energy-mode select
 │   │yes
 │  manual override active? ──yes──► do nothing
 │  desired ≠ current AND dwell_ok AND under daily cap?
 │   │yes
 │  dry_run? ──yes──► log decision only
 │   │no
 │  apply ──────────────────────────────────►  indevolt.charge/discharge | standby button | select
 │  confirm (poll a few cycles) ◄──────────────  read state
 │  not confirmed? ──► retry→AUTO→alert
 │  publish status + reason (+ "why not") ─────► UI / MQTT
```

### 8.2 Replan cycle (after ~13:00 prices, dawn, or large deviation)
```
trigger             EMS                              external
 │  fetch prices ──────────────────────────────►  Tibber (cache forward only)
 │  quarter-hourly? else expand hourly→4×15min
 │  complete for tomorrow? ──no──► keep prior plan / failsafe
 │  fetch solar (within budget ledger) ────────►  Solcast → Forecast.Solar
 │  apply correction k (clamped), store provenance
 │  select strategy (month + rolling threshold + hysteresis)
 │  build schedule: rank prices / size to demand
 │     project SoC (efficiency), reserve evening peak,
 │     enforce invariants (reserve, capacity), no-trade if savings<min
 │  publish plan + per-slot reasons ────────────►  UI / MQTT
```
