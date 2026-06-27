# Failure-mode table

> Companion to `../SPEC.md` §8.8, §9.3, §16. Every row defines: how it's detected, the safe behaviour, the alert raised, and recovery. The guiding rule: **the system must never be worse than "no EMS"** — when in doubt, fall back to the battery's own `AUTO` self-consumption.

| Failure | Detection | Safe behaviour | Alert | Recovery |
|---|---|---|---|---|
| **Tomorrow's prices missing** | no `tomorrow` array by `tomorrow_required_by` (default 15:00) | keep prior plan if still valid; else `AUTO` | `prices_stale` | retry with jitter 13:00–14:00; EnergyZero/ENTSO-E cross-check |
| **Partial price array** | slot count ≠ expected for date (DST-aware: 96/92/100 qh = normal/spring-forward/fall-back) | do **not** plan on it; keep prior/`AUTO` | `prices_stale` | re-fetch forward window |
| **Tibber unreachable** | HTTP error / timeout | use **cached** immutable past + last good forward; else `AUTO` | `prices_stale` | backoff; fall to EnergyZero |
| **Forecast stale (Solcast)** | issue-time older than threshold | switch to **Forecast.Solar** fallback; flag provenance | `forecast_stale` | resume Solcast next scheduled call |
| **Solcast budget exhausted** | ledger at daily cap | stop calling Solcast today; use last good + Forecast.Solar | `solcast_budget_exhausted` | resets at local midnight |
| **Both forecasts fail** | no usable solar forecast | summer: assume P10≈0 (conservative); winter unaffected | `forecast_stale` | retry next cycle |
| **HA down** | WS/REST unreachable | **actively command `AUTO` before losing control** if reachable; once unreachable the battery's own watchdog must hold a safe mode (CONFIRM@M1 the vendor reverts a *forced* mode on loss of comms — never assume a forced CHARGE/DISCHARGE simply persists safely); UI serves stale from SQLite | `fallback_active` | reconnect; re-validate entities |
| **Mapped entity missing/renamed** | startup validation fails | start **degraded/`AUTO`**; refuse to mis-read | `meter_missing` | fix `entity_map`; re-probe |
| **Battery unreachable** | command times out / state unread | retry once w/ backoff → command `AUTO` → if still failing, leave safe | `battery_write_failed` | resume when reachable |
| **Battery rejects/ignores command** | post-write confirmation poll mismatch | same as above (retry→`AUTO`→alert) | `battery_write_failed` | re-probe capability surface |
| **Manual change outside EMS** | observed mode ≠ EMS intent, not EMS-issued | respect for `manual_override_respect_minutes` (or reassert, per policy) | (info badge) | resume planning after window |
| **P1 / grid meter stale** | freshness age exceeded | reconstruction unreliable → `AUTO` | `meter_missing` | resume on fresh read |
| **Solar meter stale** | freshness age exceeded | use forecast for `solar_power_w`; flag | `meter_missing` | resume on fresh read |
| **Car meter stale** | freshness age exceeded | assume `ev_power_w=0`; widen load band | `meter_missing` | resume on fresh read |
| **Clock skew / NTP unsynced** | `health.ntp_check` | windows misalign → flag; avoid acting on suspect times | `ntp_unsynced` | re-sync; resume |
| **EMS process crash** | (external — supervisor) | battery stays in last (safe) mode; `restart: unless-stopped` | — | container restart; reload plan |
| **DB unwritable / full** | write error | continue control on in-memory state; stop sampling | (log) | free space; `VACUUM`; restore backup |
| **Max writes/day hit** | counter at cap | stop switching; hold current mode | (log) | resets next day |
| **No-trade day (unprofitable)** | `net_benefit ≤ 0` or savings < `daily_min_savings_eur` | `AUTO` all day (by design, not an error) | (info) | re-evaluate next replan |
| **SoC reading implausible (fresh)** | jump > `soc_max_jump_pct_per_5min`, or out of 0–100 | treat as stale → hold last safe mode; don't act on it | `meter_missing` | resume on a plausible read |
| **ML model absent / GPU unavailable** | capability detection / load fails | run the **statistical baseline + `rule_based`** planner | `ml_fallback_active` | load model / GPU back |
| **ML inference slow or low-confidence** | > `ml.inference_timeout_seconds`, or confidence < `confidence_min` | use last cached ML output or fall back to baseline (never blocks the control loop) | `ml_fallback_active` | next inference recovers |
| **ML plan invalid / `unsafe`** | fails the §8.11 validator | discard → `rule_based` plan → `AUTO` | `ml_fallback_active` | fix model/inputs |
| **Local LLM timeout / suspect output** | > `llm.timeout_seconds`, or output adds numbers not in the facts | use the **deterministic template reason** verbatim | (log) | next call recovers |
| **SPA asset / build load failure** | UI bundle won't load | API + control unaffected (headless); show a minimal status page | (log) | rebuild/redeploy UI |

## Failsafe precedence

When multiple conditions hold, the **most conservative** wins:

```
unknown/stale critical input  →  AUTO (self-consumption)  >  hold last safe mode  >  planned mode
```

`AUTO` is always the floor: it is the battery's own self-consumption behaviour, so the house is never worse off than with no EMS at all.
