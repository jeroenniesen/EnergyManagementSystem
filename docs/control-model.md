# Control model — intent, plan & runtime lifecycle

> Companion to `../SPEC.md` §7 (modes/intent), §8 (decision logic) and §13 (internal structure). This is the implementer-level reference for the **control plane**: the intent layer, target-SoC math, deadline planning, the Plan domain object, plan validation, and the runtime state machine. The spec carries the decisions and key tables; this doc carries the detail.

## 1. The cardinal contract — Indevolt owns P1 zeroing

The Indevolt, when paired with the P1/CT meter, runs its **own fast self-consumption controller** that modulates power to keep net grid flow near zero ("P1 zeroing"). The EMS is a **mode/intent switcher, not a power-tracking loop** — it must **never fight that controller**:

- In `ALLOW_SELF_CONSUMPTION` the vendor controller runs; the EMS does **nothing per-cycle** beyond confirming the mode.
- The EMS only *overrides* vendor behaviour for an explicit reason (grid-charge to a target, hold reserve, serve load in an expensive window) and reverts to letting the vendor run as soon as the reason lapses.
- The EMS **does not repeatedly correct minor live-power deviations** (`SPEC §2`, "don't fight vendor control"). No proportional/PID-style nudging.

**Whether P1 zeroing remains active in each mode is hardware behaviour we VERIFY and STORE at M1**, not assume. It lives in the `CapabilityReport` (§6) and the §2 compatibility matrix's "P1-zeroing active?" column.

## 2. BatteryIntent (planner vocabulary) → physical mode → vendor action

The planner emits **`BatteryIntent`**, never a raw vendor command. The mode controller maps intent → physical mode → the probe-resolved vendor action.

| `BatteryIntent` | Carries | Physical mode | Vendor action (probe-resolved) | P1-zeroing active? (CONFIRM@M1) |
|---|---|---|---|---|
| `ALLOW_SELF_CONSUMPTION` | — | `AUTO` | energy-mode select → self-consumption | **YES** (vendor tracks P1) |
| `GRID_CHARGE_TO_TARGET` | `target_soc`, `deadline`, `power` | `CHARGE` | `indevolt.charge {power, target_soc}` | **NO** (forced charge) |
| `HOLD_RESERVE` | `allow_solar_charge` | `IDLE` | standby button / discharge-floor = current SoC + AUTO | **N/A / partial** |
| `DISCHARGE_FOR_LOAD` | `floor_soc`, `deadline` | `DISCHARGE` | `indevolt.discharge {…}`, capped at house load | **partial / NO** |

Notes:
- **`HOLD_RESERVE.allow_solar_charge`** (an intent field; set from the config key **`hold_reserve_blocks_solar_charge`** — note the **inverse polarity**) decides whether holding reserve still lets *solar* top the battery up (summer "build toward sunset") or blocks all charge (pure freeze). See `SPEC §7`.
- **Standby vs. self-consumption-disabled:** if the probe finds the battery exposes *both* a true standby/hold *and* a "self-consumption off" state, prefer the one that holds SoC without exporting; record which in the `CapabilityReport`.
- **IDLE-emulation caveats** (when no true standby — `SPEC §7.2`): the *discharge-floor = current SoC + AUTO* emulation only **blocks discharge** — with `allow_solar_charge` true, solar can still drift SoC *up* (fine for "build toward sunset", wrong for a pure freeze → then also block charging). The *charge-to-current-SoC* emulation may **not latch** (a charge that's "already complete" can revert to the prior mode), so prefer the standby button when the probe confirms it holds.
- **`DISCHARGE_FOR_LOAD` is not a power-tracking loop.** Serving "exactly the load" is done by the vendor's self-consumption controller (P1-zeroing in discharge — **CONFIRM@M1**), not by the EMS rewriting power each cycle (which `SPEC §2` forbids). The force-discharge *service* is for deliberate export only; if the vendor won't serve-load in forced discharge, "discharge during the peak" degrades to leaving the battery in self-consumption drawing down storage.

## 3. Preconditions (checked before every overriding action)

A `GRID_CHARGE_TO_TARGET` / `DISCHARGE_FOR_LOAD` / `HOLD_RESERVE` action is only issued if **all** hold (else fall back to `ALLOW_SELF_CONSUMPTION` and raise the relevant alert):

- battery **online** and reachable;
- local API / HA control path **enabled** (probe succeeded);
- **grid charging allowed** (for charge intents — the "Allow grid charging" switch / config);
- **P1 linked to Indevolt** (paired-meter check, §6 / `SPEC §6.5`);
- **SoC valid** (plausible, fresh — §5 plausibility);
- inside the **startup grace period?** → defer (§7).

## 4. Target-SoC planning (not just mode planning)

**Energy-unit definitions (pinned — these were ambiguous; fix them here once):**
- **`usable_kwh`** = the energy delivered across the **full 0→100% *reported* SoC range** (the vendor's own top/bottom buffers are already excluded — this is the ~9.7 of 10.8 kWh). So **1% SoC = `usable_kwh`/100 kWh** everywhere.
- **Two distinct "reserve" quantities — they are independent and *stack*:**
  - **`min_reserve_soc`** (%, e.g. 10) — a **floor**: discharge never goes below it. Used in `usable_now_kwh`.
  - **`night_reserve_kwh`** (e.g. 2.0) — a **comfort buffer added to *demand*** (summer overnight need, `SPEC §8.2`), *not* a SoC floor. (Consider reading it as `comfort_buffer_kwh`.) Mapping one onto the other would over-charge or discharge into the buffer — keep them separate.
- **Round-trip efficiency is consumed in *exactly one* place — the economics (`SPEC §8.3`, `charge_price / round_trip_efficiency`).** The **projected-SoC curve (`SPEC §8.5`) applies the loss once more there only for the SoC trajectory** — it must not re-apply the round-trip factor to the *price* math. Don't let losses bite the same energy twice.
- **`evening_reserve_kwh`** = the energy the evening discharge windows must serve (their `required_kwh`); the morning-peak discharge floor is sized so projected SoC entering the evening peak stays ≥ this above `min_reserve_soc`. (Replaces any magic "reserve ≥40%".)
- **Equivalent full cycle** (for `max_cycles_per_day`/`_month`) = **(kWh charged + kWh discharged) / (2 × `usable_kwh`)**.

Every charge window has a **target SoC derived from required energy**, and the planner reasons in kWh:

```text
usable_now_kwh = usable_kwh * (soc - reserve_soc) / 100            # energy available above reserve
required_kwh   = max(0, needed_kwh - usable_now_kwh)               # extra energy to acquire for the window(s)
target_soc     = soc + (required_kwh / usable_kwh) * 100
target_soc     = clamp(target_soc, reserve_soc, season_ceiling_soc)  # never above the season/longevity ceiling
```

- **`needed_kwh`** = energy the upcoming committed windows must serve (overnight need in summer; profitable discharge windows in winter), each sized off **P10** when it's a commitment (`SPEC §8`).
- **Season ceilings** (`target_soc_ceiling` per season, e.g. 90–95%): don't charge above the ceiling unless explicitly needed — preserves cell life. Configurable (`SPEC §9`).
  > **Status (2026-07):** *not yet enforced in code, by design.* The demand-aware planners already size the target to the committed **need** and never charge above it (they don't gratuitously fill to 100%), and the "unless explicitly needed" rule means a real overnight/peak requirement must be allowed to breach any ceiling — so a planner-side clamp is a no-op in normal operation. The remaining high-SoC exposure is **solar self-consumption** filling the pack on sunny days, which the vendor controls in self-consumption mode; capping *that* for cell health would need an active HOLD-at-ceiling control behaviour. The SolidFlex uses **semi-solid cells**, which the vendor rates as low-degradation and stable at high SoC, and its own BMS / self-consumption controller owns cell protection (SPEC §2, "set intent, don't fight vendor control"). So an EMS-side SoC ceiling is **not planned** — it would cost usable overnight capacity for negligible health gain.
- **Minimum top-up** (`min_grid_charge_kwh`, e.g. 0.5 kWh): don't schedule tiny inefficient grid charges; if `required_kwh` is below it, either skip or wait for solar.
- **Maximum daily grid charge** (`max_daily_grid_charge_kwh`): hard cap so a bad forecast/config can't over-buy.
- **Partial charge is normal:** a window need not fill the battery — it charges to `target_soc` and no more.

**Worked example (winter):** capacity 9.7 kWh usable, SoC 30%, reserve 10%, two profitable peaks need 6 kWh.
`usable_now = 9.7*(30-10)/100 = 1.94 kWh` → `required = 6 - 1.94 = 4.06 kWh` → `target_soc = 30 + (4.06/9.7)*100 = 72%` (≤ 90% winter ceiling ✓). Schedule cheapest pre-peak slots to reach **72%**, not the ceiling.

## 5. Deadline-driven planning

Charge is scheduled to **complete by a deadline**, not by an arbitrary replan time:

- **Summer:** `target_soc` (overnight need) must be reached **by sunset** (`astral`). Schedule charge/solar accumulation to finish before sunset; if solar alone reaches target before the deadline, **don't grid-charge** (rec: "charge only if target can't be reached by solar before deadline").
- **Winter:** `target_soc` must be available **before the first expensive period** (morning peak). Charge windows are the cheapest slots *before* that deadline.
- **Remaining-day solar estimate** (separate from the full-day forecast): on intraday replans, use forecast solar **from now → deadline** to decide whether grid charging is still needed. A sunny afternoon ahead can cancel a planned grid charge.
- **"Don't grid-charge if surplus arrives soon"**: if the remaining-day solar estimate will reach `target_soc` before the deadline, skip grid charging.

## 6. CapabilityReport (from the battery adapter)

The M1a probe produces a stored `CapabilityReport`:

```text
CapabilityReport:
  services_available:    [charge, discharge]          # what indevolt.* exposes
  charge_params:         {power: bool, target_soc: bool}
  energy_mode_options:   [self_consumption, ...]       # select entity options
  has_standby_button:    bool
  has_self_consumption_off: bool                       # distinct from standby?
  discharge_floor_number: entity_id | null
  grid_charge_switch:    entity_id | null
  observed_power_min_w / max_w: int
  p1_paired:             bool                           # is Indevolt reading the P1 meter?
  p1_zeroing_active_by_mode: {AUTO: bool, CHARGE: bool, DISCHARGE: bool, IDLE: bool}  # VERIFIED at M1
  probed_at:             tz-aware datetime
```

`battery.py` builds its intent→action mapping (§2) from this; `null`/missing capabilities push that intent to the RPC fallback or to emulation (`SPEC §7.1` IDLE emulation).

## 7. Runtime lifecycle & ownership state machine

```
            ┌─────────────┐  boot
            │  INACTIVE   │
            └──────┬──────┘
                   │ start
                   ▼
            ┌─────────────┐  validate sensors + capability probe
            │  OBSERVING  │  (read-only; build/restore plan; NO commands)
            └──────┬──────┘
        grace ok + │ valid plan
        dry_run?   ▼
        ┌──────────┴───────────┐
        │                      │
   dry_run=true           dry_run=false
        │                      │
        ▼                      ▼
  ┌───────────┐          ┌─────────────┐
  │  DRY_RUN  │          │ CONTROLLING │
  │ (log only)│          │  (commands) │
  └───────────┘          └──────┬──────┘
                                │ manual override (UI/HA) / pause-until-tomorrow
                                ▼
                        ┌──────────────────┐
                        │ MANUAL_OVERRIDE  │ (respect, with expiry)
                        └──────────────────┘
```

- **Boot sequence (33):** `INACTIVE → OBSERVING`: validate HA entities (`SPEC §11.5`), run the capability probe, load the last persisted plan (or build a fresh one), **capture the battery's original vendor mode** (for restore, §8). Only then consider acting.
- **Startup grace period (34, `startup_grace_seconds`):** while inside it, stay in `OBSERVING` and issue **no** commands — lets HA entities settle after a restart so the EMS doesn't act on half-populated state.
- **Persistent per-day switch counter (31):** stored, keyed by **local date**; survives restarts so `max_mode_switches_per_day` isn't reset by a reboot.
- **Persistent unresolved warnings (32):** alerts survive restarts until cleared.
- **`last_action_requested` / `last_action_confirmed` (29):** persisted so a restart mid-action knows what was in flight.

## 8. Restore original vendor mode

At boot the EMS records the battery's **original energy-mode/state**. On `Return to Indevolt default` (UI button, rec 40), on `Pause EMS until tomorrow`, or on graceful shutdown, the EMS restores that original mode (or plain `AUTO` self-consumption if unknown) so manual EMS testing never leaves the battery in a surprise state.

## 9. The Plan domain object & validation

```text
Plan:
  id / version:        monotonic; every ActionDecision references the plan that produced it
  created_at:          tz-aware
  strategy:            SUMMER_SOLAR | WINTER_ARBITRAGE | MANUAL
  input_snapshot:      PlannerInputSnapshot (saved for audit)
  slots:               [ {start, end, intent, target_soc?, reason} ]   # 15-min, non-overlapping
  projected_soc:       [ {t, soc} ]                                    # efficiency-aware
  confidence:          0..1
  data_quality:        complete | degraded | forecast_only | price_fallback | unsafe
  deadlines:           {summer_sunset?, winter_first_peak?}

PlannerInputSnapshot:   prices(+resolution+provenance), forecast(+issue time+provider+percentiles),
                        baseline, soc, capability_report_ref, config_hash, taken_at

ActionDecision:         plan_id/version, desired_intent, mapped_command, reason,
                        preconditions_checked, outcome (requested/confirmed/failed)
```

**Plan validator (35) — a plan is rejected (→ keep prior / fail safe) unless ALL hold** (must match `SPEC §8.11` exactly; runs identically whether the plan came from the rule-based or the ML planner):
- slots are **non-overlapping** and cover the horizon;
- every slot duration **≥ `min_mode_dwell_seconds`** (no sub-dwell flapping);
- every `target_soc` is within `[reserve_soc, ceiling_soc]`;
- each charge window's **energy is feasible** within its slots at `max_charge_w` (and reaches `target_soc` by its deadline);
- projected SoC never **< reserve** or **> capacity**, and reserves enough for the evening peak (≥ `evening_reserve_kwh`, §4);
- **no action is scheduled off stale inputs** (data_quality ≠ `unsafe`);
- the plan's mode switches fit the **remaining same-day budget** (`max_mode_switches_per_day` − the persisted `switches_today`, §7) — not just the per-day total, so a mid-day replan can't schedule switches the runtime will then refuse.

**Plan confidence & data-quality badge (36, 50):** derived from forecast age, price completeness, per-signal sensor freshness, and forecast spread (P90−P10). Low confidence downgrades the badge; `unsafe` blocks all overriding actions (→ `ALLOW_SELF_CONSUMPTION`).

**Plan invalidation reasons (37) → trigger a replan:** new prices, new forecast, SoC deviation beyond threshold, manual override, missed/failed command. **Max replan frequency (38, `min_replan_interval_seconds`)** prevents churn.

**Planned-vs-actual SoC deviation (39):** each cycle compares actual SoC to `projected_soc(now)`; if `|Δ| > soc_deviation_replan_pct`, invalidate and replan.

## 10. Missed-window recovery & charge completion

- **Charge completion (5):** if actual SoC reaches `target_soc` before the charge window ends → transition `GRID_CHARGE_TO_TARGET → HOLD_RESERVE` (or `ALLOW_SELF_CONSUMPTION`) for the remainder. Don't keep forcing charge.
- **Missed-window recovery (6)** — charge command failed or the Pi was down during the cheap window. On recovery, decide:
  1. **Deadline still reachable?** Compute remaining cheapest slots before the deadline.
  2. **Catch up** if the still-available slots clear the economics (winter) / a strong-solar morning won't cover it (summer).
  3. **Partial catch-up** if only part of `required_kwh` is still affordable/feasible — charge to a reduced `target_soc`.
  4. **Skip** if catching up no longer beats `ALLOW_SELF_CONSUMPTION` (e.g. prices have risen, or solar will cover it).
  Log the decision + reason on the `ActionDecision`.

## 11. Data quality (per-signal, with plausibility)

- **Per-signal staleness (46):** each input (`grid`, `solar`, `ev`, `soc`, `price`, `forecast`) has its **own** freshness state — not one global flag.
- **Source priority per metric (49):** **HA sensor → direct device API → cached value (display only, never for control)**.
- **Plausibility checks (47):** reject/flag implausible readings — SoC can't jump > `soc_max_jump_pct_per_5min` (e.g. 20%/5 min), solar can't be negative, prices must be **chronological** and within sane bounds.
- **Timestamp hygiene (48):** handle duplicate/missing 15-min slots — dedupe by `startsAt`, fill or flag gaps; never silently shift slots.
- **Timezone-aware slots (62):** all slot math uses tz-aware datetimes via shared utilities; **naive datetimes never enter planner code** (DST correctness, `SPEC §14`).

## 12. Testing hooks

- **Fake Indevolt adapter (56):** a `CapabilityReport`-driven in-memory battery for tests — no HA, no hardware. Lets restart/missed-window/deviation tests run deterministically.
- **Scenario runner (51):** CLI that takes `{soc, prices, forecast, expected_load}` and prints the `Plan` — for eyeballing and golden tests.
- **Golden fixtures (52):** `cloudy_summer_topup`, `sunny_no_topup`, `cheap_night_before_expensive_morning`, `missed_charge_window`, plus `actual_soc_below_planned` (55) and `restart_during_charge_window` (54).
- **Target-SoC & feasibility math (53):** unit tests for §4/§9 formulas and window feasibility.

## 13. Optional / later (documented, not core scope)

- **Time-of-day forecast correction (63):** per-hour correction factors (still clamped) instead of one daily `k`.
- **Away/vacation mode (64):** prioritise low cost + high reserve differently while away.
- **Storm / outage reserve mode (65):** on a weather alert or manual flag, hold a high reserve.
- **Learning freeze (66):** exclude flagged unusual days from the consumption baseline so they don't distort it.
- **Weekly export report (67):** one week of plans, actions, savings, and warnings.
