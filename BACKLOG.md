# BACKLOG — HEMS Product Backlog

*Product-owner backlog, 2026-07-02; multi-level since 2026-07-03 (see
[`docs/superpowers/specs/2026-07-03-backlog-sync-design.md`](./docs/superpowers/specs/2026-07-03-backlog-sync-design.md)).
Owner: Jeroen. Groom by editing this file, then run `/backlog-sync` to mirror to GitHub.
**Status verified against `main` + merged PRs on 2026-07-20** — PRs through #42 merged (E-09 quality pass + auth slices 1–4); several items marked ✅ below.*

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
| **E-07 · Consumer-ready commercial product** | 🟨 B-55 settings menu | | | B-32 B-33 B-34 B-35 🟨 B-36 B-37 🟨 B-38 B-39 ✅ B-40 B-41 B-56 B-57 B-58 B-59 B-60 B-61 B-62 ✅ B-89 |
| **E-08 · Predictive optimization intelligence** | | | | B-63 B-64 B-65 B-66 B-67 B-68 B-69 B-70 B-71 B-72 B-73 B-74 B-75 B-76 B-77 B-78 |
| **E-09 · ISO 25010 quality engineering** | | | | **P1:** ✅ B-79 ✅ B-80 ✅ B-81 ✅ B-82 ✅ B-83 · ✅ B-84 B-85 |
| **E-10 · Web UI redesign: dense → calm** | | | | ✅ B-86 ✅ B-87 B-88 |
| *Big levers (pool)* | | | | B-17 B-18 B-19 B-20 B-23 |
| *Refactoring (pool)* | | | | B-24 B-25 B-26 B-27 B-28 B-29 |
| *Architecture & platform (pool)* | | | | **P1:** B-42 B-43 B-44 B-52 · B-45 🟨 B-46 B-47 B-48 B-49 B-50 B-51 B-53 B-54 |

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
**Track:** ✅ done — [PR #24](https://github.com/jeroenniesen/EnergyManagementSystem/pull/24). Story-card footer shows the measured finance figure ('€X.XX measured' / '€— · measuring'); the false €0.00 is gone.

### B-13 · Long-horizon energy rollups — Feature enabler · S–M
Monthly/weekly **kWh** aggregates so year-over-year trends survive the 365-day raw purge (the finance half shipped with B-03a: `daily_finance` is never purged). Without this, next summer's "vs last year" comparison silently breaks.
**Track:** ✅ done — finance half PR #3; kWh half [PR #27](https://github.com/jeroenniesen/EnergyManagementSystem/pull/27): daily_energy rollups (never purged) backfilled from history, year views read them.

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
**Track:** ✅ done — [PR #25](https://github.com/jeroenniesen/EnergyManagementSystem/pull/25). strategy.hysteresis_days (3) dampens auto season switches (day-keyed, restart-safe, forced modes bypass); sunset was already forecast-derived — better than almanac, noted in §8.4.

### B-16 · Missed-window recovery — Feature · M
SPEC §8.12 (`planner/recovery.py`): charge-completion checks and a catch-up path when a cheap window is missed (outage, stale data, dwell block).
**Track:** ✅ done — [PR #25](https://github.com/jeroenniesen/EnergyManagementSystem/pull/25). planner/recovery.py: completion check + cheapest-remaining catch-up through the unchanged validator + caps; WINTER-ONLY (summer catch-up would over-buy); honest partial + notification when impossible.

### B-22 · Projected-SoC gating in the plan validator — Feature · S–M
SPEC §8.5's "later step": use the SoC projection (already computed for display) to reject/adjust plans pre-apply.
**Track:** ✅ done — [PR #25](https://github.com/jeroenniesen/EnergyManagementSystem/pull/25). Validator check #6: grid-charge plans projecting >5pp short of their own target are rejected with the numbers named (complete-data only; reserve check #5 already existed). planner.validate_projection default ON.

## EPIC E-05 · Quiet motivation
*Goal: progress you can watch — trends, recaps, and honest wins, without confetti.* (Motivation)

### B-06 · Score trends: "you vs last week/month" — UX · S
Verify/complete the trend spark-lines the Insights spec promised; add period-over-period comparison ("3 points better than last week") to score tiles and the Insights headline. Empty-history states degrade gracefully.
**Track:** ✅ done — [PR #24](https://github.com/jeroenniesen/EnergyManagementSystem/pull/24). Trend chips vs the previous period on the Insights score cards; muted, never alarm-red; early-day aware.

### B-08 · Quiet success markers in Energy Story — UX · S
"Reserve respected", "evening peak covered from the battery", "no grid top-up needed" — shown only when true, from recorded data. *(Design review P2)*
**Track:** ✅ done — [PR #24](https://github.com/jeroenniesen/EnergyManagementSystem/pull/24). Two physics-provable markers (night-on-battery, cheap-window-only buying) — render only when verifiably true.

### B-07 · Weekly recap — Feature + UX · M
"Your week in energy": best day, the three scores with deltas, € saved, one concrete improvement suggestion. In-app (web + iOS), quiet tone. Defines which moments would ever deserve a push (B-20).
**Track:** ✅ superseded — B-58's weekly digest (Sunday read + notification) and B-06's trend chips together cover this item's intent; nothing left to build separately.

## EPIC E-06 · Trust & guidance
*Goal: every warning answers "is the battery safe, what happens now, what can I do" — and setup feels commissioned, not toggled.* (Trust)

### B-31 · Don't render "no top-up" as both comfort and warning — UX · S
The story can show "✓ No grid top-up needed" beside "⚠ Short of the 88% target with no grid top-up planned" — the same fact as reassurance and problem. Suppress the marker when the on-track verdict is `behind` (`api.py` `_trust_markers`/`_on_track`).
**Track:** ✅ done — [PR #24](https://github.com/jeroenniesen/EnergyManagementSystem/pull/24). Comfort chip yields to the 'behind' caution — one voice per fact.

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
**Track:** ✅ done — [PR #20](https://github.com/jeroenniesen/EnergyManagementSystem/pull/20). Hero verdict + synthesis + explicit 'Nothing needed from you.'; duplicate next-24h narrative killed (plan behind a persisted disclosure); score pills; stat-tile footer. 3.1→2.2 screens.

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
**Track:** ✅ done — [PR #20](https://github.com/jeroenniesen/EnergyManagementSystem/pull/20). Every alert carries safe ('is my home safe?') + action ('what can I do') copy, style-guarded by tests; info-level notices stay one calm line.

### B-38 · Data freshness and device health — Feature + UX · S–M
Show live connection status, last update time, forecast age, price-data status, and device health in consumer language. Surface stale inputs before they create confusing plans.
**Done when:** the user can tell whether EMS is acting on live data, cached data, demo data, or degraded data.
**Track:** Pool · E-07 · 🟨 foundations shipped (`ems/freshness.py` + `/api/freshness` per-signal badges; data-quality badge; iOS "last updated / couldn't refresh" stale banners); remaining = the consumer-framed device-health view + demo/degraded labelling.

### B-39 · Secure remote access path — Feature · L
Design and ship a secure cloud relay/proxy option so the iOS app works away from home without exposing the home network directly. Include auth, TLS, device pairing, rate limits, and privacy boundaries. Also owns the "warn when the app is remotely reachable with open read access" surface deferred here from B-83 — the LAN/VPN boundary is this item's concern, not identity auth's.
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
**Track:** ✅ done — [PR #20](https://github.com/jeroenniesen/EnergyManagementSystem/pull/20). Demo state shows a dismissible 'Use my real home →' nudge into Settings→Connection. Full onboarding remains B-34.

### B-58 · Weekly digest ("the Sunday read") — Feature · M *(roadmap P2; feeds B-36)*
A scheduled weekly summary in the advisor voice: what you saved, what the system did (incl. why-NOTs), one suggested tweak. In-app first; email/push delivery can ride B-20/B-39.
**Done when:** a household member can answer "did we do well this week?" from the digest alone in 10 seconds.
**Track:** ✅ done — [PR #21](https://github.com/jeroenniesen/EnergyManagementSystem/pull/21). 'Your week' panel + Sunday-18:00 delivery via the outbox; honest partial-week accounting.

### B-59 · iOS widgets + Live Activity — Feature · M *(roadmap P2)*
Home-screen widget (SoC + today's verdict sentence) and a Live Activity during a planned car-charge window (window, kWh, ≈€). Push for the rare must-know rides B-20.
**Done when:** the two most-wanted glances (battery state, car window) never require opening the app.
**Track:** ✅ done — [PR #21](https://github.com/jeroenniesen/EnergyManagementSystem/pull/21). WidgetKit small/medium via App Group; Live Activity deferred (needs APNs, documented).

### B-60 · One-tap advisor adoption — Feature · S *(roadmap P3)*
Advisor suggestions (solar confidence today; export model as 2027 nears) gain an "Apply" button: audit-logged, reversible, still never automatic.
**Done when:** adopting a suggestion is one tap + one confirm, and the audit log shows what changed and why.
**Track:** ✅ done — [PR #20](https://github.com/jeroenniesen/EnergyManagementSystem/pull/20). 'Apply Z%' on the solar-confidence advisor rides the normal dirty→save path (audit-logged, reversible); '✓ matches your setting' when aligned.

### B-61 · Self-calibration pass — Feature · M *(roadmap P3)*
Auto-calibrated load baseline from history, auto-season verification, car-session signatures refining the SoC anchor, and anomaly whispers ("solar underperformed similar days — panels dirty?"). All suggest-first per the constitution.
**Done when:** a year of normal operation needs ≤4 manual setting changes.
**Track:** Pool · E-07 · ⬜

### B-62 · "Year in Energy" review + milestone moments — Feature · M *(roadmap P4)*
A shareable annual review (savings, sunniest day, best arbitrage catch) plus milestone moments (first €100 saved, first full solar night). Earned delight — never gratuitous.
**Done when:** at least one screen someone shows to a friend unprompted.
**Track:** Pool · E-07 · ⬜

### B-89 · Username/password auth — slices 1–5 — Feature + Security · L *(roadmap P1)*
Turn the single shared web token into real accounts: username/password login (Argon2id), an admin/reader role model with last-admin guards, one-time invite codes, per-device access tokens (mint/list/revoke with atomic replace), iOS username/password login + a per-device widget access token, and a slice-4 hardening pass (per-username login rate-limit/lockout, strict CSP, auth audit wiring, export redaction of credential tables). **Slice 5** hardens the token model surfaced by the slice-4 security review: access tokens gain a per-token privilege tier (minted ≤ owner, default read-only) decided by one fail-closed `effective_rank` function, idle auto-revoke (unused past a configurable window), and `/api/users*`/`/api/invites*` become session-only so no machine token can manage accounts; the migrated shared token drops to OPERATE and the iOS widget token to read-only. Identity auth is always-on: every non-exempt `/api/*` request needs a bearer token, readers get a read-only UI, and the auth middleware is pure-ASGI so it never starves the control cycle.
**Done when:** a household can invite members, sign in per device, and revoke a lost device; readers cannot operate; no credential material reaches logs or exports.
**Track:** ✅ done — slices 1–4 across [PR #40](https://github.com/jeroenniesen/EnergyManagementSystem/pull/40) + the slices 2–4 batch PR; **slice 5** (token scoping / idle expiry / admin session-gating) in this PR. Backs B-83 (secure deployment posture) and the family-reach iOS work. *(Later: a per-device current-session marker in the account UI, session revocation on password change, and a friendlier iOS onboarding surface — first-run invite/login flow.)*

---

## EPIC E-08 · Predictive optimization intelligence
*Goal: EMS becomes a prediction + optimization system that plans under uncertainty, explains confidence, and proves value from historical replay.* (€/Trust/Motivation)

### B-63 · Probabilistic solar/load forecasting — Feature · M
Replace single-point forecasts with pessimistic/expected/optimistic bands for solar and home load, calibrated from historical forecast error.
**Done when:** planner inputs include confidence bands and the UI can say how likely the battery is to cover the evening peak.
**Track:** Pool · E-08 · 🟨 first code slice: `ems.intelligence.build_planning_scenarios()` turns P10/P50/P90 solar + load uncertainty into pessimistic/expected/optimistic planner inputs. Remaining = calibrate bands from historical error and expose the likelihood read in UI/API.

### B-64 · Household load prediction model — Feature · M
Learn normal household demand by time of day, weekday, season, weather, and recent behavior so the planner has a better baseline for evening, overnight, and morning load.
**Done when:** load forecast error is tracked and improves over the naive recent-average baseline.
**Track:** Pool · E-08 · 🟨 first code slice: `build_planning_scenarios()` consumes the existing learned `LoadProfile` and produces per-slot expected/high/low load maps for planning. Remaining = add weather/season features and prove improvement over the existing baseline.

### B-65 · Risk-aware battery planning — Feature · L
Optimize for expected cost while respecting reserve and comfort under uncertainty, for example targeting enough charge to cover the evening in 80-90% of plausible scenarios.
**Done when:** planner decisions can trade off cost vs confidence explicitly and expose the selected risk level.
**Track:** Pool · E-08 · 🟨 first code slice: `plan_risk_aware_adaptive()` selects a conservative/expected/optimistic scenario and delegates to the safe adaptive planner. Remaining = wire policy selection into live planning, validation evidence, and UI explanation.

### B-66 · Battery degradation-aware economics — Feature · M
Add battery wear cost to charge/discharge decisions so EMS avoids cycling when the price spread is too small to justify degradation.
**Done when:** every cycle has an estimated wear cost and the planner skips unprofitable cycling after wear is included.
**Track:** ✅ already shipped by F2/B-05 ([PR #14](https://github.com/jeroenniesen/EnergyManagementSystem/pull/14)): degradation_eur_per_kwh sits in the shared breakeven(); the planner skips unprofitable cycles; wear charged in measured savings. Nothing left to build.

### B-67 · Dynamic reserve recommendation — Feature · M
Recommend reserve levels based on forecast uncertainty, expected overnight demand, tomorrow's solar, current prices, and household comfort preference.
**Done when:** the app can explain why today's recommended reserve differs from the default.
**Track:** Pool · E-08 · ⬜

### B-78 · Automatic dynamic reserve adoption — Feature · M
Optionally apply B-67's daily reserve recommendation automatically after the recommendation-only
release has built enough evidence. This is a separate, explicit opt-in: retain a user-configured
hard reserve floor, bound day-to-day changes, explain every adjustment, audit-log it, and revert to
the last manual reserve whenever forecast/model quality is insufficient or stale.
**Done when:** an opted-in household can let EMS adjust reserve automatically without crossing its
hard comfort floor; every adjustment is explainable and reversible; replay plus a multi-week dry-run
shows no additional reserve breaches versus recommendation-only operation.
**Track:** Pool · E-08 · ⬜ follow-up to B-67; do not start before B-67 has production evidence.

### B-68 · Plan confidence score — UX + Feature · S–M
Show a confidence score for each plan based on data freshness, forecast uncertainty, device health, and planner validation quality.
**Done when:** every plan is labeled high/medium/low confidence with a plain-language reason.
**Track:** ✅ done — [PR #21](https://github.com/jeroenniesen/EnergyManagementSystem/pull/21). plan_confidence() worst-component-wins; hero chip with the deciding reason (shown only when not high).

### B-69 · Counterfactual savings engine — Feature · M–L
Compare actual EMS behavior against realistic alternatives: no battery, battery without EMS, solar-only self-use, and simple cheap-hour charging.
**Done when:** savings reports are measured against transparent baselines, not only against planned behavior.
**Track:** ✅ done — [PR #24](https://github.com/jeroenniesen/EnergyManagementSystem/pull/24). /api/counterfactual: planner vs no-battery vs vendor-auto over the recorded window (15-min cache). Naive-cheap-hours scenario honestly skipped — no engine knob for it; revisit only if a real question needs it.

### B-70 · Energy anomaly detection — Feature · M
Detect suspicious behavior such as battery not following commands, solar underperforming forecast, EV charging unexpected power, inconsistent meters, or inverter mode drift.
**Done when:** anomalies create calm, actionable alerts with supporting evidence.
**Track:** ⬜ Pool · E-08. Note: overlaps E-07's B-61 anomaly whispers — build once. Cluster-mismatch detection, incident rollup and the ingest plausibility clamp already exist as foundations.

### B-71 · Constrained EV charging optimizer — Feature · L
Optimize EV charging around required SoC, departure deadline, charger max power, battery reserve, solar forecast, dynamic prices, and household peak protection.
**Done when:** EMS can produce an EV charge plan that respects charger/home constraints and explains why each charge window was chosen.
**Track:** 🟨 mostly shipped by the EV planner ([PR #16](https://github.com/jeroenniesen/EnergyManagementSystem/pull/16)): SoC target, deadline, charger max, prices, solar, per-window explanations, brute-force-verified. Remaining: household peak protection + battery-reserve interaction — fold into the EV control half (B-17) when the charger arrives.

### B-72 · Forecast accuracy tracking — Feature · S–M
Track solar, load, price, and plan-execution error over time so EMS knows when a provider/model is biased or stale.
**Done when:** the system can report recent forecast error and use that error to plan more conservatively.
**Track:** ✅ done — [PR #21](https://github.com/jeroenniesen/EnergyManagementSystem/pull/21). Solar skill + plan-execution error + load-baseline error in the export + GET /api/accuracy.

### B-73 · Scenario simulator — Feature + UX · M
Let the homeowner explore "what if" questions such as changing reserve, charging the car tonight, cloudy weather, or negative prices.
**Done when:** simulations are read-only, clearly separate from the active plan, and explain cost/reserve impact.
**Track:** ✅ done — [PR #24](https://github.com/jeroenniesen/EnergyManagementSystem/pull/24). What-if panel: allowlisted A/B replay, read-only by construction, 'simulation — nothing is changed' badge.

### B-74 · Optimization explainability layer — Feature · M
Make optimization decisions produce structured reasons: selected window, alternative rejected, expected benefit, risk, and safety constraint.
**Done when:** planner explanations come from decision data and can be rendered consistently in web, iOS, logs, and diagnostics.
**Track:** Pool · E-08 · ⬜

### B-75 · Forecast-driven notifications — Feature · S–M
Notify only for predicted meaningful events: low solar tomorrow, EV needs plug-in before a cheap window, battery may miss evening peak, or unusual price opportunity.
**Done when:** notifications are sparse, actionable, and backed by forecast confidence.
**Track:** ✅ done — [PR #21](https://github.com/jeroenniesen/EnergyManagementSystem/pull/21). 4 detectors, evening-windowed, confidence-gated, per-day dedupe, own fail-safe loop.

### B-76 · Model and optimization health dashboard — Feature · S–M
Expose internal model health: solar error, load error, plan execution error, battery response error, data freshness, and optimization fallback rate.
**Done when:** support/debug views show whether EMS is predicting and executing well enough to trust.
**Track:** ✅ done — [PR #25](https://github.com/jeroenniesen/EnergyManagementSystem/pull/25). /api/accuracy health block (thresholds imported from confidence.py) + System-page Model-health panel with honest 'still collecting evidence' empty states + backups/clamped-samples ops rows.

### B-77 · Historical replay optimization suite — Feature + Test · M
Replay historical days through the planner to compare rule changes, validate reserve behavior, measure savings, and prevent seasonal regressions.
**Done when:** CI or a local command can replay representative days and report cost, reserve breaches, confidence, and plan-quality deltas.
**Track:** ✅ done — [PR #21](https://github.com/jeroenniesen/EnergyManagementSystem/pull/21). make replay: 3-scenario day replay (no-battery/AUTO/plan) + --set A/B; read-only by construction. The engine for B-73/B-69.

## EPIC E-09 · ISO 25010 quality engineering
*Goal: prove that EMS is functionally suitable, responsive, secure, accessible, reliable, maintainable, flexible, compatible, and safe in real-home conditions.* (Trust/€)

### B-79 · Truthful intelligence capability status — Bug · S · **P1**
The API now exposes the intelligence layer as `not_active` (the misleading hard-coded `shadow` label was corrected in [PR #38](https://github.com/jeroenniesen/EnergyManagementSystem/pull/38)); the scenario planner is still not evaluated by the live runtime. The remaining work is to replace the static label with a real, runtime-proven capability state (`not_active`, `shadow_evaluation`, `advisory`, `active`) and show the last evaluation time/result. Never imply that intelligence steers a plan before it actually does.
**Done when:** `/api/battery-plan` reports a runtime-proven state; UI copy distinguishes available, shadow, advisory, and active; tests prove no false claim is emitted.
**Track:** ✅ done — E-09 · label fix ([PR #38](https://github.com/jeroenniesen/EnergyManagementSystem/pull/38)) + the runtime-proven capability state in this PR. The status is now derived each request from a single evaluation-record seam (`app.state.intelligence_box`) via `_intelligence_status()`, exposed as an object on `/api/battery-plan` provenance + a new `GET /api/intelligence` (state / last-eval time / result / reason); it fails safe to `not_active` and can never claim a capability the runtime didn't record. Actually running shadow/advisory/active evaluation stays E-08 (this shipped only the honesty mechanism).

### B-80 · Control/API performance budgets — Ops + Test · M · **P1**
Define and measure control-cycle completion time, device-read latency, API p95 latency, SQLite transaction duration, memory ceiling, and replay/reporting budgets on the Raspberry Pi target. Add sustained dashboard-poll and slow-device tests; an over-budget cycle must preserve the safe fallback.
**Done when:** budgets are documented, measured in CI or a repeatable local command, and regressions fail with actionable output.
**Track:** ✅ done — [PR #41](https://github.com/jeroenniesen/EnergyManagementSystem/pull/41). Per-tier perf budgets (hot/interactive/batch) with a pure-ASGI `PerfTimingMiddleware`, a perf `Registry`, and a repeatable `python -m ems.tools.perf_check`; over-budget requests log `perf.over_budget` without being cancelled (measurement, not rate-limiting).

### B-81 · Fault-injection and recovery qualification — Test/Ops · M · **P1**
Exercise database loss/reopen, interrupted migrations, battery timeout, failed AUTO recovery, malformed price/forecast responses, process restart during leases, and competing control owners. Verify recovery, audit visibility, and no unsafe write under every failure.
**Done when:** a deterministic failure matrix runs in CI and each scenario has an explicit expected state, alert, and recovery outcome.
**Track:** ✅ done — [PR #41](https://github.com/jeroenniesen/EnergyManagementSystem/pull/41). A `fault_injection`-marked suite exercises database loss/reopen (the self-healing shared connection), battery write timeout → HOLD-not-revert, malformed price/forecast fallback, and process restart/lease recovery, each asserting a safe state + audit visibility. *Not every named scenario has a dedicated test yet (e.g. competing control owners, interrupted migration mid-backfill) — the matrix is real but not exhaustive.*

### B-82 · Accessibility quality gate — UX + Test · M · **P1**
Add automated accessibility checks and keyboard/screen-reader coverage for Dashboard, Manage, Car, Chat, alerts, charts, drawers/modals, and settings. Include focus restoration, reduced motion, contrast, labels, and mobile navigation.
**Done when:** axe (or equivalent) and keyboard smoke tests run in CI; critical WCAG failures block merge; charts have useful text alternatives.
**Track:** ✅ done — [PR #41](https://github.com/jeroenniesen/EnergyManagementSystem/pull/41)/[#42](https://github.com/jeroenniesen/EnergyManagementSystem/pull/42). A Playwright `a11y.spec.ts` WCAG 2.1 AA gate runs axe across the main views and blocks merge on critical failures; charts carry text-alternative `aria-label` summaries (e.g. the combined-plan chart's "Principal action windows:").

### B-83 · Secure deployment posture — Security/Ops · S–M · **P1**
Make authentication posture explicit at startup and in System diagnostics. Warn when the app is remotely reachable with open read access; document the LAN/VPN boundary; test token redaction, read/write authorization, token rotation, and failure responses. Consider requiring auth by default outside mock mode.
**Done when:** unsafe exposure is visible and actionable, auth behavior is contract-tested, and no token/secret appears in logs or exports.
**Track:** ✅ done — auth slices 1–4 ([PR #40](https://github.com/jeroenniesen/EnergyManagementSystem/pull/40) + this branch). Identity auth is always-on (every non-exempt `/api/*` needs a bearer token), plus per-username login rate-limiting/lockout, a strict Content-Security-Policy, auth audit wiring, and export redaction (`NEVER_EXPORT_TABLES` keeps credential tables out of the support ZIP, verified by a denylist-driven leak test). The residual "warn when the app is remotely reachable with open read access" idea is deferred to **B-39** (secure remote access path), where the LAN/VPN boundary lives.

### B-84 · Safety property and invariant tests — Test · M · P1/P2
Add property-based or exhaustive invariant tests for reserve-floor preservation, validator authority, recovery break-even limits, single-writer ownership, AUTO fallback, idempotent commands, and no-control-on-unsafe-data. Include representative DST and multi-peak days.
**Done when:** safety invariants run independently of example fixtures and failures identify the violated invariant.
**Track:** ✅ done — [PR #41](https://github.com/jeroenniesen/EnergyManagementSystem/pull/41). Invariant/property tests cover reserve-floor preservation, validator authority (`unsafe` ⇒ stay AUTO), single-writer ownership, AUTO fallback, idempotent commands, and no-control-on-unsafe-data, with DST and multi-peak fixtures; a failure names the violated invariant.

### B-85 · Stale-code and compatibility retirement — Refactor · S–M · P2
Create a retirement register for legacy `forecast_snapshots` reads, deprecated EV quick-advice settings, test-only simulation modules, stale EV documentation, temporary browser artifacts, and research-only intelligence code. Add usage evidence and removal versions before deleting compatibility paths.
**Done when:** every retained legacy path has an owner, reason, usage signal, and removal trigger; temporary artifacts are ignored or removed.
**Track:** Pool · E-09 · ⬜

---

## EPIC E-10 · Web UI redesign — dense → calm
*Goal: every screen answers "what's happening, and is it good?" before it shows a single number; each surface has a stated information budget, a clear hero → support → detail hierarchy, and detail on demand — never a wall of graphs and tiles competing for first attention.* (Trust/Motivation)

> **Builds on** E-07's design constitution ([`docs/2026-07-12-apple-of-ems-roadmap.md`](docs/2026-07-12-apple-of-ems-roadmap.md)) — principles 2 (*one glance, one truth*) and 3 (*progressive disclosure everywhere*) — and **completes what B-32 started** on the dashboard, applying the same discipline to *every* surface. Density here is a measurable budget, not taste. Constraints unchanged: ≤300 KB gz, WCAG 2.1 AA, light/dark (`GOAL.md` §2, SPEC §9.1). *Prompted 2026-07-17: the operator finds the current UI too dense — too much information per screen.*

### B-86 · Density audit + hierarchy budget — UX · S *(roadmap P2)*
The diagnostic and the ruler, before any pixels move. Inventory each surface (Dashboard, Next-24h/plan, Insights, Manage/Settings, Car, Chat): count the numbers, charts, badges, and cards visible per viewport; set a per-screen **information budget** (one hero verdict → supporting facts → detail-on-demand) and a shared type/spacing scale so "calm" is measurable and regressions are catchable — not a matter of taste.
**Done when:** each screen has a target information budget and a written hierarchy spec; there is a repeatable way to measure per-viewport density (extends the visual-regression harness).
**Track:** ✅ done — stable desktop/390 px actual-vs-baseline inventories now cover Dashboard/Next-24h, Insights, Manage/Settings, Car, and Chat, with the approved target stated in actionable failures. The audit records Car at four always-visible top-level sections against its calmer one-verdict target; bringing that and other remaining screens into budget belongs to open B-88.

### B-87 · Dashboard + Next-24h "one glance" redesign — UX · M *(extends B-32; roadmap P2)*
Apply the budget to the two heaviest surfaces. The **Next-24h/plan** screen today stacks a battery-level chart, a price chart, a battery-plan strip, and a solar chart under five stat tiles and two banners — four charts and five numbers before the user has asked a question. Collapse to one primary chart answering one question per viewport; demote the secondary charts and the stat grid behind a fold/tap. The dashboard keeps its hero verdict and sheds the cards still competing with it.
**Done when:** the plan view shows one primary question per viewport; a first-time user is never shown four charts and five tiles at once; the technical detail is one tap deeper, not gone.
**Track:** ✅ done — merged hero, four outcome tiles, one combined plan chart, and retained technical disclosure; verified in the 204-test frontend suite.

### B-88 · Insights / Manage / Car density pass + shared primitives — UX + Refactor · M *(pairs B-28/B-51; roadmap P2)*
Carry the same hierarchy across the remaining surfaces, and extract shared chart / stat-tile / card primitives so the density language stays consistent by construction and the bundle stays within budget.
**Done when:** every surface shares one density language and stays within its B-86 budget; chart/tile primitives are deduped (pairs with the B-28 frontend consolidation).
**Track:** Pool · E-10 · ⬜

## Pool — big levers (each with a trigger)

### B-17 · EV smart charging + battery-HOLD coordination — Feature · L
Solar-surplus + dynamic-price charging with a departure deadline; the hardened car-guard becomes part of a real EV strategy. ~€250–700/yr, worth more post-2027. **Trigger:** E-02/E-04 sprint work live; Tesla auth/BLE decision. *(Roadmap F4, `docs/v2-ev-control.md`)*
**Track:** Pool · 🟨 advisory half shipped (PR #16); **battery-HOLD coordination now has three modes** — [PR #31](https://github.com/jeroenniesen/EnergyManagementSystem/pull/31): hold (default) / static-W discharge / match-predicted-house-load, bounded writes (recommand rule + 10-min dwell + 6-command cap, write-count proven), reserve floor inviolable, unsafe-data suppresses discharge. Remaining = the charger/car CONTROL half, gated on the v2 spec + hardware.

### B-18 · HA client + MQTT publishing — Feature · M
WebSocket/REST HA read client + publish EMS state/scores as HA entities (SPEC'd, unbuilt). Enabler for B-19. **Trigger:** committing to heating control or a real HA-automation need.
**Track:** Pool · ⬜

### B-19 · Heating control on the gas boiler — Feature · L
OpenTherm-GW/Plugwise: weather-compensated low-temp curve, price/CO₂-aware setback, DHW eco (≥60 °C). Biggest absolute prize (~15–25% of gas); comfort-critical → fail-safe design first. **Trigger:** B-18 + B-11 recommendations acted on. *(Roadmap F6)*
**Track:** Pool · ⬜

### B-20 · Push notifications — Feature · S–M
ntfy/HA-companion pushes for genuine wins and warnings. **Trigger:** B-07 shows which moments deserve interruption.
**Track:** ✅ done — [PR #21](https://github.com/jeroenniesen/EnergyManagementSystem/pull/21). Outbox + bell + ntfy channel (phone pushes, zero cloud). APNs/native push still open if ever needed.

### B-11 · Heating recommendations (advice-only) — Feature · S
"Zet 'm op 60", hydraulic balancing (waterzijdig inregelen, 10–15%), weather-appropriate setback tips. No control, no hardware — the cheapest lever on the biggest CO₂ prize. *(Roadmap F7)*
**Track:** ✅ done — [PR #21](https://github.com/jeroenniesen/EnergyManagementSystem/pull/21). Three advice cards under the gas panel, annualized from the household's meter; DHW >=60°C safety line.

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
**Track:** ✅ done — [PR #24](https://github.com/jeroenniesen/EnergyManagementSystem/pull/24). 38 silent excepts now log (severity by blast radius); mode_controller's site was already fixed by B-42.

### B-25 · Split `web/api.py` — Refactor · M · P2
Now **3,079 lines** with 44 routes, most inside one `create_app()` closure; domain logic in route handlers. Split into `APIRouter`s per domain (status/plan/car/insights/finance/control/settings) + a thin service layer, **incrementally as routes get touched**. Do after **B-46** (extract the control service + app-context) so routers take an injected context instead of closures.
**Track:** 🟨 first slice done — [PR #24](https://github.com/jeroenniesen/EnergyManagementSystem/pull/24): minimal AppContext + car/digest/notify/export/accuracy routers, api.py 3618→3172. Remaining core (control/status/settings) rides B-46 as designed.

### B-26 · Reconcile SPEC with reality — Refactor/docs · S · P2
SPEC mandates HA integration (`entity_map`, WS/REST) and lists MQTT (`ems/publish/`) + a §14 visual/bundle/WCAG gate; the code reads devices directly, has no `ems/publish/`, and the visual gate is unimplemented — and works. Update SPEC §5/§9/§11/§13/§14 + CLAUDE.md deliberately (or build the gaps: HA client = B-18, visual/bundle gate ships with B-44). **Note:** the `ports.py`/`Planner` seam is now recommended to be **built** (B-47), not deleted from the SPEC.
**Track:** ✅ done — [PR #25](https://github.com/jeroenniesen/EnergyManagementSystem/pull/25). Major drift surfaced honestly: HA/MQTT/Solcast/Pi-Docker are planned-not-implemented; production = Mac Mini LaunchAgent, direct-device, armed control; §9.1 endpoints grep-verified (48 routes); §13 tree matched file-for-file; CLAUDE.md premise corrected to match.

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
**Track:** Pool · 🟨 — stages 1–2 done via [PR #37](https://github.com/jeroenniesen/EnergyManagementSystem/pull/37): a `ControlService` is extracted and testable outside FastAPI. Remaining: adopt the purpose-built `ControlLoop`, and unify the divergent readiness computations behind a single injected `AppContext`.

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
**Track:** ✅ done — [PR #27](https://github.com/jeroenniesen/EnergyManagementSystem/pull/27). Long-lived store connections, finance year view 3 round-trips instead of >=90 (query-count-tested), report CPU off the event loop, year series on rollups. Suite runtime halved as a side effect.

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
**Track:** ✅ done — backups PR #19 + migration runner [PR #27](https://github.com/jeroenniesen/EnergyManagementSystem/pull/27) (PRAGMA user_version, per-step transactions, loud failure; proven by the v1-v3 observation/rollup/ledger migrations incl. a live backfill).

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
