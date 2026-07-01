# BACKLOG — HEMS Product Backlog

*Product-owner backlog, 2026-07-02. Owner: Jeroen. Groom roughly weekly; re-cut horizons when a NOW item ships.*

**Product goals every item must serve at least one of:**
**€** lower energy bill · **CO₂** lower footprint · **Motivation** the household sees progress and is nudged to improve · **Trust** the system is honest, safe, and explains itself.

**How this document relates to the rest:** [`GOAL.md`](./GOAL.md) is the why, [`SPEC.md`](./SPEC.md) is the how (source of truth — update it deliberately when an item changes behaviour), [`docs/2026-07-01-product-roadmap.md`](./docs/2026-07-01-product-roadmap.md) (features F1–F7) and [`docs/2026-06-28-emotional-design-review.md`](./docs/2026-06-28-emotional-design-review.md) are the research this backlog consolidates. Items reference their source.

**Legend:** Type = Feature / UX (emotional design) / Refactor · Effort: S ≈ days, M ≈ weeks, L ≈ 1–2 months.

**Decisions baked into this backlog (2026-07-02):**
- Motivation style = **quiet personal motivation** — trends, honest wins, calm tone. No badges, confetti, or leaderboards (conflicts with the "quiet competence" tone; see the emotional-design review's risks).
- Family channel = **the iOS app** (`ios-dashboard-chat` branch), brought to emotional-design parity with the web UI. Push notifications and MQTT/HA publishing are deliberately LATER.
- Refactoring is low priority **except** where it touches the control path or observability (B-24).

---

## NOW — make the goals measurable and visible

### B-01 · Ship the Insights branch — ✅ Shipped 2026-07-02 (PR #1)
`feat/insights-reporting` merged to main: scores, `/api/report`, Insights view, energy-story polish, sky backdrop. Remaining tail: confirm it's deployed to the Mac Mini.

### B-02 · Gas + CO₂ visibility — Feature · S · CO₂/Motivation
Ingest P1 `total_gas_m3` (the HomeWizard P1 already sends it; today it is thrown away), store it with history retention, and fold it into the CO₂ score — which already accepts `gas_m3` and is specified to "step down" honestly when gas first appears (see the insights design spec §②). Show gas use, € and kg CO₂ on the dashboard/Insights.
**Why now:** gas is ~2.5× electricity on both € and CO₂; and every week of delay is history we can never backfill.
**Done when:** gas readings stored; CO₂ score includes the gas term with the step-down annotation ("gas heating is X% of your footprint"); gas visible in Insights per period. *(Roadmap F1)*

### B-03 · Measured savings — Feature + UX · M · Trust/€/Motivation
Replace the plan-based "Saved today" estimate (`ems/savings.py` self-describes as "rough, illustrative") with savings computed from **recorded** energy versus a no-EMS counterfactual (reuse the "no-shifting replay" already specified for the best-price €). Until measured savings land: label the tile "estimated" and stop letting "€0.00" dominate the dashboard (emotional-design review: never overpromise savings).
**Done when:** savings derive from stored history; the UI distinguishes measured vs estimated; week/month savings appear in Insights.

### B-04 · iOS app catch-up + emotional-design parity — Feature + UX · M · Motivation
The `ios-dashboard-chat` branch is several commits/PRs behind. Rebase it onto main (after B-01) and bring over the web UI's emotional design: home-state headline, the three score cards (ring + motivating copy), energy-story visual language, calm tone throughout. Keep it read-only + chat; no control from the phone yet.
**Done when:** iOS app builds against current main, shows scores + home state + energy story with the same copy/tone as the web, and is on family phones.

### B-05 · Post-2027 economics — Feature · S–M · €
Prepare the planner for net-metering (saldering) ending 1 Jan 2027: self-consumption-first weighting, export-price awareness, never export (charge/curtail instead) at negative midday prices, native 15-min slots. Ships behind dry-run first, per the standing rule — and dry-run acceptance takes calendar weeks, so start well before the deadline. *(Roadmap F2)*
**Done when:** planner decisions reflect export price; dry-run comparison over multiple days reviewed; enabled live.

### B-06 · Score trends: "you vs last week/month" — UX · S · Motivation
The insights design spec promised trend spark-lines in v1 — verify they actually ship, and add period-over-period comparison ("3 points better than last week") to the score tiles and Insights headline. This is the heart of quiet motivation: progress you can watch.
**Done when:** each score tile shows a trend spark + a truthful comparison line; empty-history states degrade gracefully.

---

## NEXT — deepen motivation and CO₂ honesty

### B-07 · Weekly recap — Feature + UX · M · Motivation
"Your week in energy": best day, the three scores with deltas, € saved, one concrete improvement suggestion. In-app (web + iOS), quiet tone. This defines which moments would ever deserve a push notification (B-20).

### B-08 · Quiet success markers in Energy Story — UX · S · Motivation/Trust
"Reserve respected", "evening peak covered from the battery", "no grid top-up needed" — shown only when true, from recorded data. *(Design review P2)*

### B-09 · Emotionally complete failure states — UX · S · Trust
Every warning/error answers three questions: is the battery safe, what will the EMS do now, what can I do. Shipped on the System page; still missing on the alerts strip and API-error banners. *(Design review P0 remainder)*

### B-10 · Carbon reporting via NED.nl — Feature · S–M · CO₂/Trust
Replace the fixed 0.27 kg/kWh factor with the NED.nl **average** NL grid-intensity signal (never marginal — see roadmap myth-busts) for the CO₂ raw number and an intensity curve in the UI/explainer. *(Roadmap F3)*

### B-11 · Heating recommendations (advice-only) — Feature · S · CO₂/€/Motivation
Surface the proven gas levers as recommendations: "zet 'm op 60" (CH water temperature), hydraulic balancing (waterzijdig inregelen, 10–15%), weather-appropriate setback guidance. No control, no hardware needed — the cheapest lever on the biggest CO₂ prize. *(Roadmap F7)*

### B-12 · Setup vs Settings split — UX · M · Trust
A guided commissioning flow (connect devices → validate meters → solar array → battery read test → safe defaults → readiness checklist) separate from day-to-day Settings. Also closes the SPEC M0b setup-wizard/map gap. *(Design review P1)*

### B-13 · Long-horizon history rollups — Feature enabler · S–M · Motivation
Monthly/weekly aggregate tables so year-over-year trends survive the 365-day raw-sample purge. Without this, next summer's "vs last year" comparison silently breaks.

### B-14 · Solcast forecast provider — Feature · S · €/Trust
Real P10/P50/P90 percentiles (SPEC's primary provider, unbuilt) instead of the derived 0.6×/1.15× bands in `forecast_solar.py`. Improves risk-aware grid-charge sizing — the derived P10 haircut has already caused over-buying once.

### B-15 · Strategy auto-switch hysteresis + sunset deadline — Feature · S · €
`select_strategy` currently switches summer/winter by calendar month only. Add the SPEC §8.4 rolling solar threshold + hysteresis days, and real sunset deadlines (`astral`). Fewer wrong-season plans in shoulder months.

### B-16 · Missed-window recovery — Feature · M · €/Trust
SPEC §8.12 (`planner/recovery.py`): charge-completion checks and a catch-up path when a cheap window is missed (outage, stale data, dwell block). Today a missed window has no recovery.

---

## LATER — the big levers, each with a trigger

### B-17 · EV smart charging + battery-HOLD coordination — Feature · L · €/CO₂
Solar-surplus + dynamic-price charging with a departure deadline; coordinated battery HOLD (the car-guard, hardened this month, becomes part of a real EV strategy). ~€250–700/yr, and self-consumption is worth more post-2027. **Trigger:** NOW items live; requires Tesla auth/BLE decision. *(Roadmap F4, `docs/v2-ev-control.md`)*

### B-18 · HA client + MQTT publishing — Feature · M · enabler
WebSocket/REST HA read client + publish EMS state/scores as HA entities (SPEC'd, entirely unbuilt). Enables HA dashboards/automations/voice — and is the prerequisite for B-19. **Trigger:** committing to heating control, or a real need for HA automations.

### B-19 · Heating control on the gas boiler — Feature · L · CO₂/€
OpenTherm-GW/Plugwise: weather-compensated low-temp curve, price/CO₂-aware setback, DHW eco (≥60 °C safety). The biggest absolute prize (~15–25% of gas, ~€250–420/yr, ~320–535 kg CO₂/yr) — and comfort-critical, so fail-safe design first. **Trigger:** B-18 done + B-11's recommendations acted on. *(Roadmap F6)*

### B-20 · Push notifications — Feature · S–M · Motivation
ntfy/HA-companion pushes for genuine wins and warnings. **Trigger:** B-07 shows which moments deserve interruption; keep it rare and calm.

### B-21 · Dutch as a first-class language — UX · M · Trust
Safety-critical copy reads more trustworthy in the household's native language. Full-UI Dutch pass, starting with failure states and override consequences. *(Design review)*

### B-22 · Projected-SoC gating in the plan validator — Feature · S–M · Trust
SPEC §8.5's "later step": use the SoC projection (already computed for display) to reject/adjust plans pre-apply.

### B-23 · Deferred with roadmap triggers — Feature · L
ML layer (accelerator-gated, Jetson); hybrid heat-pump price/CO₂ switchover (**trigger:** heat pump installed); OCPP wallbox (**trigger:** wallbox purchased); ToU/peak-aware planning (**trigger:** NL ToU tariffs ~2029). *(Roadmap deferred list)*

### Deliberately not doing
Badges, confetti, pressure-style streaks, per-person leaderboards, cute error states, celebration of battery cycling for its own sake — all conflict with the quiet-competence tone (emotional-design review, decision 2026-07-02).

---

## REFACTORING — low priority except where safety/observability is touched

*Priority scale for this section: P1 = schedule soon · P2 = do when the code is touched anyway · P3 = opportunistic.*

### B-24 · Control-path & observability exceptions — Refactor · S · **P1** · Trust
(1) Inspect the silent `except Exception: pass` at `ems/control/mode_controller.py:208` — it sits in the control path. (2) Add log lines to the ~32 silent `except: pass` sites (concentrated in `web/api.py`): a systematically failing explainer, cache, or enrichment path is invisible today.

### B-25 · Split `web/api.py` — Refactor · M · P2
2,312 lines; ~2,066 of them inside one `create_app()` closure; 35 routes; domain logic (`compute_charge_need`, `project_energy`, `_charge_kind`) executed inside route handlers. Split into routers + a thin service layer, **incrementally, as routes get touched** — not as a big-bang rewrite.

### B-26 · Reconcile SPEC with reality — Refactor/docs · S · P2 · Trust
SPEC mandates HA as the required integration hub (`entity_map`, WS/REST) and a central `ports.py`; the code reads devices directly and has no ports file — and works well that way. Per CLAUDE.md, drift must be resolved deliberately: update SPEC §5/§9/§13 (and CLAUDE.md) to describe the direct-device architecture, keeping the HA client as the B-18 backlog item.

### B-27 · Dead & duplicated planner logic — Refactor · S · P3
Remove or wire `planner/optimal.py` (tested but never dispatched); deduplicate `api._charge_kind` vs `energy_flow._allocate_slot` (a self-admitted mirror that can drift).

### B-28 · Frontend consolidation — Refactor · S–M · P3
A shared API client (11 components call `fetch` directly, each repeating auth/error handling); split the 663-line `App.tsx` (15 `useState`/5 `useEffect`: routing, polling, auth, modals in one file).

### B-29 · Test gaps — Refactor · S · P3
`main.py` wiring untested; `test_sources.py` is a 10-line stub; visual-regression baselines lighter than the SPEC implies. Coverage is otherwise excellent (66 test files, no skips).
