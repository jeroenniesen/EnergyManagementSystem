# Caching analysis & plan (2026-06-28)

Goal: stop overusing rate-limited external APIs (Tibber, Forecast.Solar) and LLM tokens
(MiniMax explanations), **without** ever staling live meter data.

## Current state (as found)

| Source | Cache today | Survives restart? | Concurrency-safe? |
|---|---|---|---|
| Tibber prices | 15-min in-memory TTL + 60s retry backoff + last-good | ❌ no | ❌ no single-flight |
| Forecast.Solar | 30-min in-memory TTL + model fallback | ❌ no | ❌ no single-flight |
| AI explanation | in-memory Task cache keyed by `(reason, language)` | ❌ no | ✅ (shared Task) |
| AI chat | not cached (correct — each question unique) | n/a | n/a |
| AI validation | runs on an interval (`validate_hours`, default 24h) | n/a | n/a |
| Live meters (HomeWizard/Indevolt) | 30s poll-coalescing `_sample_cache` | n/a (must be live) | n/a |

Sources are built **once** at startup (`connection.build_wiring`) and shared, so the in-memory
caches do hold for the process lifetime. Settings POST rebuilds only the explainer, not the
price/forecast sources.

## Root causes

1. **Thundering herd → Tibber 429.** `price_source.slots()` / `solar_forecast.slots()` are called
   from **sync** FastAPI endpoints (`def prices()`, `_current_plan()` behind `/api/plan`,
   `/api/decision`, `/api/savings`, `/api/alerts`). Sync endpoints run in the threadpool, so a
   single dashboard refresh fans out to several threads. When the TTL expires, every concurrent
   caller misses at once and each fires its own upstream request — a burst that trips rate limits.
   The TTL cache has no single-flight lock.
2. **Restart re-fetch / re-spend.** All caches are in-memory. Every restart (deploy, Pi reboot, and
   especially rapid dev restarts) immediately re-fetches Tibber + Forecast.Solar and re-spends LLM
   tokens re-explaining decisions that haven't changed.
3. **AI cache thrash.** Some planner reasons embed live numbers (e.g. solar watts), so the
   `(reason, language)` key changes whenever those move, re-running the LLM for a near-identical
   explanation.

## Plan

- **Loop 1 — AI explanations (token win).** Add a small persistent, TTL'd `CacheStore` (SQLite,
  shared DB). Back the explanation cache with it, keyed by `sha256(model | language | reason)`, so
  an identical decision is explained **once** (within TTL) even across restarts. Only cache real
  LLM answers (never template/error fallbacks). Keep the in-memory Task cache for in-flight
  coalescing. Add `explainer.cache_hours` setting.
- **Loop 2 — Tibber + Forecast.Solar (API win).** Add a **single-flight lock** so concurrent
  cache-miss callers share one upstream request. Warm-start the in-memory cache from a persisted
  snapshot on construction so a restart within the validity window does no fetch at all.
- **Loop 3 — validate / test / fix.** Guardrail: live meters stay uncached by the external layer
  (always fresh). Concurrency + restart tests. Full gate.
- **Polish 1–2.** Edge cases (no-secrets-in-cache assertion, expiry purge + size bound), settings
  UI surfacing, docs, TTL tuning, live verification.

Hard rule preserved throughout: **meter/SoC data is never served from the long external caches.**
