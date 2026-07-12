# BACKLOG — HEMS Product Backlog

*Product-owner backlog, 2026-07-02; multi-level since 2026-07-03 (see
[`docs/superpowers/specs/2026-07-03-backlog-sync-design.md`](./docs/superpowers/specs/2026-07-03-backlog-sync-design.md)).
Owner: Jeroen. Groom by editing this file, then run `/backlog-sync` to mirror to GitHub.*

**Product goals every item must serve at least one of:**
**€** lower energy bill · **CO₂** lower footprint · **Motivation** the household sees progress and is nudged to improve · **Trust** the system is honest, safe, and explains itself.

**How this file relates to the rest:** [`GOAL.md`](./GOAL.md) is the why, [`SPEC.md`](./SPEC.md) is the how (source of truth — update it deliberately when an item changes behaviour), [`docs/2026-07-01-product-roadmap.md`](./docs/2026-07-01-product-roadmap.md) (features F1–F7) and [`docs/2026-06-28-emotional-design-review.md`](./docs/2026-06-28-emotional-design-review.md) are the research this backlog consolidates.

## How this file works

- **Three levels:** `EPIC E-xx` (a feature spanning sprints) → `B-xx` items (one-sprint slices;
  a sliced feature keeps its number with a letter: B-04a/b/c) → a **sprint** bucket.
- **Sprints are numbered, date-less** (Sprint 1 = current). `Pool` = groomed, not scheduled.
- Every item carries a **Track:** line — `Sprint · parent epic · GitHub #issue · PR #n` — with
  status glyphs ⬜ todo · 🔄 in progress / PR open · ✅ done.
- **Sync contract:** *content* flows local → GitHub (this file is the source of truth for text,
  slicing, epic membership); *status + sprint assignment* flow GitHub → local (close an issue or
  merge a PR and `/backlog-sync` marks it here). Sprint = milestone; item = issue (label
  `backlog`); epic = issue with a task list. Only epics + sprint-assigned items sync — pools stay
  markdown-only.
- Effort: S ≈ days, M ≈ weeks, L ≈ 1–2 months. Type = Feature / UX / Refactor / Bug.

**Decisions baked in (2026-07-02/03):** quiet personal motivation — no badges/confetti/
leaderboards; iOS app is the family channel; refactoring low-priority except control-path/
observability (B-24); numbered date-less sprints, Issues+Milestones on GitHub.

---

## Sprint board

| Epic | Sprint 1 (current) | Sprint 2 | Sprint 3 | Pool |
|---|---|---|---|---|
| **E-01 · Honest CO₂ picture** | ⬜ B-02 gas | | | B-10 |
| **E-02 · Measured money** | 🔄 B-03a history | ⬜ B-03b tile | ⬜ B-13 rollups | |
| **E-03 · Family reach: iOS** | | ⬜ B-04a ⬜ B-04b | ⬜ B-04c | |
| **E-04 · 2027-ready planner** | 🔄 B-30 valley fix | | ⬜ B-05 economics | B-15 B-16 B-22 |
| **E-05 · Quiet motivation** | ⬜ B-06 trends | | ⬜ B-08 markers | B-07 |
| **E-06 · Trust & guidance** | | ⬜ B-31 marker | | B-09 B-12 B-21 |
| *Big levers (pool)* | | | | B-17 B-18 B-19 B-20 B-23 |
| *Refactoring (pool)* | | | | B-24 B-25 B-26 B-27 B-28 B-29 |

---

## EPIC E-01 · Honest CO₂ picture
*Goal: the CO₂ story covers the whole house — gas included — with a defensible grid factor.* (CO₂/Motivation)

### B-02 · Gas + CO₂ visibility — Feature · S
Ingest P1 `total_gas_m3` (the HomeWizard P1 already sends it; today it is thrown away), store it with history retention, and fold it into the CO₂ score — which already accepts `gas_m3` and is specified to "step down" honestly when gas first appears (insights design spec §②). Show gas use, € and kg CO₂ on the dashboard/Insights.
**Why now:** gas is ~2.5× electricity on both € and CO₂; every week of delay is history we can never backfill.
**Done when:** gas readings stored; CO₂ score includes the gas term with the step-down annotation; gas visible in Insights per period. *(Roadmap F1)*
**Track:** Sprint 1 · E-01 · ⬜

### B-10 · Carbon reporting via NED.nl — Feature · S–M
Replace the fixed 0.27 kg/kWh factor with the NED.nl **average** NL grid-intensity signal (never marginal — roadmap myth-busts) for the CO₂ raw number and an intensity curve in the UI/explainer. *(Roadmap F3)*
**Track:** Pool · E-01 · ⬜

## EPIC E-02 · Measured money
*Goal: every € the app shows is measured from recorded history, never promised from a plan.* (€/Trust/Motivation)

### B-03a · Measured savings history — Feature · M
Per-day financial history measured from recorded samples + stored prices: grid cost, battery wear, saved vs the no-battery baseline; `price_slots` + `daily_finance` storage, `/api/finance`, Insights money section with honest coverage caveats.
**Track:** Sprint 1 · E-02 · 🔄 [PR #3](https://github.com/jeroenniesen/EnergyManagementSystem/pull/3)

### B-03b · Dashboard "Saved today" goes measured — Feature + UX · S
Swap the plan-based estimate tile (`ems/savings.py`, self-described "rough, illustrative") to the measured number from B-03a; label "measured" vs "estimated" explicitly; stop letting "€0.00" dominate (emotional-design review: never overpromise savings).
**Done when:** the tile derives from `/api/finance`; estimate label gone or truthful.
**Track:** Sprint 2 · E-02 · ⬜

### B-13 · Long-horizon energy rollups — Feature enabler · S–M
Monthly/weekly **kWh** aggregates so year-over-year trends survive the 365-day raw purge (the finance half shipped with B-03a: `daily_finance` is never purged). Without this, next summer's "vs last year" comparison silently breaks.
**Track:** Sprint 3 · E-02 · 🔄 finance half in [PR #3](https://github.com/jeroenniesen/EnergyManagementSystem/pull/3); kWh half ⬜

## EPIC E-03 · Family reach: the iOS app
*Goal: the scores and the calm story reach the family's phones, not just the LAN browser.* (Motivation)

### B-04a · iOS catch-up: rebase + API parity — Feature · S–M
The `ios-dashboard-chat` branch is several commits/PRs behind. Rebase onto current main; align with the current API surface (`/api/report` series, `/api/finance`, home_state); app builds and runs against production.
**Track:** Sprint 2 · E-03 · ⬜

### B-04b · iOS emotional-design parity — UX · M
Bring over the web UI's emotional layer: home-state headline, the three score cards (ring + motivating copy), energy-story visual language, calm tone. Read-only + chat; no control from the phone.
**Track:** Sprint 2 · E-03 · ⬜

### B-04c · Family rollout — Feature · S
TestFlight (or local install) on the family's phones; onboarding copy; confirm the daily-glance loop works (scores visible in <5 s from unlock).
**Done when:** the app is on family phones and gets opened without prompting.
**Track:** Sprint 3 · E-03 · ⬜

## EPIC E-04 · 2027-ready planner
*Goal: the planner captures every profitable window today and is repointed at the post-saldering world before 1 Jan 2027.* (€/Trust)

### B-30 · Valley-aware charge scheduling (winter planner) — Bug · S · P1
Found live 2026-07-02: `plan_rule_based` only shopped for charge slots before the *first* profitable peak; replanned mid-peak that window is empty → a profitable €0.14 valley before the next €0.30 peak was skipped. Fix: shop up to the *last* peak with strict pairwise profitability; per-slot deadlines point at the peak each charge feeds. Sizing-vs-interim-drain remains documented (mitigated by 5-min replans).
**Track:** Sprint 1 · E-04 · 🔄 [PR #2](https://github.com/jeroenniesen/EnergyManagementSystem/pull/2) (fix + regression test, replay-verified)

### B-05 · Post-2027 economics — Feature · S–M
Saldering ends hard 1 Jan 2027: self-consumption-first weighting, export-price awareness, never export at negative midday prices, native 15-min slots. Ships behind dry-run; dry-run acceptance takes calendar weeks — start well before the deadline. *(Roadmap F2)*
**Track:** Sprint 3 · E-04 · ⬜

### B-15 · Strategy auto-switch hysteresis + sunset deadline — Feature · S
Rolling solar threshold + hysteresis days (SPEC §8.4) instead of calendar-month switching; `astral`-based sunset deadlines. Fewer wrong-season plans in shoulder months.
**Track:** Pool · E-04 · ⬜

### B-16 · Missed-window recovery — Feature · M
SPEC §8.12 (`planner/recovery.py`): charge-completion checks and a catch-up path when a cheap window is missed (outage, stale data, dwell block).
**Track:** Pool · E-04 · ⬜

### B-22 · Projected-SoC gating in the plan validator — Feature · S–M
SPEC §8.5's "later step": use the SoC projection (already computed for display) to reject/adjust plans pre-apply.
**Track:** Pool · E-04 · ⬜

## EPIC E-05 · Quiet motivation
*Goal: progress you can watch — trends, recaps, and honest wins, without confetti.* (Motivation)

### B-06 · Score trends: "you vs last week/month" — UX · S
Verify/complete the trend spark-lines the Insights spec promised; add period-over-period comparison ("3 points better than last week") to score tiles and the Insights headline. Empty-history states degrade gracefully.
**Track:** Sprint 1 · E-05 · ⬜

### B-08 · Quiet success markers in Energy Story — UX · S
"Reserve respected", "evening peak covered from the battery", "no grid top-up needed" — shown only when true, from recorded data. *(Design review P2)*
**Track:** Sprint 3 · E-05 · ⬜

### B-07 · Weekly recap — Feature + UX · M
"Your week in energy": best day, the three scores with deltas, € saved, one concrete improvement suggestion. In-app (web + iOS), quiet tone. Defines which moments would ever deserve a push (B-20).
**Track:** Pool · E-05 · ⬜

## EPIC E-06 · Trust & guidance
*Goal: every warning answers "is the battery safe, what happens now, what can I do" — and setup feels commissioned, not toggled.* (Trust)

### B-31 · Don't render "no top-up" as both comfort and warning — UX · S
The story can show "✓ No grid top-up needed" beside "⚠ Short of the 88% target with no grid top-up planned" — the same fact as reassurance and problem. Suppress the marker when the on-track verdict is `behind` (`api.py` `_trust_markers`/`_on_track`).
**Track:** Sprint 2 · E-06 · ⬜

### B-09 · Emotionally complete failure states — UX · S
Every warning/error answers: is the battery safe, what will the EMS do now, what can I do. Shipped on the System page; missing on the alerts strip and API-error banners. *(Design review P0 remainder)*
**Track:** Pool · E-06 · ⬜

### B-12 · Setup vs Settings split — UX · M
Guided commissioning flow (connect devices → validate meters → solar array → battery read test → safe defaults → readiness checklist) separate from day-to-day Settings. Closes the SPEC M0b setup-wizard gap. *(Design review P1)*
**Track:** Pool · E-06 · ⬜

### B-21 · Dutch as a first-class language — UX · M
Safety-critical copy reads more trustworthy in the household's native language; start with failure states and override consequences. *(Design review)*
**Track:** Pool · E-06 · ⬜

---

## Pool — big levers (each with a trigger)

### B-17 · EV smart charging + battery-HOLD coordination — Feature · L
Solar-surplus + dynamic-price charging with a departure deadline; the hardened car-guard becomes part of a real EV strategy. ~€250–700/yr, worth more post-2027. **Trigger:** E-02/E-04 sprint work live; Tesla auth/BLE decision. *(Roadmap F4, `docs/v2-ev-control.md`)*
**Track:** Pool · 🟨 **advisory half shipped 2026-07-12** (`feat/ev-charging`): weekly min-SoC schedule, multi-day charge planner (brute-force-pinned), car DB + picker, SoC anchor + session detection, Web/iOS Car cards, export. Remaining = the control half (charger/car API), gated on `docs/v2-ev-control.md` being written.

### B-18 · HA client + MQTT publishing — Feature · M
WebSocket/REST HA read client + publish EMS state/scores as HA entities (SPEC'd, unbuilt). Enabler for B-19. **Trigger:** committing to heating control or a real HA-automation need.
**Track:** Pool · ⬜

### B-19 · Heating control on the gas boiler — Feature · L
OpenTherm-GW/Plugwise: weather-compensated low-temp curve, price/CO₂-aware setback, DHW eco (≥60 °C). Biggest absolute prize (~15–25% of gas); comfort-critical → fail-safe design first. **Trigger:** B-18 + B-11 recommendations acted on. *(Roadmap F6)*
**Track:** Pool · ⬜

### B-20 · Push notifications — Feature · S–M
ntfy/HA-companion pushes for genuine wins and warnings. **Trigger:** B-07 shows which moments deserve interruption.
**Track:** Pool · ⬜

### B-11 · Heating recommendations (advice-only) — Feature · S
"Zet 'm op 60", hydraulic balancing (waterzijdig inregelen, 10–15%), weather-appropriate setback tips. No control, no hardware — the cheapest lever on the biggest CO₂ prize. *(Roadmap F7)*
**Track:** Pool · ⬜

### B-14 · Solcast forecast provider — Feature · S
Real P10/P50/P90 percentiles (SPEC's primary provider, unbuilt) instead of derived 0.6×/1.15× bands. Improves risk-aware grid-charge sizing.
**Track:** Pool · ⬜

### B-23 · Deferred with roadmap triggers — Feature · L
ML layer (accelerator-gated); hybrid heat-pump switchover (**trigger:** heat pump installed); OCPP wallbox (**trigger:** wallbox purchased); ToU/peak-aware planning (**trigger:** NL ToU tariffs ~2029).
**Track:** Pool · ⬜

### Deliberately not doing
Badges, confetti, pressure-style streaks, per-person leaderboards, cute error states, celebration of battery cycling for its own sake — conflicts with the quiet-competence tone (decision 2026-07-02).

---

## Pool — refactoring

*Priority: P1 = schedule soon · P2 = when the code is touched anyway · P3 = opportunistic.*

### B-24 · Control-path & observability exceptions — Refactor · S · **P1**
(1) Inspect the silent `except Exception: pass` at `ems/control/mode_controller.py:208` — it sits in the control path. (2) Add log lines to the ~32 silent `except: pass` sites (concentrated in `web/api.py`): a failing explainer/cache/enrichment path is invisible today.
**Track:** Pool · ⬜

### B-25 · Split `web/api.py` — Refactor · M · P2
2,312+ lines, most inside one `create_app()` closure; domain logic in route handlers. Split into routers + a thin service layer, **incrementally as routes get touched**.
**Track:** Pool · ⬜

### B-26 · Reconcile SPEC with reality — Refactor/docs · S · P2
SPEC mandates HA integration (`entity_map`, WS/REST) and `ports.py`; the code reads devices directly and has no ports file — and works. Update SPEC §5/§9/§13 + CLAUDE.md deliberately; keep the HA client as B-18.
**Track:** Pool · ⬜

### B-27 · Dead & duplicated planner logic — Refactor · S · P3
Remove or wire `planner/optimal.py` (tested, never dispatched); dedupe `api._charge_kind` vs `energy_flow._allocate_slot`.
**Track:** Pool · ⬜

### B-28 · Frontend consolidation — Refactor · S–M · P3
Shared API client (a dozen components call `fetch` directly, repeating auth/error handling); split the 663-line `App.tsx`.
**Track:** Pool · ⬜

### B-29 · Test gaps — Refactor · S · P3
`main.py` wiring untested; `test_sources.py` is a 10-line stub; visual-regression baselines lighter than the SPEC implies.
**Track:** Pool · ⬜

---

## Shipped

### B-01 · Ship the Insights branch — ✅ 2026-07-02
`feat/insights-reporting` merged: scores, `/api/report`, Insights view, energy-story polish, sky backdrop. Tail: confirm deployment on the Mac Mini.
**Track:** ✅ [PR #1](https://github.com/jeroenniesen/EnergyManagementSystem/pull/1) (merged)
