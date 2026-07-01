# HEMS Product Roadmap — cutting electricity + gas + CO₂

*Product-owner synthesis, 2026-07-01. Built from four research streams: a codebase audit and three
market/domain studies (NL gas/heating, EV + carbon data, NL regulation + competitive landscape).
Confidence tags: **high** = official/primary; **medium** = corroborated commercial; **low** = inferred.*

---

## 1. Strategy in one paragraph

Two forces set the direction. **(a) Net-metering (`saldering`) ends hard on 1 Jan 2027** — no taper —
which makes a self-consumed kWh worth **~3–5× a fed-in one** and drops battery payback from ~13–15 yr
("dumb") to ~6–8 yr **with an EMS that shifts load**. That is a direct tailwind for this project's whole
thesis. **(b) For a family home, gas is the bigger prize** — ~2–2.6× electricity on both € and CO₂, and
~80% of raw household energy. So the roadmap is: **monetize the 2027 pivot and make gas visible now**
(cheap, high-value), **then attack the two big controllable loads** — the EV (electrical) and heating
(gas). Everything stays inside the product's unclaimed white space — the only **local-first,
vendor-agnostic, explainable, safe closed-loop** optimizer (EMHASS optimizes but controls nothing; evcc
only does EV; commercial tools are cloud + walled-garden).

## 2. The prize (reference household)

| | Gas (~1,200 m³/yr) | Electricity (~3,000 kWh/yr) |
|---|---|---|
| Cost | ~€1,680/yr | ~€810/yr |
| CO₂ | ~2,140 kg/yr | ~810 kg/yr (grid-mix) |

Gas ≈ 75% space heating, 20% hot water, 5% cooking. **The environmental win is mostly in gas** — but the
gas levers are the hardest (need a heating integration). Electricity levers (EV, 2027 economics) are more
feasible now. The roadmap sequences by **impact × feasibility**, not impact alone.

## 3. Prioritized roadmap

Effort: S ≈ days, M ≈ weeks, L ≈ 1–2 months. Impact/CO₂ are annual, reference household, with confidence.

| # | Feature | € / CO₂ impact | Effort | Why now | Risk |
|---|---|---|---|---|---|
| **F1** | **Gas + CO₂ visibility** — ingest P1 `total_gas_m3`, store, dashboard (gas kWh-eq, €, CO₂) | Foundational (enables all gas work; behaviour nudge) | **S** | Data already arrives in the P1 feed and is **thrown away**; names the bigger prize | None (read-only) |
| **F2** | **Post-2027 economics** — self-consumption-first; export-price-aware; midday-negative = charge/curtail, never export; native 15-min slots | **High €** (self-consumption worth 3–5× feed-in from 2027) | **S–M** | The macro pivot; reuses the planner; also the **VAT-refund compliance** framing | Low (planner math, dry-run) |
| **F3** | **Carbon reporting** — NED.nl *average* CO₂ signal → "CO₂ avoided" + CO₂ curve in UI/explainer | Transparency; small hard-CO₂ lever in NL | **S–M** | Serves the environmental goal visibly; lays the data pipe | Low |
| **F4** | **EV smart charging + battery-HOLD coordination** — solar-surplus + dynamic-price + departure deadline; battery HOLD on EV-charging (fixes the live "battery drains into car" bug) | **~€250–700/yr** + self-consumption +5–15 pp (worth more post-2027) | **M** | Biggest *controllable electrical* load; fixes a real bug | Medium (Tesla auth/BLE; v2 scope) |
| **F5** | **HA client + MQTT** (enabler) — WebSocket/REST read + publish EMS state | Unblocks heating + broader HA ecosystem | **M** | Prerequisite for F6; both are spec'd-but-unbuilt | Low |
| **F6** | **Heating control on the gas boiler** — OpenTherm-GW/Plugwise: weather-compensated low-temp curve, CH-temp→60 °C, price/CO₂-aware setback, DHW eco | **~€250–420/yr, ~320–535 kg CO₂/yr** (~15–25% gas) | **L** | The **biggest absolute prize** for the gas/environment goal | Med-High (comfort-critical → fail-safe; needs a heat model) |
| **F7** | **Heating recommendations** (non-control) — surface hydraulic balancing (`waterzijdig inregelen`, 10–15%), "zet 'm op 60" | Unlocks F6's savings; ~€170–250/yr if acted on | **S** | Cheap; the precondition that makes low-temp levers deliver | None (advice only) |

### Deferred (design-ready, don't build yet) — with the trigger to revisit
- **Hybrid/full heat-pump price/CO₂-aware switchover** — the *flagship* gas feature (~50–70% gas
  displacement, ~€600/yr, ~30% of home CO₂). It maps **1:1 onto the existing economics-gated planner**
  (run the heat pump when `COP > elec-price ÷ gas-price-per-kWh`). **Hardware-gated:** the household has a
  gas combi. Design the `HvacIntent` now; **activate when/if a (hybrid) heat pump is installed** (ISDE
  subsidised).
- **Carbon-aware *optimization*** (tiebreaker within price-acceptable windows) — in NL cheap↔clean already
  align strongly (price↔renewables r ≈ −0.83; ~1,819 h/yr were both cheap and clean), so the *incremental*
  CO₂ gain over price-only is modest. Do F3 (reporting) first; add the tiebreaker later. **Use an
  average/flow-traced signal, never marginal** (marginal signals *increased* footprint in DK/PL).
- **OCPP wallbox control** — only if the household adds a controllable wallbox (cleaner phase-switching than
  car-side amps). For a Tesla on a "dumb" charger, control the **car via local BLE**.
- **Peak/ToU-aware planning** — NL residential ToU grid tariffs land ~2029 (fallback 2030). Add the
  time-window flags cheaply now so it activates automatically; low urgency (no reward yet).
- **ML layer** — already scoped and accelerator-gated; optional, Jetson-only.

## 4. Recommended next milestone — "See everything, ready for 2027" (F1 + F2 + F3)

All three are **Size S–M, low-risk, high strategic value, and coherent**: they make the whole house
(incl. gas + CO₂) visible and re-point the optimizer at the post-saldering world where self-consumption
and load-shifting are what pay. F1 is the standout quick win (already-flowing data, zero control risk).
F2 is the highest-€ near-term lever and doubles as the battery-VAT-refund compliance story. F3 gives the
environmental narrative a number. Ship behind the usual dry-run/observe gates.

Then **Milestone B = F4 (EV)** — the biggest controllable electrical lever, and it retires a live bug.
Then **Milestone C = F5 → F6/F7 (heating)** — the biggest absolute prize, gated on building the HA client.

## 5. Stakeholder notes
- **Comfort first (household):** heating features must be fail-safe and never trade comfort/hot-water/safety
  for savings — home-type-aware setback (deep setback backfires in high-mass/floor-heated homes), DHW ≥60 °C.
- **Operator (€ + trust):** the 2027 pivot is the wallet story; keep every decision explained ("why NOT
  acting" too) — the differentiator vs EMHASS/commercial tools.
- **Environment:** gas is where the CO₂ is; carbon-reporting makes the electrical side legible.
- **Grid/society:** ToU/peak-awareness is cheap future-proofing for 2029 and aligns with `netcongestie` relief.

## 6. Hard constraints every feature must respect (from SPEC/CLAUDE.md)
Mode-switching not continuous control (≥5 s, <10 writes/day); fail-safe to vendor self-consumption; **one
battery writer** (new controllable devices get their *own* driver/controller, never `ModeController`);
plan in intent + target + deadline; validate + dry-run before live; HA-integrated; privacy/local-first
(minimal redacted external calls); Pi-first (ML accelerator-gated); explainability first.

## 7. Myth-busts surfaced by the research (avoid these traps)
- NL **gas is a *daily* price** (not quarter-hourly) → shift heating *between days*, don't build an intraday
  gas optimizer.
- **No 2026 heat-pump mandate** (scrapped; a 2029 norm is only *proposed*) → don't hard-code forcing.
- A **"smart thermostat" as a device is ~3%**, not the 20–30% marketing claim → the lever is the
  scheduling logic (which the EMS owns), not the hardware.
- **Marginal** carbon signals can *increase* footprint in NW-Europe → average/flow-traced only.
