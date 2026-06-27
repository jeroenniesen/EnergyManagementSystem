# ML layer — optional intelligence on a Jetson

> Companion to `../SPEC.md` §2 (design goals/non-goals), §8.11 (plan validator), `../GOAL.md` §4 (KISS/SOLID), §5 (ML), and `docs/control-model.md` §9 (Plan object, validator, ports).

The ML layer is **entirely optional**. The EMS core runs fully on a plain Raspberry Pi (CPU-only) using a statistical rolling load baseline and the rule-based planner with deterministic template explanations; ML is never on the critical path. The accelerator-gated parts (forecaster, optimizer, local LLM) activate on a **supported accelerator** — **CUDA** on an Nvidia Jetson, **Metal / Core ML / MLX** on Apple Silicon (a great dev/test host) — to improve forecast accuracy, schedule quality, and natural-language explanations. The **explainer is separate and not accelerator-gated**: a plain Pi can get richer explanations via the **`external_llm`** adapter (a cloud LLM API, e.g. MiniMax) with no GPU — at the cost of a network call and the privacy trade-off in §7. In every configuration the fail-safe and mode-switching constraints from `../SPEC.md` §2 apply unchanged: the system is always at least as good as "no EMS", ML outputs are always validated by the unchanged §8.11 plan validator, and the only outputs the ML layer can produce are high-level `BatteryIntent` + `target_soc` + deadline schedules — never continuous power setpoints.

---

## 1. Ports (the interfaces the core defines)

The core defines three **ports**. The statistical and rule-based implementations are always present. ML implementations are alternative adapters behind the same port; the core never knows which is running.

| Port | Default (Pi) implementation | ML implementation | Returns |
|---|---|---|---|
| `LoadForecaster` | Statistical rolling weekday + hour average over the reconstructed `non_ev_load_w` (`../SPEC.md` §8.1, 14-day window, cold-start 500 W) | `MlLoadForecaster` — an ONNX/torch model trained on the local SQLite history | `non_ev_load_w` per 15-min slot, with a confidence score |
| `Planner` | `RuleBasedPlanner` — deterministic summer/winter rules (`../SPEC.md` §8.2/§8.3); produces a validated `Plan` | `MlPlanner` — a learned optimizer that produces the same `Plan` domain object | `Plan` (id/version, slots, `BatteryIntent`, `target_soc`, deadlines, projected SoC, confidence, data-quality) |
| `Explainer` (**independent of the GPU/ML layer** — see note) | `TemplateExplainer` — deterministic format strings from the `ActionDecision` + plan numbers; offline, any device | `LocalLlmExplainer` (on-device LLM via an accelerator) **or** `ExternalLlmExplainer` (a cloud LLM API, e.g. MiniMax — HTTP only, **works on a plain Pi**); both only *rephrase* the already-computed reason | Human-readable reason string, traceable to real numbers |

> **The Explainer is selected by `explainer.mode` (§4) and is NOT gated by `ml.enabled` or an accelerator.** `template` and `external_llm` need no GPU and run on a Pi; only `local_llm` needs an accelerator. So a plain Pi can still get richer natural-language explanations via `external_llm` (at the cost of a network call and the privacy trade-off in §7/`../SPEC.md` §12). The `LoadForecaster` and `Planner` ML adapters remain accelerator-gated.

---

## 2. Planner-mode switch

`planner.mode` is a **runtime, UI-editable setting** (written to the runtime settings store in `/data`; effective without a restart). Three values:

| Mode | What executes | What is shown |
|---|---|---|
| `rule_based` *(default)* | `RuleBasedPlanner` produces and executes the `Plan` | Single plan in the UI |
| `ml` | `MlPlanner` produces the executed `Plan`; falls back to `rule_based` then `AUTO` if the plan is invalid or `unsafe` (see §5) | ML plan in the UI, with a fallback badge if the rule-based path was taken |
| `advisory` | `RuleBasedPlanner` produces the **executed** `Plan`; `MlPlanner` runs alongside and its proposed `Plan` + projected savings are **shown in the UI** for comparison — but never executed | Two plans side-by-side in the UI; trust-building before switching to `ml` mode |

The UI switch is on the settings page, token-protected, same-origin/CSRF-checked (same controls contract as `../SPEC.md` §9.1). The current planner mode is surfaced in the dashboard header alongside the ownership state.

---

## 3. "ML proposes, the validator disposes" — the core contract

Every ML-origin `Plan` passes through the **unchanged** plan validator (`../SPEC.md` §8.11, `docs/control-model.md` §9). ML cannot bypass any of the following:

- **Fail-safe:** if `data_quality == unsafe` or inputs are stale, no overriding action is issued (`../SPEC.md` §8.8, §16).
- **Reserve floor:** projected SoC never below `min_reserve_soc` across the plan horizon.
- **Season SoC ceiling:** `target_soc` always within `[reserve_soc, season_ceiling_soc]`.
- **Write cap + min dwell:** total mode switches ≤ `max_mode_switches_per_day`; each mode held for `min_mode_dwell_seconds`.
- **Window feasibility:** each charge window reachable in its slots at `max_charge_w` and before its deadline.
- **Evening-peak reservation:** enough SoC reserved at 09:00 for the evening peak (winter, `../SPEC.md` §8.3 step 5).
- **Cycle budget:** `max_cycles_per_day` / `max_cycles_per_month` respected.
- **Mode-switching only:** `MlPlanner` emits `BatteryIntent` + `target_soc` + deadlines — **not** continuous power setpoints. "Don't fight vendor control" (`../SPEC.md` §2) is enforced at the `mode_controller` level, which the ML layer never bypasses.

A `Plan` from `MlPlanner` that fails any validator check → keep the prior plan (or `ALLOW_SELF_CONSUMPTION`) and raise an alert; see §8 for the full fallback chain.

---

## 4. Config block

Lives in `config.yaml`; `docs/config-reference.md` references this section for the ML keys.

```yaml
planner:
  mode: rule_based        # rule_based | ml | advisory  (UI-editable runtime setting)

ml:                        # forecaster/optimizer layer — accelerator-gated (NOT the explainer)
  enabled: false          # master switch; auto-detected true when a supported accelerator is present
  require_accelerator: true # ML models load only on CUDA (Jetson) | Metal/CoreML/MLX (Apple Silicon); else baseline (never refuses to start)
  inference_timeout_seconds: 5   # inference slower than this counts as failure → fall back

  load_forecast:
    runtime: auto          # auto → onnxruntime(cuda|coreml) | torch(mps) | tensorrt
    model_path: /data/models/load_forecast.onnx
    confidence_min: 0.6    # below this confidence → fall back to statistical baseline

  optimizer:
    runtime: auto
    model_path: /data/models/optimizer.onnx

  training:
    schedule: "03:00"      # nightly on-device retrain
    history_source: sqlite # trains on the existing raw/derived SQLite history (SPEC §4.3)
    min_history_days: 30   # cold-start: stay on baseline until enough history
    retrain_min_interval_days: 1

explainer:                 # SEPARATE from ml: — selected regardless of ml.enabled / accelerator
  mode: template           # template | local_llm | external_llm
  local:                   # local_llm only — needs an accelerator
    runtime: auto          # auto → llama_cpp(metal/cuda) | ollama | mlx
    model_path: /data/models/explainer.gguf
    max_tokens: 200
    timeout_seconds: 8
  external:                # external_llm only — works on a plain Pi; needs internet + a key
    provider: minimax      # example; any OpenAI-compatible chat endpoint
    base_url: https://api.minimax.io/v1
    model: <model-id>
    api_key: !secret llm_api_key   # secret only; never logged/stored (SPEC §12)
    max_tokens: 200
    timeout_seconds: 8
    share: reason_and_facts        # minimal redacted payload (see §7); never raw history/secrets
```

All `ml.*`/`explainer.*` keys are read-only from `config.yaml`. `planner.mode` is UI-editable and overlaid from the runtime settings store. The **explainer is independent of `ml.enabled`**: `template`/`external_llm` need no accelerator (so a Pi can use `external_llm`); only `local_llm` requires the ML accelerator.

---

## 5. Training

- **On-device, nightly** at `ml.training.schedule` (default 03:00 local time), using `aiosqlite` to read the EMS's own SQLite history.
- **History source:** raw and derived tables (`../SPEC.md` §4.3) — `grid_power_w`, `solar_power_w`, `ev_power_w`, `battery_power_w`, `soc_pct`, derived `non_ev_load_w`, price slots, forecast records, and plan outcomes. No data is sent off-device; privacy is guaranteed by construction.
- **Cold-start:** if the history has fewer than `min_history_days` days, the `MlLoadForecaster` is not activated and the statistical baseline runs instead. The planner honours the same gate: no ML planning until the model has been trained on sufficient history.
- **Frozen fallback model:** a vendor-supplied frozen model (shipped with the image) covers the period before on-device training produces its first trained model, and serves as the fallback if a retrain fails. The frozen model is never overwritten by a failed retrain.
- **Retrain cadence:** at most once per `retrain_min_interval_days`; the last successful retrain timestamp is persisted. A failed retrain leaves the prior model in place and raises an alert.
- **Learning freeze** (inherits the core concept from `docs/control-model.md` §13): flagged unusual days (manual overrides, calibration periods) are excluded from the training set so they do not distort the models.

---

## 6. Serving runtime & budgets

| Model | Format | Runtime options (`runtime: auto` picks per platform) | Notes |
|---|---|---|---|
| `load_forecast` | ONNX | Jetson: `onnxruntime`-cuda / `tensorrt`. Apple Silicon: `onnxruntime`-**coreml** / `torch`-**mps**. CPU elsewhere | `tensorrt` fastest on Jetson; CoreML/MPS on Mac |
| `optimizer` | ONNX | same as above | same |
| `explainer.local` | GGUF | Jetson: `llama_cpp`-cuda. Apple Silicon: `llama_cpp`-**metal** / `ollama` / **mlx** | local LLM only; no network |
| `explainer.external` | — (HTTP) | any OpenAI-compatible API (e.g. MiniMax) | **no accelerator**; network call; works on a Pi (§7 privacy) |

**Jetson Orin Nano (~8 GB shared CPU/GPU RAM):** HA runs remotely (not competing for RAM); budget roughly 1–2 GB per model, with the optimizer and load forecaster loaded together and the LLM loaded on demand per decision. Total ML footprint should stay under 5 GB to leave headroom for the EMS process and OS. **Apple Silicon (M-series, dev/test):** unified memory and the GPU/ANE typically give *more* headroom than the Jetson — a good ML development host; run the models/LLM **natively** (Docker on macOS has no GPU passthrough — `../SPEC.md` §11.6).

**Latency budget:** `inference_timeout_seconds: 5` per model call. The optimizer and load forecaster are expected to complete in well under 1 s on the Jetson GPU; the LLM completer under `explainer.llm.timeout_seconds` (default 8 s).

**ML is off the 5-minute control critical path.** The control loop (`../SPEC.md` §5.3) reads the **last cached ML Plan** (or the statistical/rule-based baseline) and never waits for an in-flight ML inference. ML planning runs asynchronously on the replan schedule (`control.replan_times`), and the result is committed to the plan store when ready. A slow or timed-out inference simply means the cached plan continues to be used.

---

## 7. LLM explainer (local **or** external)

Both LLM explainers — `LocalLlmExplainer` (on-device, accelerator) and `ExternalLlmExplainer` (a cloud API, e.g. **MiniMax**; provider-agnostic over any OpenAI-compatible endpoint) — receive the **same** input and obey the **same** rules:
1. The **already-computed deterministic reason string** produced by `TemplateExplainer` (e.g. "charging: cheapest 3-hour window at €0.09/kWh; target SoC 72% by 07:00").
2. The numeric facts from the `ActionDecision` and `Plan` (slot prices, target SoC, confidence, forecast kWh, projected savings — the same fields as `PlannerInputSnapshot`, `docs/control-model.md` §9).

Their **only job** is to rephrase the deterministic reason into more natural prose. Constraints enforced in the prompt and validated post-generation:

- **Must not introduce numbers not present in the inputs.** Any response containing a number not found in the provided facts is rejected and the template string is used verbatim.
- `max_tokens: 200` — keeps responses focused; the template string is the fallback on timeout or refusal.
- `timeout_seconds: 8` — if the LLM does not complete within this window, `TemplateExplainer` output is used.

**`external_llm` — privacy & safety (the one off-device feature).** It works on a **plain Pi** (no accelerator), so it's how a Pi gets "a bit smarter." But it is the **only** feature that sends data off the device — a deliberate, bounded exception to local-first (`../SPEC.md` §12, `GOAL.md` §3):
- **Opt-in / off by default** (`mode: template`).
- **Minimal redacted payload** (`share: reason_and_facts`): only the deterministic reason + the few numbers it cites; **never** raw history, location, tokens, or secrets.
- **Never on the control path:** explanations are cosmetic; control is unaffected whether the call succeeds, is slow, or fails.
- **Graceful fallback:** any network error / timeout / quota / suspect output → the offline `TemplateExplainer` string, verbatim.
- The **API key is a secret** (env/secret file), never logged or stored in SQLite (`../SPEC.md` §12).

**Pi default:** `explainer.mode: template`. No LLM dependency enters the Pi image; `external_llm` adds only an HTTP client (no GPU/ML deps).

**Traceability requirement:** every reason string stored in `ActionDecision` records which explainer produced it (`template` | `local_llm` | `external_llm`) and the deterministic input reason it was derived from. Any displayed explanation can always be traced back to real plan numbers.

---

## 8. Fallback & degradation detection

Each capability degrades independently. All fallbacks are surfaced via the existing data-quality badge and alert system (`../SPEC.md` §4.7, §8.11, §9.3).

| Trigger | Affected capability | Fallback |
|---|---|---|
| `ml.enabled: false` or no supported accelerator detected (CUDA / Metal / CoreML / MLX) and `require_accelerator: true` | `LoadForecaster` + `Planner` ML adapters | Statistical baseline + `RuleBasedPlanner` |
| Model file absent at `model_path` | That model only | Same as above for the affected port |
| Inference exceeds `inference_timeout_seconds` | That inference | Cached prior result, then statistical/rule-based baseline; alert raised |
| `LoadForecaster` confidence below `confidence_min` | Load forecast | Statistical rolling baseline for that planning run |
| `MlPlanner` emits an invalid or `unsafe` Plan | Optimizer | Keep prior plan (or `ALLOW_SELF_CONSUMPTION`); alert raised (`../SPEC.md` §9.3) |
| Local **or** external LLM times out, errors, hits quota, or emits a number not in inputs | Explanation only (cosmetic) | `TemplateExplainer` string used verbatim; control unaffected |
| `external_llm` has no network / no API key | Explanation only | `TemplateExplainer` string used verbatim |
| `min_history_days` not yet reached | Training / ML models | Statistical baseline; ML models not loaded |
| Retrain fails | Model quality | Prior trained (or frozen fallback) model continues; alert raised |

Fallback events are logged with a reason and visible in the UI's data-quality badge. They do not interrupt the control loop.

---

## 9. Model explainability

ML models are made inspectable through three mechanisms:

- **Feature importance:** each `MlLoadForecaster` and `MlPlanner` inference logs the top-N input features and their relative weights (SHAP values or equivalent, computed at inference time if the runtime supports it, else approximated). Stored with the `PlannerInputSnapshot` (`docs/control-model.md` §9) so any plan is auditable.
- **Advisory diff:** in `advisory` mode the UI renders the `RuleBasedPlanner` and `MlPlanner` plans side by side — slot-by-slot intent, projected SoC, and estimated savings — so the user can directly inspect where and why the ML plan differs from the rule-based plan before trusting it with `ml` mode.
- **Logged inputs:** every ML inference records its full `PlannerInputSnapshot` (`docs/control-model.md` §9) — prices, forecast (provenance + percentiles), baseline, SoC, config hash, timestamp. Any plan can be replayed offline against the same inputs for debugging or audit.

These mechanisms together satisfy the requirement that ML model behaviour is explicable and auditable (`../GOAL.md` §5).

---

## 10. Module placement

The **accelerator-gated** adapters (forecaster, optimizer, local LLM) live in a dedicated sub-package, loaded only when `ml.enabled` resolves to `true` and a supported accelerator is found:

```
ems/
  ml/
    load_forecaster.py  # MlLoadForecaster: implements the LoadForecaster port
    optimizer.py        # MlPlanner: implements the Planner port
    explainer_local.py  # LocalLlmExplainer: implements the Explainer port (accelerator)
    training.py         # nightly on-device retrain job; frozen-fallback management
    capability.py       # accelerator detection (CUDA/Metal/CoreML/MLX); ml.enabled resolution; backend + adapter loader
```

The **non-gated** explainers live in the core (no GPU/ML deps), so they work on a Pi:
```
ems/planner/explain.py  # TemplateExplainer (default) + ExternalLlmExplainer (cloud API; HTTP client only)
```

Heavy accelerator deps (`onnxruntime-gpu`/`onnxruntime-silicon`, `torch`, `tensorrt`, `llama-cpp-python`, `mlx`) are **isolated to the ML image/sidecar** (Jetson, or a Mac-native process — `docs/jetson-deployment.md`, `../SPEC.md` §11.6). The Pi/EMS base image carries no ML deps; `ems/ml/` is never imported on the Pi code path. `ExternalLlmExplainer` adds only a lightweight HTTP client to the core image.

---

## 11. New failure modes

The ML layer introduces three failure modes not present in the core. They are documented in full in `docs/failure-modes.md`; brief descriptions here:

| Failure mode | Trigger | EMS response |
|---|---|---|
| Model OOM / accelerator crash | Runtime allocates more memory than available (Jetson VRAM / Mac unified) | Inference aborted; fallback path taken; alert `ml_inference_failed` raised; process continues |
| LLM timeout / hallucination guard (local **or** external) | LLM exceeds `timeout_seconds`, errors/quota, or generates an ungrounded number | `TemplateExplainer` output used; no alert (expected occasional degradation); logged |
| Accelerator unavailable | CUDA/Metal device lost after startup (driver crash, thermal shutdown) | `capability.py` re-checks each planning cycle; ML adapters unloaded; alert `ml_accelerator_unavailable`; statistical/rule-based fallback until restart |

---

## 12. Milestone

ML is a **post-M4 milestone** (label: **M6**), gated on the Jetson being in place and the core HEMS running stably in production:

1. **M6a — advisory mode.** Deploy `MlLoadForecaster` and `MlPlanner` with `planner.mode: advisory`. Run for at least two weeks; compare ML vs rule-based plans in the UI advisory diff; validate that the ML plan consistently passes the §8.11 validator and produces sensible projected savings.
2. **M6b — ml mode after dry-run acceptance.** Switch `planner.mode: ml` behind `control.dry_run: true`; run a dry-run acceptance period (same gate as every strategy milestone, `../SPEC.md` §14/§15); enable live execution only after the acceptance comparison clears.
3. **M6c — LLM explainer** (optional, independent of M6a/b). Enable `explainer.mode: local_llm` (accelerator) **or** `explainer.mode: external_llm` (cloud API — the only path that works on a plain Pi); validate traceability, the no-invented-numbers guard, and (for `external_llm`) the redaction/privacy policy and template fallback on a sample of decisions before enabling in production.

The core (`M0`–`M4`) is unaffected by the M6 milestone. The Pi build never changes (the `external_llm` explainer adds only an HTTP client, no ML deps).
