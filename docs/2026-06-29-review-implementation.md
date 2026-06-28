# Review implementation — 2026-06-29

Implementation of `docs/2026-06-28-energy-expert-review.md` and
`docs/2026-06-28-emotional-design-review.md`, over 10 iterations + 5 polish rounds (each ending with
a code review, tests and fixes). **The battery was never written — dry-run/observe-only throughout.**

## Energy-expert review — Top 10

| # | Recommendation | Status | Where |
|---|---|---|---|
| 1 | Target SoC/kWh/deadline on `PlanSlot`, wired to the driver | ✅ done | iter 2–3 |
| 2 | Remove P1-as-solar/car fallback; degrade missing meters | ✅ done | iter 5 |
| 3 | Block operational mode until a commissioning checklist passes | ◑ partial — layered readiness + `control_ready` gate (iter 6); a dedicated commissioning **UI/flow** is deferred | iter 6 |
| 4 | CHARGE never defaults to target SoC 100 | ✅ done | iter 3 |
| 5 | Persist switch counters, dwell, last action, vendor mode, override | ✅ done | iter 7 |
| 6 | Hard plan validator before any control write | ✅ done | iter 4 |
| 7 | e2e uses an isolated mock DB/settings | ✅ done | iter 1 |
| 8 | Demand-sized winter planning (not price-only) | ✅ done | polish 2 |
| 9 | Explicit Dutch tariff/export-avoidance economics | ⛔ deferred — larger spec item; current economics use spot price + round-trip + degradation + risk margin | — |
| 10 | Readiness/diagnostics distinguish dashboard- vs control-ready | ✅ done | iter 6 |

## Emotional-design review — Top 10

| # | Recommendation | Status | Where |
|---|---|---|---|
| 1 | Top-level home-state headline | ✅ done | iter 9 |
| 2 | Guided override flow (presets/consequence/confirm/expiry) | ✅ done | iter 8 |
| 3 | Split Setup from Settings | ⛔ deferred — settings are grouped/collapsible; a guided Setup flow is a separate effort | — |
| 4 | "Why this plan / why this strategy" | ✅ done | polish 1 (+ existing plan explanation) |
| 5 | Emotionally complete failure states | ✅ done | iter 10 recovery hints + polish 3 alerts |
| 6 | Quiet success markers in Energy Story | ✅ done | polish 5 |
| 7 | Operational readiness made explicit | ✅ done | iter 6 |
| 8 | Grounded non-AI help prompts | ✅ done | iter 10 (`/api/faq`) |
| 9 | Reframe metrics around outcomes | ◑ partial — home-state headline + energy-amounts-with-modes; a full metric regroup is deferred | iter 9 |
| 10 | Calm, precise, honest tone (no hype, no cute errors) | ✅ done | throughout |

## On Dutch safety tone (emotional review P2)

SPEC/CLAUDE.md fix the **UI to English-only for v1**, so UI strings stay English. Native-language
safety copy is delivered through the **AI layer**: the explainer/chat already support
`explainer.language = English | Dutch`, so a Dutch household can read the *why* of every decision and
the chat answers in Dutch without changing the v1 UI contract. The deterministic FAQ stays English
(v1). A first-class Dutch UI is a v2 item.

## Deferred (foundations now in place)

The plan contract (target/kwh/power/floor/deadline), the §8.11 validator, layered readiness and the
replay bundle were the prerequisites the reviews called out; they now exist, so these larger items
can follow without re-architecting: full **commissioning UI/flow**, the explicit **Dutch tariff +
export-avoidance** model, **forecast confidence/error learning**, an **EV session model**, richer
**load forecasting** (weekday/holiday/away), the **Setup-vs-Settings** split, and a full **metric
regroup** by mental model.
