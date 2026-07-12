# Configuration reference

> Companion to `../SPEC.md` §9. The spec shows a *sample* `config.yaml`; this is the **per-key reference** (type, range, default, effect). `config.yaml` holds read-only **defaults**; UI-editable keys are overlaid from the runtime settings store in `/data` (**effective = defaults + runtime**). Secrets use `!secret`/env and are **never** persisted to the settings store or logs.

Legend: **UI** = also editable from the web UI (overlays the file). **CONFIRM** = needs M0/M1 hardware validation.

## `site`
| Key | Type | Default | Effect |
|---|---|---|---|
| `latitude` / `longitude` | float | 52.1 / 5.1 | forecast location + sunrise/sunset. **UI** (map pin) |
| `timezone` | IANA tz | Europe/Amsterdam | slot alignment, DST. **UI** (or `timezonefinder`) |

## `battery`
| Key | Type | Range/Default | Effect |
|---|---|---|---|
| `model` | str | `solidflex_2000` | informational |
| `usable_kwh` | float | 9.7 · **CONFIRM** | energy budget for planning |
| `max_charge_w` / `max_discharge_w` | int W | 4000 · **CONFIRM** | charge-time sizing; discharge cap |
| `min_reserve_soc` | % | 10 | never discharge below |
| `round_trip_efficiency` | 0–1 | 0.90 | arbitrage economics + SoC projection |
| `min_mode_dwell_seconds` | int | 600 | min time in a mode (anti-flap) |
| `allow_export_discharge` | bool | false | if false, serve load via `AUTO`; never force-discharge to export (§7.1/§8.3) |
| `manual_override_policy` | enum | `respect`\|`reassert` = respect | how to treat out-of-EMS changes |
| `manual_override_respect_minutes` | int | 120 | how long to respect a manual change |
| `takeover_policy` | enum | `stand_down`\|`override` = stand_down | if battery already in a vendor schedule |
| `startup_grace_seconds` | int | 120 | observe-only after boot until HA entities settle |
| `soc_max_jump_pct_per_5min` | % | 20 | plausibility: reject larger SoC jumps |

## `solar`
| Key | Type | Default | Effect |
|---|---|---|---|
| `kwp` | float | 3.0 | forecast scaling |
| `tilt` / `azimuth` | deg | 35 / 0 (0=south) · **CONFIRM** | forecast geometry. **UI** |
| `forecast_provider` | enum | `solcast`\|`forecast_solar` = solcast | primary forecast |
| `forecast_fallback` | enum | forecast_solar | used when primary stale/down (rate-limited ~12/hr) |
| `forecast_refresh_owner` | enum | `ems`\|`ha` = ems | single owner of the Solcast budget |
| `solcast_daily_call_budget` | int | 10 | hard ledger cap/day |
| `solcast_refresh_times` | list[HH:MM] | 7 daylight slots | when EMS refreshes (within budget) |
| `forecast_correction_bounds` | [lo,hi] | [0.7, 1.3] | clamp rolling forecast/actual factor |
| `use_percentiles` | map | {expected: p50, *_commit: p10} | P50 expected, P10 for commitments |

## `prices`
| Key | Type | Default | Effect |
|---|---|---|---|
| `provider` | enum | `tibber`\|`energyzero` = tibber | price source |
| `tibber_token` | secret | `!secret` | GraphQL auth |
| `resolution` | enum | quarter_hourly | planner granularity (auto-expands hourly→4×15min) |
| `cache_immutable_slots` | bool | true | never re-fetch past slots |
| `tomorrow_required_by` | HH:MM | 15:00 | after this, missing tomorrow ⇒ stale |
| `grid_fees.tibber_total_includes_all` | bool | false · **CONFIRM** | whether to add extra fees |
| `grid_fees.import_fee_eur_per_kwh` | €/kWh | 0.0 | added to import price if above false |
| `grid_fees.export_fee_eur_per_kwh` | €/kWh | 0.0 | export cost |
| `export_price_model` | enum | `net_metering`\|`spot_minus_tax`\|`fixed` = net_metering | how each exported kWh is valued (`economics.export_value`, §8.3): `net_metering`=full price (today's saldering); `spot_minus_tax`=post-2027 (may go negative, unclamped); `fixed`=flat feed-in. **UI** |
| `energy_tax_eur_per_kwh` | €/kWh | 0.13 | subtracted from spot when export = `spot_minus_tax`. **UI** |
| `fixed_feed_in_eur_per_kwh` | €/kWh | 0.01 | flat export value when export = `fixed`. **UI** |
| `export_tariff_eur_per_kwh` | €/kWh | 0.0 | (legacy) flat export value; superseded by `export_price_model` |

## `arbitrage`
| Key | Type | Default | Effect |
|---|---|---|---|
| `degradation_cost_eur_per_kwh` | €/kWh | 0.05 | wear allowance in profitability test |
| `risk_margin_eur_per_kwh` | €/kWh | 0.02 | safety margin in profitability test |
| `arbitrage_min_spread_eur` | €/kWh | 0.12 | coarse floor (not the only test) |
| `daily_min_savings_eur` | € | 0.20 | below ⇒ no-trade mode |
| `max_cycles_per_day` | float | 1.5 | equivalent full cycles for arbitrage |
| `max_cycles_per_month` | float | 30 | monthly cycle budget |
| `min_grid_charge_kwh` | kWh | 0.5 | don't schedule tiny grid charges |
| `max_daily_grid_charge_kwh` | kWh | 12 | hard cap on grid energy bought/day |

## `consumption`
| Key | Type | Default | Effect |
|---|---|---|---|
| `source` | enum | `learned`\|`fixed` = learned | baseline source |
| `learning_window_days` | int | 14 | rolling average window |
| `cold_start_w` | int W | 500 | per-hour load until enough history |
| `exclude_ev_from_baseline` | bool | true | subtract car meter only while charging |
| `ev_charging_threshold_w` | int W | 200 | above ⇒ "car is charging" |

## `ev` (v1: charging **advice** only — visual/advisory, see `SPEC.md §16` + `docs/v2-ev-control.md`)
| Key | Type | Default | Effect |
|---|---|---|---|
| `advice_enabled` | bool | false | master switch for the Car card/panel (web + iOS) and the Settings "Car" group. **UI** |
| `car_id` | str (slug) | "" | picked car (`ems/cars.py`); "" = custom, user enters capacity/charger power directly. **UI** |
| `battery_kwh` | kWh | 57.5 | usable car battery capacity; autofilled by the car picker, overridable. **UI** |
| `charge_efficiency` | 0–1 | 0.90 | AC→battery charging efficiency `η_c`, used by the SoC estimate and the planner. **UI** (advanced) |
| `charger_kw` | kW | 11.0 | home wallbox power; effective charge rate is `min(charger_kw, car.max_ac_kw)`. **UI** |
| `schedule` | JSON (per-day) | all days off, 80%/07:30 | the weekly minimum-charge schedule (`enabled`/`min_pct`/`ready_by` per day-of-week), edited via the schedule editor; parsed tolerantly by `ems/ev_schedule.parse_schedule`. **UI** |
| `charge_kwh` | kWh | 20.0 | **legacy** — a flat "typical top-up" size. Superseded by `schedule` (per-deadline `required_kwh`, computed from the SoC anchor); used by the deprecated quick-advice endpoint only (`GET /api/advisor/ev-charge`, kept for compatibility — do not extend it). Not read by `GET /api/car/plan`. |
| `departure_time` | HH:MM | 07:30 | **legacy** — a single daily departure time. Superseded by `schedule`'s per-day `ready_by`; used by the deprecated quick-advice endpoint only (`GET /api/advisor/ev-charge`). Not read by `GET /api/car/plan`. |

The car's SoC itself is **not** a config key — it's a runtime-store anchor (%, timestamp) set via `POST /api/car/soc` and estimated forward from measured charging (`ems/ev_session.py`); see `SPEC.md §16`.

## `strategy`
| Key | Type | Default | Effect |
|---|---|---|---|
| `mode` | enum | auto | auto/summer_solar/winter_arbitrage/manual. **UI** |
| `summer_months` | list[int] | [4..9] | calendar coarse override |
| `summer_solar_threshold_kwh` | kWh | 12 · **CALIBRATE** | rolling forecast to count as summer |
| `strategy_switch_hysteresis_days` | int | 3 | consecutive days past band before switching |
| `strategy_switch_band_kwh` | kWh | 2.0 | hysteresis band around threshold |
| `night_reserve_kwh` | kWh | 2.0 | extra reserve on overnight need. **UI** |
| `avoid_precharge_before_solar` | bool | true | skip pre-dawn grid charge before strong solar |
| `negative_price_soak` | bool | false | negative-price policy (replaces `midday_negative_price_action`); runtime key `planner.negative_price_soak`, see `planner` below |
| `target_soc_ceiling` | map | {summer: 95, winter: 90} | don't charge above unless needed (cell life) |
| `hold_reserve_blocks_solar_charge` | bool | false | HOLD_RESERVE: false = solar may still charge |
| `borderline_day_policy` | enum | `solar_first`\|`price_first` = solar_first | wait for solar vs buy cheap on borderline days |
| `reserve_policy` | enum | `economy`\|`comfort` = economy | bias toward cost vs more morning battery |

## `control`
| Key | Type | Default | Effect |
|---|---|---|---|
| `cycle_seconds` | int | 300 | sense→decide loop period |
| `max_mode_switches_per_day` | int | 10 | protects Indevolt API (persisted, per local date) |
| `replan_times` | list[HH:MM] | [13:15, 06:00] | scheduled replans |
| `dry_run` | bool | true | log decisions, no writes (per-strategy gate) |
| `min_replan_interval_seconds` | int | 600 | cap replan churn |
| `soc_deviation_replan_pct` | % | 10 | planned-vs-actual SoC gap that triggers a replan |

## `homeassistant`
| Key | Type | Default | Effect |
|---|---|---|---|
| `base_url` | url | http://homeassistant.local:8123 | HA endpoint |
| `token` | secret | `!secret` | long-lived token |
| `entity_map` | map | — | role→entity id (pin; validated at startup) |

## `mqtt`
| Key | Type | Default | Effect |
|---|---|---|---|
| `host` | str | localhost | broker (use host IP on compose) |
| `topic_prefix` | str | ems | topic namespace |
| `publish_discovery` | bool | true | expose HA entities; false = UI only |
| `retain_config` | bool | true | retain discovery configs |

## `web`
| Key | Type | Default | Effect |
|---|---|---|---|
| `enabled` | bool | true | serve the UI |
| `bind` / `port` | str/int | 0.0.0.0 / 8080 | listen address (**LAN only; never expose to internet**) |
| `auth` | enum | `bearer`\|`basic` = bearer | auth scheme |
| `auth_token` | secret | `!secret` | UI auth |
| `guest_readonly` | bool | true | optional read-only dashboard |
| `theme` | enum | `auto`\|`light`\|`dark` = auto | UI theme |

> The frontend is **React + Vite**, built at image-build time and served by FastAPI (SPA fallback); all deps bundled, **no runtime CDN** (`SPEC §9.1`). There is no `chart_lib` key any more — charts are an npm dependency.

## `planner`
| Key | Type | Default | Effect |
|---|---|---|---|
| `mode` | enum | `rule_based`\|`ml`\|`advisory` = rule_based | which planner produces the executed `Plan` (`SPEC §8`). **UI-editable.** `ml`/`advisory` require the ML layer |
| `negative_price_soak` | bool | false | opt-in: charge on sub-zero-priced slots (you're PAID to consume), up to headroom — even outside a normal cheap window and with summer grid top-up off. Off = today's behaviour (§8.2 step 5). Applies to the winter, adaptive and summer planners. **UI** |

## `ml` (optional forecaster/optimizer layer — off on a plain Pi; full schema in `ml-layer.md`)
| Key | Type | Default | Effect |
|---|---|---|---|
| `enabled` | bool | false | master switch; auto-true when a supported accelerator is detected |
| `require_accelerator` | bool | true | ML models load only on CUDA (Jetson) / Metal·CoreML·MLX (Apple Silicon); else statistical baseline (never refuses to start) |
| `inference_timeout_seconds` | int | 5 | slower inference ⇒ fall back to baseline |
| `load_forecast.runtime` / `.model_path` / `.confidence_min` | enum/path/float | auto / … / 0.6 | ML load forecaster; `auto`→onnxruntime(cuda\|coreml)\|torch(mps)\|tensorrt; below confidence ⇒ baseline |
| `optimizer.runtime` / `.model_path` | enum/path | auto / … | the `MlPlanner` (still passes the §8.11 validator) |
| `training.schedule` / `.history_source` / `.min_history_days` | HH:MM/enum/int | 03:00 / sqlite / 30 | on-device nightly retrain on the SQLite history |

## `explainer` (how the "why" text is phrased — **independent** of the `ml` layer)
| Key | Type | Default | Effect |
|---|---|---|---|
| `mode` | enum | `template`\|`local_llm`\|`external_llm` = template | `template`=offline strings (any device); `local_llm`=on-device, needs an accelerator; `external_llm`=cloud API, works on a Pi |
| `local.runtime` / `.model_path` / `.timeout_seconds` / `.max_tokens` | enum/path/int/int | auto / … / 8 / 200 | on-device LLM (`auto`→llama_cpp·metal\|ollama\|mlx) |
| `external.provider` / `.base_url` / `.model` | str/url/str | minimax / … | example: any OpenAI-compatible chat endpoint |
| `external.api_key` | secret | `!secret` | **secret only** — never logged/stored (§12) |
| `external.timeout_seconds` / `.max_tokens` | int/int | 8 / 200 | per-call limits; on failure ⇒ `template` |
| `external.share` | enum | `reason_and_facts` | **minimal redacted payload** — the deterministic reason + the few numbers it cites; never raw history/secrets (privacy, §12) |

## `history`
| Key | Type | Default | Effect |
|---|---|---|---|
| `db_path` | path | /data/ems.sqlite | SQLite location |
| `sample_seconds` | int | 60 | sampling cadence |
| `retention_days` | int | 365 | purge older samples |
| `vacuum_on_start` | bool | true | reclaim space on boot |
| `backup_dir` | path | /data/backups | DB/config backup target |

## `health`
| Key | Type | Default | Effect |
|---|---|---|---|
| `ntp_check` | bool | true | alert on clock drift (time-critical windows) |

## `dev` (local development / testing — `SPEC §11.6`)
| Key | Type | Default | Effect |
|---|---|---|---|
| `mode` | enum | `live`\|`mock`\|`replay` = live | `mock`/`replay` need no HA/battery/GPU and **force `dry_run`** — for running on a Mac etc. |
| `fixtures_dir` | path | /data/fixtures | canned Tibber/Solcast/HomeWizard/HA payloads for `replay` (§14) |
