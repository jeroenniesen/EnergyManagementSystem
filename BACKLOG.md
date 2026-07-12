# BACKLOG — HEMS Product Backlog

*Product-owner backlog, 2026-07-02; multi-level since 2026-07-03 (see
[`docs/superpowers/specs/2026-07-03-backlog-sync-design.md`](./docs/superpowers/specs/2026-07-03-backlog-sync-design.md)).
Owner: Jeroen. Groom by editing this file, then run `/backlog-sync` to mirror to GitHub.
**Status verified against `main` + merged PRs on 2026-07-12** — PRs #1–#17 all merged; several items marked ✅ below.*

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
| **E-01 · Honest CO₂ picture** | ✅ B-02 gas | | | ✅ B-10 |
| **E-02 · Measured money** | ✅ B-03a history | ⬜ B-03b tile | ⬜ B-13 rollups | |
| **E-03 · Family reach: iOS** | | ✅ B-04a ✅ B-04b | ⬜ B-04c | |
| **E-04 · 2027-ready planner** | ✅ B-30 valley fix | | ✅ B-05 economics | B-15 B-16 B-22 |
| **E-05 · Quiet motivation** | ⬜ B-06 trends | | ⬜ B-08 markers | B-07 |
| **E-06 · Trust & guidance** | | ⬜ B-31 marker | | B-09 B-12 B-21 |
| **E-07 · Consumer-ready commercial product** | 🟨 B-55 settings menu | | | B-32 B-33 B-34 B-35 🟨 B-36 B-37 🟨 B-38 B-39 ✅ B-40 B-41 B-56 B-57 B-58 B-59 B-60 B-61 B-62 |
| *Big levers (pool)* | | | | B-17 B-18 B-19 B-20 B-23 |
| *Refactoring (pool)* | | | | B-24 B-25 B-26 B-27 B-28 B-29 |
| *Architecture & platform (pool)* | | | | **P1:** B-42 B-43 B-44 B-52 · B-45 B-46 B-47 B-48 B-49 B-50 B-51 B-53 B-54 |

---

## EPIC E-01 · Honest CO₂ picture
*Goal: the CO₂ story covers the whole house — gas included — with a defensible grid factor.* (CO₂/Motivation)

### B-02 · Gas + CO₂ visibility — Feature · S
Ingest P1 `total_gas_m3` (the HomeWizard P1 already sends it; today it is thrown away), store it with history retention, and fold it into the CO₂ score — which already accepts `gas_m3` and is specified to "step down" honestly when gas first appears (insights design spec §②). Show gas use, € and kg CO₂ on the dashboard/Insights.
**Why now:** gas is ~2.5× electricity on both € and CO₂; every week of delay is history we can never backfill.
**Done when:** gas readings stored; CO₂ score includes the gas term with the step-down annotation; gas visible in Insights per period. *(Roadmap F1)*
**Track:** ✅ done — [PR #15](https://github.com/jeroenniesen/EnergyManagementSystem/pull/15) (merged 2026-07-12). Gas ingested via P1 (`sense.py`/`sources/live.py`), stored (`gas_readings`), gas CO₂ term wired (`reporting.py`), gas panel in Insights.

### B-10 · Carbon reporting via a live grid-intensity signal — Feature · S–M
Replace the fixed 0.27 kg/kWh factor with a live **average** NL grid-intensity signal for the CO₂ raw number + intensity curve. *(Roadmap F3)*
**Track:** ✅ done — [PR #15](https://github.com/jeroenniesen/EnergyManagementSystem/pull/15) (merged 2026-07-12), with a **provider substitution**: `CarbonSource` port + `StaticCarbonSource` (flat factor, default) + **`ElectricityMapsCarbonSource`** (optional live adapter, free key) with last-good→flat fail-safe. **NED.nl was deliberately not used** — its public API doesn't expose the needed intensity signal (documented in `sources/carbon.py`); ElectricityMaps delivers the same goal. *(Update the title's "via NED.nl" framing in SPEC/roadmap — B-26.)*

## EPIC E-02 · Measured money
*Goal: every € the app shows is measured from recorded history, never promised from a plan.* (€/Trust/Motivation)

### B-03a · Measured savings history — Feature · M
Per-day financial history measured from recorded samples + stored prices: grid cost, battery wear, saved vs the no-battery baseline; `price_slots` + `daily_finance` storage, `/api/finance`, Insights money section with honest coverage caveats.
**Track:** ✅ done — [PR #3](https://github.com/jeroenniesen/EnergyManagementSystem/pull/3) (merged 2026-07-03) + honest partial-coverage follow-up [PR #8](https://github.com/jeroenniesen/EnergyManagementSystem/pull/8) (merged 2026-07-05). `daily_finance` + `/api/finance` + Insights money section live.

### B-03b · Dashboard "Saved today" goes measured — Feature + UX · S
Swap the plan-based estimate tile (`ems/savings.py`, self-described "rough, illustrative") to the measured number from B-03a; label "measured" vs "estimated" explicitly; stop letting "€0.00" dominate (emotional-design review: never overpromise savings).
**Done when:** the tile derives from `/api/finance`; estimate label gone or truthful.
**Track:** Sprint 2 · E-02 · ⬜

### B-13 · Long-horizon energy rollups — Feature enabler · S–M
Monthly/weekly **kWh** aggregates so year-over-year trends survive the 365-day raw purge (the finance half shipped with B-03a: `daily_finance` is never purged). Without this, next summer's "vs last year" comparison silently breaks.
**Track:** Sprint 3 · E-02 · 🟨 finance half shipped ([PR #3](https://github.com/jeroenniesen/EnergyManagementSystem/pull/3), merged; `daily_finance` never purged); **kWh** monthly/weekly rollup half ⬜ (not started).

## EPIC E-03 · Family reach: the iOS app
*Goal: the scores and the calm story reach the family's phones, not just the LAN browser.* (Motivation)

### B-04a · iOS catch-up: rebase + API parity — Feature · S–M
The `ios-dashboard-chat` branch is several commits/PRs behind. Rebase onto current main; align with the current API surface (`/api/report` series, `/api/finance`, home_state); app builds and runs against production.
**Track:** ✅ done — [PR #6](https://github.com/jeroenniesen/EnergyManagementSystem/pull/6) (merged 2026-07-05) + parity kept current in #9/#16/#17. App builds (`swift test` + `xcodebuild` verified) against the live API surface incl. `/api/battery-plan`, `/api/car/plan`.

### B-04b · iOS emotional-design parity — UX · M
Bring over the web UI's emotional layer: home-state headline, the three score cards (ring + motivating copy), energy-story visual language, calm tone. Read-only + chat; no control from the phone.
**Track:** ✅ done — [PR #6](https://github.com/jeroenniesen/EnergyManagementSystem/pull/6) scores-first redesign + `InsightsView`/`ChatView`; battery-plan card ([#9](https://github.com/jeroenniesen/EnergyManagementSystem/pull/9)) + Car card ([#16](https://github.com/jeroenniesen/EnergyManagementSystem/pull/16)) since. *(Refactor debt tracked in B-50.)*

### B-04c · Family rollout — Feature · S
TestFlight (or local install) on the family's phones; onboarding copy; confirm the daily-glance loop works (scores visible in <5 s from unlock).
**Done when:** the app is on family phones and gets opened without prompting.
**Track:** Sprint 3 · E-03 · ⬜ — app is build/TestFlight-ready (a/b done); this is the human rollout step, not code.

## EPIC E-04 · 2027-ready planner
*Goal: the planner captures every profitable window today and is repointed at the post-saldering world before 1 Jan 2027.* (€/Trust)

### B-30 · Valley-aware charge scheduling (winter planner) — Bug · S · P1
Found live 2026-07-02: `plan_rule_based` only shopped for charge slots before the *first* profitable peak; replanned mid-peak that window is empty → a profitable €0.14 valley before the next €0.30 peak was skipped. Fix: shop up to the *last* peak with strict pairwise profitability; per-slot deadlines point at the peak each charge feeds. Sizing-vs-interim-drain remains documented (mitigated by 5-min replans).
**Track:** ✅ done — [PR #2](https://github.com/jeroenniesen/EnergyManagementSystem/pull/2) (merged 2026-07-01; fix + regression test, replay-verified)

### B-05 · Post-2027 economics — Feature · S–M
Saldering ends hard 1 Jan 2027: self-consumption-first weighting, export-price awareness, never export at negative midday prices, native 15-min slots. Ships behind dry-run; dry-run acceptance takes calendar weeks — start well before the deadline. *(Roadmap F2)*
**Track:** ✅ done — [PR #14](https://github.com/jeroenniesen/EnergyManagementSystem/pull/14) (merged 2026-07-12): post-2027 export valuation + opt-in negative-price soak (`planner/economics.py`, `finance.py`). *Confirm the multi-day dry-run acceptance period before arming for real.*

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

## EPIC E-07 · Consumer-ready commercial product
*Goal: a normal homeowner can install the product, trust the automation, understand the value, and get support without needing to think like an energy engineer.* (€/Trust/Motivation)

> **Design constitution:** [`docs/2026-07-12-apple-of-ems-roadmap.md`](docs/2026-07-12-apple-of-ems-roadmap.md) — 8 principles + phase bars (P0 calm controls → P1 first five minutes → P2 trust at a glance → P3 self-configuring → P4 delight → P5 ecosystem). Every E-07 item should name the phase bar it serves. Audit companion: [`docs/2026-07-12-settings-ux-audit.md`](docs/2026-07-12-settings-ux-audit.md).

### B-32 · Consumer dashboard hierarchy — UX · S–M
Make the main dashboard answer the four consumer questions in order: what is happening now, is it good or bad, what happens next, and do I need to act. Keep the technical proof available, but stop making every graph compete for first attention.
**Done when:** a first-time user can explain the current battery/solar/grid state after a 10-second glance; detailed graphs are one tap deeper.
**Track:** Pool · E-07 · ⬜

### B-33 · "Why is EMS doing this?" explanations — UX + Feature · M
Add plain-language explanations to every important automated decision: grid charge, battery hold, discharge, reserve protection, EV charge planning, and paused states. Use decision facts from the planner/API, not static copy.
**Done when:** every main action has a "why" explanation that includes reason, expected benefit, and safety impact.
**Track:** Pool · E-07 · ⬜

### B-34 · Guided homeowner onboarding — Feature + UX · M–L
Create a setup flow separate from Settings: connect devices, validate live meters, select tariff, configure battery reserve, configure solar forecast, optional EV charger, and finish with a readiness checklist.
**Done when:** a new install reaches a clear "ready to automate" state with missing integrations called out as optional or blocking.
**Track:** Pool · E-07 · ⬜

### B-35 · Automation trust controls — Feature · M
Expose clear limits for what EMS may do: minimum battery reserve, grid-charge rules, EV charging power, approval requirements for unusual actions, and safe fallback behavior.
**Done when:** a homeowner can see and change the safety limits without reading config files, and every limit is reflected in planner validation.
**Track:** Pool · E-07 · ⬜

### B-36 · Measured value proof — Feature · M
Show weekly/monthly proof of value: measured savings, avoided peak-price grid use, solar self-use, battery contribution, and honest coverage gaps when data is incomplete.
**Done when:** the app can answer "what did EMS save me?" with measured numbers and a transparent baseline.
**Track:** Pool · E-07 · 🟨 foundations shipped (B-03a measured `daily_finance` + Insights money section + honest coverage caveats); remaining = the consumer-framed weekly/monthly "value proof" surface (overlaps B-07).

### B-37 · Calm actionable warnings — UX · S–M
Make every warning answer: what happened, is my home/battery safe, what will EMS do now, and what can I do. Remove warnings that describe a condition without a next step.
**Done when:** alert banners, paused states, connection errors, and stale-data states all include safety + next-action language.
**Track:** Pool · E-07 · ⬜

### B-38 · Data freshness and device health — Feature + UX · S–M
Show live connection status, last update time, forecast age, price-data status, and device health in consumer language. Surface stale inputs before they create confusing plans.
**Done when:** the user can tell whether EMS is acting on live data, cached data, demo data, or degraded data.
**Track:** Pool · E-07 · 🟨 foundations shipped (`ems/freshness.py` + `/api/freshness` per-signal badges; data-quality badge; iOS "last updated / couldn't refresh" stale banners); remaining = the consumer-framed device-health view + demo/degraded labelling.

### B-39 · Secure remote access path — Feature · L
Design and ship a secure cloud relay/proxy option so the iOS app works away from home without exposing the home network directly. Include auth, TLS, device pairing, rate limits, and privacy boundaries.
**Done when:** the phone can reach the home EMS from outside the LAN through a documented, secure path with clear failure states.
**Track:** Pool · E-07 · ⬜

### B-40 · Support diagnostics export — Feature · S–M
Add a diagnostics package for support: configuration summary, device status, latest plan, recent planner decisions, API health, data freshness, and redacted logs.
**Done when:** a homeowner can export/share a support bundle without exposing secrets.
**Track:** ✅ done — [PR #12](https://github.com/jeroenniesen/EnergyManagementSystem/pull/12)/[#13](https://github.com/jeroenniesen/EnergyManagementSystem/pull/13) (merged 2026-07-11). `ems/export_package.py` + `GET /api/export`: one ZIP of samples/prices/forecasts/`daily_finance`/`audit_log`/`plan_history`/gas/EV sessions + a privacy-safe `manifest.json` (run mode, planner settings, data quality, recorder health, incident rollup); secrets never included (config via `public_values`). *Minor gap: redacted app-log file isn't bundled — add if support needs it.*

### B-41 · Compatibility and commercial positioning — Product/docs · S
Create a public-facing compatibility and positioning page: supported batteries, inverters, smart meters, tariffs, EV chargers, solar forecast providers, and the primary product promise.
**Done when:** a prospective customer can tell whether EMS works for their home and why they would buy it.
**Track:** Pool · E-07 · ⬜

### B-55 · Settings menu system (two-pane) — UX · M *(roadmap P0)*
The audit-driven redesign: intent-grouped sidebar (Your setup / How it runs / App) with search, one section at a time, first-sentence help + More, per-section Advanced fold, sticky save bar, mobile drill-in.
**Done when:** any setting reachable in ≤2 interactions; a section ≤1.5 screens; save always visible when dirty.
**Track:** ✅ done — [PR #18](https://github.com/jeroenniesen/EnergyManagementSystem/pull/18) (merged 2026-07-12). Two-pane menu: intent sidebar + search, per-section Advanced, sticky save, mobile drill-in.

### B-56 · LAN auto-discovery of meters in onboarding — Feature · M *(roadmap P1; extends B-34)*
Discover HomeWizard devices via mDNS and *offer* them during setup (suggest, never silently apply — the advisor voice); manual IP entry stays as fallback.
**Done when:** on a normal LAN, unboxing → live dashboard needs zero typed IP addresses; B-34's flow consumes it.
**Track:** Pool · E-07 · ⬜

### B-57 · Demo mode as the empty state — UX · S *(roadmap P1)*
An unconfigured app opens straight into the demo home with one persistent "use my real home" action into onboarding — never an empty chart, never a dead dashboard.
**Done when:** first launch is indistinguishable from a working product; day-2 return after setup shows a personalised insight, not a blank.
**Track:** Pool · E-07 · ⬜

### B-58 · Weekly digest ("the Sunday read") — Feature · M *(roadmap P2; feeds B-36)*
A scheduled weekly summary in the advisor voice: what you saved, what the system did (incl. why-NOTs), one suggested tweak. In-app first; email/push delivery can ride B-20/B-39.
**Done when:** a household member can answer "did we do well this week?" from the digest alone in 10 seconds.
**Track:** Pool · E-07 · ⬜

### B-59 · iOS widgets + Live Activity — Feature · M *(roadmap P2)*
Home-screen widget (SoC + today's verdict sentence) and a Live Activity during a planned car-charge window (window, kWh, ≈€). Push for the rare must-know rides B-20.
**Done when:** the two most-wanted glances (battery state, car window) never require opening the app.
**Track:** Pool · E-07 · ⬜

### B-60 · One-tap advisor adoption — Feature · S *(roadmap P3)*
Advisor suggestions (solar confidence today; export model as 2027 nears) gain an "Apply" button: audit-logged, reversible, still never automatic.
**Done when:** adopting a suggestion is one tap + one confirm, and the audit log shows what changed and why.
**Track:** Pool · E-07 · ⬜

### B-61 · Self-calibration pass — Feature · M *(roadmap P3)*
Auto-calibrated load baseline from history, auto-season verification, car-session signatures refining the SoC anchor, and anomaly whispers ("solar underperformed similar days — panels dirty?"). All suggest-first per the constitution.
**Done when:** a year of normal operation needs ≤4 manual setting changes.
**Track:** Pool · E-07 · ⬜

### B-62 · "Year in Energy" review + milestone moments — Feature · M *(roadmap P4)*
A shareable annual review (savings, sunniest day, best arbitrage catch) plus milestone moments (first €100 saved, first full solar night). Earned delight — never gratuitous.
**Done when:** at least one screen someone shows to a friend unprompted.
**Track:** Pool · E-07 · ⬜

---

## Pool — big levers (each with a trigger)

### B-17 · EV smart charging + battery-HOLD coordination — Feature · L
Solar-surplus + dynamic-price charging with a departure deadline; the hardened car-guard becomes part of a real EV strategy. ~€250–700/yr, worth more post-2027. **Trigger:** E-02/E-04 sprint work live; Tesla auth/BLE decision. *(Roadmap F4, `docs/v2-ev-control.md`)*
**Track:** Pool · 🟨 **advisory + usability half shipped & merged 2026-07-12** — [PR #16](https://github.com/jeroenniesen/EnergyManagementSystem/pull/16) (weekly min-SoC schedule, multi-day look-ahead charge planner, car DB + picker, SoC anchor + session detection, Web/iOS Car cards, export) + [PR #17](https://github.com/jeroenniesen/EnergyManagementSystem/pull/17) (reserve-safety hold + manual-only-meter warnings; validated). Remaining = the **control half** (charger/car API), gated on `docs/v2-ev-control.md` being written.

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
Now **3,079 lines** with 44 routes, most inside one `create_app()` closure; domain logic in route handlers. Split into `APIRouter`s per domain (status/plan/car/insights/finance/control/settings) + a thin service layer, **incrementally as routes get touched**. Do after **B-46** (extract the control service + app-context) so routers take an injected context instead of closures.
**Track:** Pool · ⬜

### B-26 · Reconcile SPEC with reality — Refactor/docs · S · P2
SPEC mandates HA integration (`entity_map`, WS/REST) and lists MQTT (`ems/publish/`) + a §14 visual/bundle/WCAG gate; the code reads devices directly, has no `ems/publish/`, and the visual gate is unimplemented — and works. Update SPEC §5/§9/§11/§13/§14 + CLAUDE.md deliberately (or build the gaps: HA client = B-18, visual/bundle gate ships with B-44). **Note:** the `ports.py`/`Planner` seam is now recommended to be **built** (B-47), not deleted from the SPEC.
**Track:** Pool · ⬜

### B-27 · Dead & duplicated planner logic — Refactor · S · P3
Remove or wire `planner/optimal.py` (tested, never dispatched); dedupe `api._charge_kind` vs `energy_flow._allocate_slot`.
**Track:** Pool · ⬜

### B-28 · Frontend consolidation — Refactor · S–M · P3
Shared data layer — a `useResource(path,{pollMs})` hook + `apiGet`/`apiWrite` — to replace the **26 `fetch(` sites across 13 components** that repeat auth headers, the `alive`-guard, error/loading state, and the 401/422 write handling (copy-pasted verbatim 3×). Split the large components along the fetch/derive/present seam (`Settings.tsx` 796, `App.tsx` 690, `EnergyStory.tsx` 549, `CarCard.tsx` 466). Pairs with **B-45** (typed contract) and **B-51** (shared chart primitives).
**Track:** Pool · ⬜

### B-29 · Test gaps — Refactor · S · P3
`main.py` wiring untested; `test_sources.py` is a 10-line stub; visual-regression baselines lighter than the SPEC implies.
**Track:** Pool · ⬜

---

## Pool — architecture & platform hardening

*From the whole-app architecture review (2026-07-12). Same priority scheme as refactoring:
P1 = schedule soon · P2 = when the area is touched · P3 = opportunistic. The safety core
(single battery writer, fail-safe-to-AUTO, source-port discipline, coalesced device reads,
hermetic tests) reviewed as sound — these items scale the patterns that work and finish the
seams that were started, without touching the parts that are right.*

### B-42 · Control-path stability fixes — Bug · S · **P1**
Three small, independently-shippable correctness bugs found in the review: (1) `ModeController.tz`
is never wired to `site_tz` (constructed without `tz=` in `main.py`), so the **daily switch-cap
resets at 00:00 UTC, not local midnight** — the documented contract. (2) `settings_cache.clear()`
then `.update()` (`web/api.py`, lifespan + POST `/api/settings`) exposes a momentarily-empty dict to
threadpool GET handlers → possible `KeyError`/500; drop the `clear()` (the overlay always returns the
full keyset). (3) the override endpoint's `asyncio.create_task(_run_control_cycle())` is unreferenced
→ GC-able mid-run ("charge now" silently no-ops) and its crashes surface nowhere; keep a ref +
`_task_died` callback like the lifespan tasks. Also: `astimezone()` → `astimezone(site_tz)` for
override time strings; log the silent `except` in `mode_controller._persist`.
**Track:** ✅ done — [PR #19](https://github.com/jeroenniesen/EnergyManagementSystem/pull/19). All three bugs fixed + tz-local override strings + logged _persist; each pinned by a fail-first test.

### B-43 · Reproducible production image — Bug/Ops · S · **P1**
`Dockerfile` hand-installs Python deps with `>=` ranges and **omits `httpx`**, so the live/Pi image
`ImportError`s on the first real Tibber/HomeWizard/Indevolt read (works only in mock mode); it also
ignores `uv.lock`, and uses `npm install` where everything else uses `npm ci`. Switch to
`COPY pyproject.toml uv.lock` + `uv sync --frozen --no-dev` and `npm ci` for a reproducible, tested image.
**Track:** ✅ done — [PR #19](https://github.com/jeroenniesen/EnergyManagementSystem/pull/19). Lockfile-driven image (uv sync --frozen --no-dev, pinned builder, npm ci) + .dockerignore (context 800MB→4.4MB, fixes host node_modules clobbering); build+run verified.

### B-44 · Continuous integration — Ops · M · **P1**
No `.github/workflows/` exists; ~11k LOC of pytest + `ruff` + `swift test` + Playwright e2e + the
SPEC §14 bundle-size/visual gate run only when a human remembers `make`. Add GitHub Actions:
`backend` (uv + ruff + pytest) and `frontend` (npm ci + build + **bundle-size gate** + Playwright) on
ubuntu; `ios` (`swift test`) on macos-latest, path-filtered. Never use the Pi/Jetson as runners
(SPEC §11). Highest-leverage stability fix for a "never worse than no EMS" system.
**Track:** ✅ done — [PR #19](https://github.com/jeroenniesen/EnergyManagementSystem/pull/19). ci.yml (backend ruff+pytest · frontend build + 300KB-gz bundle gate + hermetic Playwright) + path-filtered ios.yml (swift test). First live run = PR #19's own checks.

### B-45 · Typed API contract + generated clients — Refactor · M · P2
The backend returns hand-built `dict`s (zero FastAPI `response_model`); the web app and the iOS app
each **re-transcribe every response shape by hand** (Score/Report/Flows are duplicated even within
the frontend) → a backend field rename type-checks green until it breaks at runtime on two clients.
Add Pydantic response models on the hot endpoints → generate TS (`openapi-typescript`) + Swift types
from the OpenAPI schema. One source of truth across backend/web/iOS. Supersedes the type half of B-28.
**Track:** Pool · ⬜

### B-46 · Extract control service + app-context — Refactor · L · P2
The EMS "brain" (SPEC §13 `cycle()`) lives as nested closures inside `create_app` — `_effective_intent`,
`_control_tick`, `_current_plan`, `_run_control_cycle` + ~10 mutable `*_box` state dicts — while the
purpose-built `ems/control/loop.py::ControlLoop` sits **unused**. `create_app` takes 19 keyword args
because there's no object to hang collaborators on. Extract an `EmsRuntime`/`ControlService` + a single
injected `AppContext`, adopt `ControlLoop`, and unify the two divergent "readiness" computations
(`lifecycle` vs `readiness.py`). Makes the brain testable outside FastAPI and unblocks B-25.
**Track:** Pool · ⬜

### B-47 · Planner port + registry — Refactor/Feature · M · P2
There is no `Planner` Protocol (strategy selection is an `if/elif` in `planner/strategy.py`), `PlannerMode`
(`domain.py`) is defined but unused, and `PlannerInputSnapshot` — which SPEC §13.2/§8.11 require saved with
every plan for audit/replay — **does not exist**. Define `Planner` (mirroring the clean source-side
Protocols), a registry keyed by strategy/`PlannerMode`, add the input snapshot, and a discoverable
`ports.py` catalog. This is the seam the optional ML planner and `advisory` mode plug into behind the
unchanged §8.11 validator. Reframes B-26: **build** the port rather than delete it from the SPEC.
**Track:** Pool · ⬜

### B-48 · Per-cycle compute memoization — Refactor/Perf · M · P2
Nothing memoizes the plan/projection pipeline: `build_plan` runs ~6× and `_forward_projection` twice
**per dashboard poll, per client** (each re-reads ~2016 history rows + rebuilds the load profile), even
though the cheap device reads it depends on are already coalesced. On a Pi with 2–3 clients + iOS that's
12–24 identical plan builds every 10 s. Cache `(plan, validation, projection, forward-bundle,
recent-actuals, load-profile)` per quantized `now` (the pattern `_current_sample`/`_current_towers`
already use) → one build per cycle shared across all endpoints and clients.
**Track:** Pool · ⬜

### B-49 · Reporting query performance — Refactor/Perf · S–M · P2
`/api/finance` is an N+1 — a fresh `aiosqlite` connection (own thread) **per local day**, up to 365 for a
year view; every store connects-per-call; and `build_report`/`build_series`/`build_daily_flows` iterate up
to ~200k rows of CPU **on the event loop** (unlike `_forward_projection`, which uses `to_thread`). Bulk-read
finance days once, hold a long-lived connection per store, wrap the report assembly in `to_thread`, and
pre-aggregate/down-sample the year view (dovetails with B-13 rollups).
**Track:** Pool · ⬜

### B-50 · iOS architecture cleanup — Refactor · M–L · P2/P3
`DashboardView.swift` is 2241 lines (~40 nested view structs) and `Models.swift` is 1642 (contract models
+ demo fixtures + primitives in one file). Split both by feature (demo fixtures out of the production model
file); unify the three divergent store conventions into one `ServerIdentity` + a `RemoteStore` protocol
(consistent `errorMessage`/`isLoading`/`isStale`/`lastUpdatedAt`, one server-switch clear path); route all
charts through `SeriesGeometry` + a `BatteryAction` enum owning color/label once (three copied string→color
maps today); extract shared card chrome/`MessagePanel`/`ScoreRing`/`ValueTile`; dedup the API POST/auth
boilerplate; finish the `ISOTimestamp` adoption (`FlexibleSection` still rolls its own).
**Track:** Pool · ⬜

### B-51 · Shared frontend chart primitives — Refactor · M · P3
Six components hand-roll inline SVG and re-derive the same scale + "break the line at gaps" logic (with
code comments admitting the copy). Add a dependency-free `src/charts/` module (`linearScale`, `niceMax`,
`segments` gap-splitting, `<Gridlines>`/`<Crosshair>`/`<ChartTooltip>`/`<Legend>`) and route the charts
through it. Also `React.memo` the pure charts + split the 10 s poll into 10 s/60 s cadences to cut idle
re-render churn. Protect the react-only bundle — no chart library. Complements B-28.
**Track:** Pool · ⬜

### B-52 · Data durability: backups + migrations — Ops · M · P1/P2
The SPEC §11-required scheduled SQLite backup is **unimplemented** — on an unattended Pi an SD/NVMe failure
wipes all (never-purged) financial history. And the schema is entirely `CREATE TABLE IF NOT EXISTS` with no
`PRAGMA user_version`/migration path (the finance `calc_v` recompute is a good per-cache pattern but not a
general one). Add a scheduled `VACUUM INTO`/`.backup` to `_maintenance_loop` and a small ordered
migration runner at startup before the schema needs a real `ALTER`.
**Track:** 🟨 backup half done — [PR #19](https://github.com/jeroenniesen/EnergyManagementSystem/pull/19): daily online VACUUM INTO snapshots, history.backup_keep rotation (7), diagnostics storage.backup, runbook restore. Remaining: the migration runner (PRAGMA user_version) — pick up before the next schema ALTER.

### B-53 · Deploy consistency (Pi/Jetson) — Ops · M · P2
Only macOS is scripted (host `uv` + LaunchAgent); the Pi/Jetson are Docker Compose targets but there is
**no committed production `docker-compose.yml`** — the §11.1 healthcheck/`mem_limit`/`stop_grace` compose
lives only as a fenced sketch in SPEC.md, and `docker-compose.dev.yml` has no healthcheck. Commit the prod
compose + a Pi/Jetson install path (or systemd unit) and reconcile the data-dir mount. Also wire the
`health.ntp_check` (price/charge windows are time-critical).
**Track:** Pool · ⬜

### B-54 · Repo & test hygiene — Refactor · S · P3
2074 iOS Xcode build-artifact files are **tracked in git** (`.gitignore` lacks `ios/**/build`,
`ios/**/.build`, `xcuserdata`) — repo bloat + merge noise. Wall-clock timing tests
(`test_control_loop.py`, several `time.sleep` tests) will flake first on shared CI runners — use the loop's
injectable `clock=` for deterministic ticks. Package the flat 30-module `ems/` into cohesive packages
(`ems/ev/` — 5 already-decoupled modules; `ems/insights/` — reporting/analysis/scores/finance).
**Track:** Pool · ⬜

---

## Shipped

### B-01 · Ship the Insights branch — ✅ 2026-07-02
`feat/insights-reporting` merged: scores, `/api/report`, Insights view, energy-story polish, sky backdrop. Tail: confirm deployment on the Mac Mini.
**Track:** ✅ [PR #1](https://github.com/jeroenniesen/EnergyManagementSystem/pull/1) (merged)
