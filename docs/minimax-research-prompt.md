# Research Prompt — How can a MiniMax subscription benefit this Home Energy Management System?

> Paste everything below the line into your research agent / LLM. It is self-contained (the
> researcher does **not** need repo access), but if the agent *can* read the repository, point it at
> `SPEC.md`, `GOAL.md`, `docs/ml-layer.md`, and `docs/control-model.md` for ground truth.
> The "About the application" section is the binding context; the constraints in §4 are
> non-negotiable and any recommendation that violates them is invalid.

---

## 0. Your role

You are a **senior AI research analyst** evaluating where a specific commercial AI platform
(**MiniMax**) can add real, defensible value to a specific product (a home battery energy-management
system, "HEMS"). You are rigorous and **critical, not promotional**: your job is to find the few
high-value, well-fitting uses and to explicitly reject the poorly-fitting ones. Distinguish
"a nice demo" from "worth building and running in production." Where you make a factual claim about
MiniMax (a model, a price, a limit, a data-handling term), **cite a source and date it**, and flag
anything you could not verify — MiniMax's catalogue changes quickly and your training data may be
stale.

## 1. The objective (single question)

**Given that the owner already has a MiniMax subscription, what are the highest-value, lowest-risk
ways this HEMS can use MiniMax — and which tempting uses should be avoided?**

A great answer is a **ranked, evidence-based shortlist** of concrete opportunities, each scored
against value, fit, effort, cost, and the product's hard constraints (§4), with an integration
sketch that names where it slots into the existing architecture — plus an honest "poor fit / do not
build" list.

## 2. About the application (ground your analysis in this)

**What it is.** A self-hosted HEMS that switches the *operating mode* of an Indevolt home battery
(2-tower cluster, ~10.8 kWh, ~4 kW) based on a solar forecast and dynamic day-ahead electricity
prices (Tibber, NL). Goal: run the house on battery overnight in summer; arbitrage cheap/expensive
price windows in winter. It runs on a **Raspberry Pi 5** (CPU-only) next to Home Assistant, or
optionally on an **Nvidia Jetson / Apple Silicon** which adds a local GPU ML layer. Backend:
Python 3.12 / FastAPI; its own React dashboard; local **SQLite** history.

**Design philosophy (this shapes everything):**
- **Local-first / the user owns their data.** Telemetry, training, and all control decisions stay
  on the device. There is exactly **one** sanctioned exception (see below).
- **Fail-safe.** The system must never be worse than "no EMS." If anything is stale or uncertain it
  falls back to the battery's own self-consumption mode.
- **Mode-switching, not continuous control.** It commands the battery only when the desired *mode*
  changes (target < 10 writes/day) — it never runs a tight power-tracking loop, and it never
  fights the vendor's fast P1-zeroing controller.
- **Ports / dependency inversion.** The core depends on **interfaces** (`ports.py`):
  `LoadForecaster`, `Planner`, `Explainer`, `SolarForecaster`, `PriceSource`, `BatteryDriver`.
  Concrete vendors (Solcast vs Forecast.Solar, rule-based vs ML planner, etc.) are interchangeable
  adapters behind those ports. **This is the seam any AI feature must slot into.**
- **Deterministic decisions; AI only *phrases* or *advises*.** Every decision (and every *non*-action)
  is computed deterministically and carries a human-readable reason. A planner-mode switch
  (`rule_based` default | `ml` | `advisory`) governs planning; any ML plan must pass the **unchanged**
  safety/plan validator ("ML proposes, the validator disposes"). The battery is "one writer behind
  one interface."

**The MiniMax-shaped hole that already exists.** The spec already names MiniMax as the example
provider for an **`external_llm` explainer**:
- The `Explainer` port turns the already-computed deterministic reason + the few numbers it cites
  into natural-language "why" text shown in the UI (including *why it is **not** acting*).
- Three backends: `template` (offline, default), `local_llm` (on-device, needs a GPU), and
  **`external_llm`** — *"a cloud LLM API, e.g. MiniMax; works on a plain Pi"*. Config sketch:
  `provider: minimax`, `base_url: https://api.minimax.io/v1`, `model: <id>`, `api_key: !secret`,
  `max_tokens: 200`, `timeout_seconds: 8`, `share: reason_and_facts`.
- It is **off by default, opt-in**; sends only a **minimal redacted payload** (the deterministic
  reason + the numbers it cites — *never* raw history, location, tokens, or secrets); is **never on
  the control path**; and **falls back to the offline template** on any error/timeout/quota/suspect
  output. The LLM **may not introduce a number not present in the inputs** (post-generation guard).
- This is the **only** feature in the entire system that sends data off the device — a deliberate,
  bounded exception to local-first.

So MiniMax has a *real, already-architected* entry point. Your research should treat that as the
baseline opportunity and then ask: **what else, if anything, is worth it — and what is not?**

## 3. What to research about MiniMax (verify current facts)

Establish, with dated sources, MiniMax's **current** offering as relevant to this product:
- **Text / reasoning models** available via the API (families, context-window sizes, long-context
  and reasoning variants), and whether the chat API is genuinely **OpenAI-compatible** (so the
  provider-agnostic adapter works unchanged), including **function/tool calling** support.
- **Speech**: text-to-speech (quality, voices, **languages incl. Dutch**, latency, streaming, voice
  cloning) and speech-to-text/ASR.
- **Other modalities** (vision-language, image, music, video) — note them, but assess fit honestly
  (this app has almost no image/video surface).
- **Commercials & operations**: what the **subscription** covers vs. metered/per-API billing;
  token/character pricing for the relevant models; **rate limits & quotas**; **latency** and
  **regional endpoints / data residency** (the deployment is in the Netherlands/EU); **uptime/SLA**.
- **Privacy & data-handling terms** (critical): does MiniMax **retain** API inputs/outputs, for how
  long, and does it **train on customer data** by default or offer an opt-out / zero-retention
  mode? EU/GDPR posture? This directly gates whether the off-device exception is acceptable, and
  what the user must be told.

## 4. Hard constraints — every recommendation MUST satisfy these (checklist)

Reject or redesign any idea that fails any of these:
1. **Never on the control path.** MiniMax output may never decide, delay, or alter a battery
   command. The §8.11 plan validator and the mode-controller are never bypassed. AI is advisory or
   cosmetic only.
2. **Fail-safe / graceful fallback.** Any MiniMax call can be slow, fail, hit quota, or be offline;
   the feature must degrade to a fully-functional local default (e.g. the template explainer) with
   no user-visible breakage.
3. **Local-first by default.** Any off-device data flow is **opt-in, off by default**, and is an
   explicit, justified extension of the *one* sanctioned exception. Prefer designs that keep new
   data on-device.
4. **Minimal redacted payload.** Send only what the feature strictly needs — never raw meter
   history, location/coordinates, secrets/tokens, or anything that could re-identify the household.
   Specify the exact payload for each idea.
5. **Grounded / no invented numbers.** If MiniMax produces text about the system, every figure must
   trace to real, provided data; ungrounded output is rejected and the deterministic fallback used.
6. **Runs on a plain Raspberry Pi.** No GPU/accelerator dependency may enter the Pi image; HTTP-only
   clients are fine. (Accelerator-only ideas belong to the separate, local, GPU-gated ML layer — call
   that out if relevant, but it is *not* MiniMax's lane.)
7. **Provider-agnostic.** Integrate behind the existing port/OpenAI-compatible adapter so MiniMax can
   be swapped without core changes — avoid lock-in. Note where an idea would *require* a
   MiniMax-specific capability (and whether that lock-in is worth it).
8. **Cost-proportionate.** This is a single household with < 10 control actions/day and infrequent
   explanations. Quantify expected monthly token/character/audio volume and cost; reject features
   whose cost or complexity dwarfs their value.
9. **Secrets discipline.** API keys via secret/env only — never logged or stored in SQLite.

## 5. Candidate opportunity areas (a seed list — confirm, expand, or reject each)

For each, decide: is it real value here, does it pass §4, does it *need* MiniMax specifically?
1. **Richer explanations (the existing `external_llm` hook).** Better, more natural "why / why-not"
   phrasing than the templates — the baseline opportunity. Consider **localization** (e.g. Dutch
   explanations for a Dutch household — the app is English-only v1) as a concrete win.
2. **Grounded conversational assistant.** "Why didn't you charge last night?", "What happens if
   tomorrow is cloudy?", "Was last week cheaper than usual?" — answered by grounding MiniMax on the
   *local* deterministic reasons, plan objects, and SQLite history (retrieval/RAG + tool/function
   calls into the read-only EMS API). Advisory only; same redaction + grounding guards.
3. **Spoken / accessible summaries (TTS).** A short daily/morning natural-language + **voice**
   briefing ("Today you'll run on solar and cover the evening peak from the battery"), and
   accessibility for low-vision users. Assess MiniMax TTS quality/latency/Dutch support.
4. **Voice queries (ASR + TTS).** Ask the dashboard questions by voice (advisory only; never voice
   *control* of the battery without the full guardrails — and even then, advisory-first).
5. **Periodic natural-language reports.** Weekly/monthly savings & behaviour summaries, anomaly
   call-outs ("your overnight load was 30% higher than usual on Tuesday"), generated from local
   aggregates.
6. **Long-context system analysis.** Use a large-context model to ingest a long span of plan/outcome
   logs and produce an advisory "how is my system doing / is the forecast drifting / are your
   settings well-tuned" review. Off the control path, opt-in, redacted.
7. **Setup / configuration assistant.** Conversationally help the user set tilt/azimuth, reserve,
   strategy, device IPs — a guided onboarding.
8. **Explicitly evaluate and most likely *reject*: LLM-as-forecaster / LLM-as-optimizer.** Be
   skeptical: numeric load/solar forecasting and schedule optimization are better served by the
   local statistical baseline and the (local, GPU-gated) ML models. State clearly whether MiniMax
   adds anything here, and if not, say so — don't force it.

You are encouraged to propose opportunities **not** on this list. You are equally encouraged to
shoot any of these down with reasons.

## 6. Evaluation rubric (score every surviving opportunity)

Rate each 1–5 (or High/Med/Low) and justify:
- **Homeowner value** — does a non-technical owner actually benefit?
- **Constraint fit** — passes the §4 checklist cleanly? (a fail here = drop or redesign)
- **Implementation effort** — where it slots in (which port/adapter/file), how much new surface.
- **Ongoing cost** — estimated monthly MiniMax usage & € cost for one household.
- **Latency & reliability** — acceptable given it's off the control path? fallback quality?
- **Privacy delta** — exactly what leaves the device; is the trade worth it; what must the user be told?
- **MiniMax-specificity** — does it exploit a real MiniMax strength (long context, Dutch TTS, price),
  or would any LLM do (i.e., is the *subscription* the reason, or incidental)?
- **Differentiation** — does it make the product meaningfully better/more delightful?

## 7. Required output format

1. **Executive summary** (≤ 8 sentences): the 2–3 opportunities worth pursuing now, the headline
   reason, and the single biggest risk.
2. **Ranked opportunities.** For each: *what it is · MiniMax capability used · value · integration
   sketch (named port/file/config) · exact redacted payload · est. monthly cost · latency/fallback ·
   privacy delta · rubric scores · verdict (build now / pilot / later / no)*.
3. **Recommended phased plan.** Concrete first step (almost certainly: enable + harden the existing
   `external_llm` explainer, with localization), then 2–3 follow-ons gated on it proving out.
4. **Poor-fit / do-not-build list** with reasons (so the owner doesn't chase them later).
5. **Cost model.** A small table of assumed monthly volumes → estimated MiniMax cost, and what the
   subscription covers vs. meters separately.
6. **Privacy & compliance note.** What MiniMax does with inputs (cite terms + date), EU/data-residency
   posture, and the exact user-facing disclosure each recommended feature requires.
7. **Open questions / facts to verify** — anything you couldn't confirm, with how to confirm it.

## 8. Method (you may iterate)

Work in passes and refine: (1) establish MiniMax's current capabilities, pricing, limits, and
privacy terms from primary sources; (2) map each capability against this app's architecture and the
§4 constraints; (3) score and rank; (4) adversarially review your own shortlist — for each "build"
verdict, argue the opposite and see if it survives; drop anything that doesn't. Prefer fewer,
stronger recommendations over a long wish-list. Call out every assumption.
