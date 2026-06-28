# MiniMax × HEMS — Research Findings

> Research executed against [`docs/minimax-research-prompt.md`](minimax-research-prompt.md).
> **Process:** 5 research iterations (research → improve → validate → test → research-again) — facts
> gathered via parallel web research, then put through an **adversarial validation pass** (which
> corrected the M2.5 price, the cost range, the grounding-guard claim, and the privacy wording), a
> **working prototype + test** (§4), and a final re-verification. Verified via web on **28 Jun
> 2026**; MiniMax's catalogue changes fast, so every figure is dated and sourced and anything
> unverified is flagged. Binding constraints (never on the control path; fail-safe; local-first /
> opt-in; minimal redacted payload; grounded / no invented numbers; runs on a plain Pi;
> provider-agnostic; cost-proportionate) come from `SPEC.md` §12, `GOAL.md` §3/§5, `docs/ml-layer.md`.

## Executive summary

- **The technology and cost are non-issues.** MiniMax is OpenAI-compatible (`api.minimax.io/v1`,
  works with the stock OpenAI SDK), and at a single household's volume every use here costs only a
  **few dollars/month at most** (the explainer ≈ **$0.40–4/mo** at MiniMax's *first-party* PAYG
  rate; a daily Dutch spoken summary < $0.01/mo).
- **Build now:** harden + enable the already-specified **`external_llm` explainer** (`provider:
  minimax`), and use it to also deliver **Dutch** explanations (the UI is English-only v1). Trivial
  integration, a few €/month, fully inside the system's one sanctioned off-device exception.
- **Strong second (pilot):** a short **spoken Dutch daily summary** via MiniMax TTS — its confirmed
  nl-NL voices (cheap; ~$60/1M chars) are the one place MiniMax is genuinely *differentiated* versus
  "any LLM."
- **The real constraint is privacy, not cost.** MiniMax is a Chinese company; its own API privacy
  pages are **unverifiable** (JS-rendered), its Open Platform terms **permit using inputs to
  "improve services"** (so assume they are used), and there is **no documented EU residency, DPA, or
  zero-retention** option. The system's defense already exists: the payload is a **minimal,
  non-identifying** redacted string — lean on that, keep it opt-in, and offer a zero-retention
  routing path (M3-text only). The provider-agnostic design means MiniMax is never locked in.
- **Biggest risk:** data-handling/residency (unverified MiniMax terms) — mitigated by minimal
  redaction + an opt-in ZDR routing option + the OpenAI-compatible (swappable) adapter.
- **Avoid:** LLM-as-forecaster/optimizer, anything on the control path, and shipping raw history to
  MiniMax for "analysis."

---

## 1. MiniMax capabilities (verified 28 Jun 2026)

### 1.1 Text / reasoning models (`api.minimax.io`)

| Model | Status | Context | Input $/1M | Output $/1M | Notes |
|---|---|---|---|---|---|
| **MiniMax-M3** | current / flagship | ~1M (price breaks >512k) | $0.30 (≤512k) | $1.20 | very long context, reasoning; cache-read $0.06/M; "50% permanent" rate |
| **MiniMax-M2.7** | current (~Mar 2026) | ~205K | $0.30 | $1.20 | agentic/tool use, coding, reasoning |
| **MiniMax-M2.5** | legacy, still callable | ~205K | **$0.30** | **$1.20** | fine for the rephrase task |
| M2.1 / M2 | legacy | ~197K | ~$0.26 | ~$1.00 | first M2-gen |
| MiniMax-01 / M1 | likely retired | — | — | — | not on the current pay-go page (unverified) |

**Pricing correction (validated iteration 3/5):** MiniMax's **first-party PAYG** rate for M2.5 is
**$0.30/$1.20** (listed under "Legacy"), *not* the $0.12/$0.48 some trackers show — that lower
figure is **OpenRouter's promotional** rate. At first-party rates the M-series text models all
cluster around **$0.30/$1.20**, so choose by quality/latency, not price (M2.5 is ample for a
rephrase). Since we mandate a first-party key for privacy (§2.1), all costs below use $0.30/$1.20.

### 1.2 OpenAI-compatibility (validates the existing adapter design)

- **base_url** `https://api.minimax.io/v1`; **Bearer** auth (works with `OPENAI_API_KEY`).
- The official **OpenAI Python SDK** `chat.completions.create()` works unchanged (swap base_url + key).
- **Streaming** yes; **tool/function calling** yes; `temperature` [0,2], `max_tokens` accepted.
- **JSON / structured-output mode**: *not documented* on the OpenAI-compat page — don't rely on it;
  use prompt-level formatting + post-generation validation.

### 1.3 Rate limits

- M3 ≈ 200 RPM / 10M TPM; M2.x ≈ 500 RPM / 20M TPM; free tier ≈ 1,000 req/day. **Orders of
  magnitude above** a household's handful of calls/day. No published uptime SLA.

### 1.4 Speech / audio (TTS, ASR)

- **TTS models:** `speech-2.8` / `speech-2.6` (`-hd` / `-turbo`), legacy `T2A`. 300+ voices, voice
  cloning from ~10 s, streaming, **<250 ms** latency (2.6 Turbo).
- **Dutch (nl-NL + nl-BE): CONFIRMED** — dedicated Dutch TTS page; Dutch listed among ~40 languages.
  *Caveat: Dutch voice naturalness is unverified — do a 5-minute listening test before shipping.*
- **Pricing:** turbo ≈ **$60/1M chars**, HD ≈ **$100/1M chars** ⇒ a daily ~400-char summary
  ≈ **< $0.01/month**.
- **ASR:** referenced in secondary sources, **not confirmed** on MiniMax's own docs; not needed for
  one-way audio.

### 1.5 Other modalities (image / music / video)

- **Hailuo video**, **Music 2.6**, **image generation** — **low/no fit** for a text energy
  dashboard. Noted for completeness; not recommended.

---

## 2. Commercials & privacy

### 2.1 Billing — which "subscription" do you actually have?

Two **separate** systems exist; clarify which the owner holds:
- **Pay-as-you-go (PAYG)** — standard Open Platform API keys, metered per token. MiniMax explicitly
  **recommends PAYG for production API use**. This is what the HEMS should use.
- **Token Plan (Plus/Max/Ultra)** — a quota-backed *Subscription Key* with rolling 5-hour/weekly
  windows, aimed at interactive coding-agent use, **not** continuous API load. The **consumer
  Hailuo/MiniMax app** has its own separate plans (and free daily credits).
- **Implication:** a consumer/app subscription does **not** necessarily grant production API quota —
  the explainer/TTS should run on a PAYG API key. Either way the cost is pennies/month.

### 2.2 Endpoints & data residency

- Two hosts: **international `https://api.minimax.io/v1`** and **China `https://api.minimaxi.com/v1`**.
  MiniMax is a **Chinese company**; the physical hosting location of `api.minimax.io` is **not
  stated**, and **no EU-region endpoint** is documented. Latency from NL is acceptable for an
  off-control-path, non-real-time feature.

### 2.3 Privacy (the gating finding — honest about uncertainty)

- MiniMax's **own** API policy pages (`platform.minimax.io/protocol/privacy-policy`,
  `/terms-of-service`) are **JS-rendered and could not be extracted** — the authoritative API-tier
  terms are **unverified**.
- **Training/use of inputs: leans toward "yes, by default."** MiniMax's **Open Platform Terms of
  Service permit using input and generated content to "develop and improve" its Services** — i.e.
  closer to *"inputs are used"* than *"not used."* (A "we don't train on Customer Content" line
  exists, but on a *different product / reseller* page, **not** the API tier.) **Assume API inputs
  may be retained and used unless you have a written agreement saying otherwise.**
- **Retention:** "as long as necessary or permitted by law" — **no fixed period, no GDPR section, no
  DPA / zero-retention** found on first-party pages (which are JS-rendered and could not be extracted).
- **GDPR/EU:** no EU posture, SCCs, or adequacy basis found; data is **likely processed in mainland
  China**; China has no EU adequacy decision.
- **Mitigation paths (in priority order):** (1) the payload is **minimal & non-identifying** by
  spec — *this is the primary and load-bearing defense*: even if retained/used, it leaks only a
  decision sentence + a few figures, no location/history/PII. (2) A **zero-retention routing path**
  exists for *M3 text only* — **Ollama Cloud** states *it* hosts M3 "US-based, zero data retention."
  Caveat: this **re-routes trust to Ollama; it does not prove MiniMax never sees inputs**, it covers
  **M3 only** (not M2.5, not TTS), and switching to M3 changes model/cost. (3) Request an
  **enterprise DPA + ZDR** from MiniMax (said to exist via sales; undocumented). (4) Keep it
  **opt-in** with a clear in-UI disclosure. Because the adapter is OpenAI-compatible, any of these
  (or a different, EU-hosted provider) is a config change, not a rewrite.

---

## 3. Ranked opportunities

Scored against the prompt's rubric. Verdicts: **build now / pilot / later / no**.

### #1 — `external_llm` explainer + Dutch localization — **build now**

- **What:** rephrase the **already-computed deterministic reason** + the few numbers it cites into
  natural prose (incl. the "why is it *not* acting" diagnostics), optionally in **Dutch**.
- **MiniMax:** **M2.5**, `temperature≈0.2`, over the OpenAI-compatible endpoint.
- **Value:** High — clearer explanations; Dutch is a real win for this NL household.
- **Cost:** ~$0.00042/call at the first-party M2.5 rate ⇒ ≈ $0.38/mo at 30 calls/day. With
  retries, the longer "why-*not*-acting" diagnostics, Dutch (longer output) and grounding-context
  overhead, realistically **~$0.40–4/month**. Still trivial, but not the false-precise "$0.15."
- **Fit:** Excellent — *it is* the one sanctioned off-device exception: off by default, opt-in,
  minimal redacted payload (`reason_and_facts`), never on control, template fallback on any failure,
  key is a secret, HTTP-only (Pi-friendly).
- **Grounding guard — *partial*, not a silver bullet.** The numeric guard (reject any output number
  not in the inputs) is the **safety-critical** check and works (tested below), but it is *necessary,
  not sufficient*: it won't catch unit reformulations (2.5 kWh ↔ "2500 Wh"), rounding ("about 3"),
  or — the real gap — **ungrounded *qualitative* claims** ("because prices spike tonight"). Those
  are mitigated, not eliminated, by the rephrase-only prompt + low temperature + keeping the
  deterministic reason as the source of truth and the always-available template fallback. Treat the
  LLM text as *cosmetic phrasing*, never as a new source of fact.
- **MiniMax-specificity:** Low for the rephrase itself (any LLM works) — the reasons to pick MiniMax
  are "already subscribed" + (for Dutch) its confirmed nl voices in #2.
- **Integration:** `ems/planner/explain.py` → `Explainer` protocol + `TemplateExplainer` +
  `ExternalLlmExplainer`; config per `docs/ml-layer.md` §4. **Prototyped + tested — see §4.**
- **Verdict: build now.**

### #2 — Spoken Dutch daily summary (TTS) — **pilot**

- **What:** a short morning/daily natural-language **voice** briefing ("Vandaag draait het huis op
  zon en dekt de avondpiek uit de batterij") + accessibility for low-vision users.
- **MiniMax:** `speech-2.6-turbo`, Dutch voice; text comes from the existing deterministic summary.
- **Value:** Medium-high (delight + accessibility).
- **Cost:** **< $0.01/month**.
- **Fit:** Good — cosmetic, off control path, opt-in. Payload = the (already non-identifying)
  summary text. **Privacy is *worse* than #1's text path:** TTS has **no zero-retention route** (the
  Ollama ZDR option is M3-text only), so the briefing content ships to MiniMax with no ZDR fallback.
- **MiniMax-specificity:** **High** — confirmed nl-NL TTS is the one place MiniMax clearly beats a
  generic text LLM. *Caveats:* voice naturalness unverified (listening test first); endpoints are
  **mainland-China / US-west only (no EU)**, and the "<250 ms" figure is a *dedicated-endpoint
  benchmark*, not measured NL→US/CN — fine for a non-real-time daily briefing, but don't market it
  as low-latency.
- **Effort:** Medium — a TTS adapter + browser audio playback. Note **browser autoplay policies**:
  audio can't auto-play without a user gesture, so a "plays automatically each morning" UX won't
  work — make it a tap-to-play / on-load-after-interaction control.
- **Verdict: pilot** (gate on the Dutch-voice listening test).

### #3 — Grounded conversational Q&A ("why didn't you charge last night?") — **later**

- **What:** a chat that answers homeowner questions, grounded on local deterministic reasons + plan
  objects + SQLite aggregates (retrieval / read-only tool-calls into the EMS API).
- **Value:** High if reliable. **Cost:** low.
- **Fit/Risk:** advisory only, but free-form Q&A widens the grounding surface (higher hallucination
  risk) **and** the privacy surface (more facts must leave the device to answer). Needs strong
  retrieval + the no-invented-numbers guard + aggressive redaction.
- **Verdict: later** — only after #1 proves the grounding/redaction discipline in production.

### #4 — Periodic natural-language report (weekly/monthly savings & anomalies) — **later**

- Feed **local aggregates** (redacted) → a plain-language summary + anomaly call-outs. Tiny cost,
  off control path. Medium value, medium effort. **Verdict: later.**

### #5 — Long-context "system health / drift" advisory review (M3 1M ctx) — **later / cautious**

- Ingest a long span of plan/outcome logs for an advisory "how is my system doing / is the forecast
  drifting" review. **Privacy cost is higher** (sends more history) — must use aggregates, not raw,
  and is the weakest privacy fit. **And it's the one place token cost is non-trivial:** M3 is
  $0.30/$1.20 rising to **$0.60/$2.40 above 512k tokens**, so a genuinely long review is not "free."
  Optional. **Verdict: later / cautious.**

### Recommended phased plan

Each step is independently shippable, **off by default**, and reversible (a config flip or a
provider swap — the adapter is OpenAI-compatible):

1. **Now — explainer (English).** Wire `ExternalLlmExplainer` (prototype already built, §4) behind
   `explainer.mode: external_llm`; first-party PAYG key as a secret; template fallback on any
   failure. Validate the grounding guard + redaction on a sample of *real* decisions while in
   dry-run before exposing it.
2. **Then — Dutch.** Flip `language: Dutch` once a native speaker confirms the phrasing reads
   naturally. Near-zero extra cost.
3. **Then — spoken Dutch summary (pilot).** Add the TTS adapter + a **tap-to-play** control (not
   autoplay) after a Dutch-voice listening test; state in the disclosure that audio has no
   zero-retention route.
4. **Later — conversational Q&A**, gated on step 1 proving the grounding/redaction discipline in
   production.
5. **Optional / cautious — reports & long-context review**, aggregates only, never raw history.

### Do-not-build / poor fit (so they aren't chased later)

- **LLM-as-forecaster / LLM-as-optimizer** — numeric load/solar forecasting and schedule
  optimization belong to the local statistical/ML models; LLMs forecast poorly and this would
  breach local-first for *control*. **No.**
- **Anything on the control path**, incl. voice *control* of the battery — violates "AI never
  controls" + the §8.11 validator. **No.**
- **Shipping raw meter history or location** to MiniMax for "analysis" — breaches minimal-redaction
  and the unverified privacy posture; use redacted aggregates only. **No.**
- **LLM-*generated* reasons** (vs. rephrasing the deterministic reason) — the reason must stay
  deterministic and traceable; the LLM only phrases it. **No.**
- **Video / music / image generation** — no product fit for a text energy dashboard. **No.**

---

## 4. Prototype test (the "test" iteration)

To prove the #1 recommendation is buildable **and** that the safety guards actually hold, I built a
working prototype of the `Explainer` port in `ems/planner/explain.py` and tested it — no live
MiniMax call: the OpenAI-compatible HTTP transport is **injected** (a `chat_post` callable, mirroring
`indevolt_driver.make_setdata_post`), so the adapter carries no network dependency and is fully
unit-testable with a fake.

- **`TemplateExplainer`** — returns the deterministic reason verbatim (offline default, never fails).
- **`ExternalLlmExplainer(chat_post, model, language, …)`** — builds a minimal redacted payload
  (the reason + cited facts only), calls the chat API, applies the grounding guard, and falls back
  to the template on any failure. Tagged with `source` + `base_reason` for traceability.

**Tests (`ems/tests/test_explain.py`, all green; full suite 344 passed, ruff clean):**

| Test | Proves |
|---|---|
| happy path (Dutch output reusing the input numbers) | grounded output is accepted, tagged `external_llm`, tied to `base_reason` |
| invented number (`€0.95` not in inputs) | **rejected → template fallback** (the numeric guard works) |
| transport raises (timeout) | **template fallback**, control unaffected |
| empty / malformed response shape | **template fallback** |
| payload inspection | only the reason + cited facts are sent; no `192.168…`, `52.13`, `Amsterdam`, `secret`, `token` leak; requested language present |
| guard unit test | accepts reformatted/localised numbers (`€0,30`, "72 procent"), rejects a new one (`85%`) |

**Finding:** the integration is trivial (OpenAI SDK shape) and the **safety-critical numeric guard +
fallback behave exactly as specified**. The honest limit (proven by omission): the guard is numeric
only — it does **not** catch ungrounded *qualitative* claims, which is why the LLM output stays
*cosmetic* and the deterministic reason remains the source of truth (see #1).

---

## 5. Cost model (single household, PAYG)

All at MiniMax **first-party PAYG** rates (M-series text ≈ $0.30 in / $1.20 out per 1M; TTS turbo
≈ $60/1M chars).

| Use | Model | Assumed volume | **Monthly (nominal)** | **With retries / Dutch / longer prompts** |
|---|---|---|---|---|
| Explainer | M2.5 | 30 calls/day, ~600 in + 200 out | ~$0.38 | **~$0.40–4** |
| Dutch spoken summary | speech-2.6-turbo | 1/day, ~400 chars | < $0.01 | < $0.05 |
| Conversational (if built) | M2.5 | ~10 Q&A/day, ~1k in + 300 out | ~$0.20 | < $1 |
| Long-context review (if built) | M3 | weekly, ~300k–1M tokens | $0.10–1.20 each | up to ~$5/mo |

**Total realistic footprint: a few dollars/month at most.** Cost is *not* a decision factor — but
state ranges, not the false-precise "$0.15." Use a **first-party PAYG API key** (not the consumer
subscription) for production calls.

## 6. Privacy & compliance note (required reading)

MiniMax's authoritative API privacy terms are **unverified**; assume inputs **may** be retained and
used to improve models unless you have a written DPA/ZDR. For an EU household: (a) rely on the
spec's **minimal, non-identifying** redacted payload as the primary defense — never send location,
raw meter history, tokens, or anything re-identifying; (b) keep it **opt-in, off by default**, with
a clear in-UI disclosure naming the provider; (c) consider routing via a **zero-retention gateway**
(e.g. Ollama Cloud US for M3) or obtaining an enterprise DPA+ZDR; (d) because the adapter is
OpenAI-compatible, the user can point at any compliant endpoint without code changes. Document the
chosen provider's data-handling for the user (SPEC §12).

## 7. Open questions / facts to verify

- Authoritative MiniMax **API-tier** retention/training terms (own pages were JS-rendered) — get in writing.
- Whether the owner's "subscription" is the consumer app, a Token Plan, or PAYG (affects API quota).
- Dutch TTS **voice quality** (listening test).
- JSON/structured-output mode on the OpenAI-compat endpoint (undocumented).
- Whether MiniMax-01 / M1 are still served.

## Sources

**Models / API:** [OpenAI SDK docs](https://platform.minimax.io/docs/api-reference/text-openai-api) ·
[PAYG pricing](https://platform.minimax.io/docs/guides/pricing-paygo) ·
[Rate limits](https://platform.minimax.io/docs/guides/rate-limits) ·
[M2.7](https://openrouter.ai/minimax/minimax-m2.7) · [M2.5](https://openrouter.ai/minimax/minimax-m2.5)
**Speech:** [Dutch TTS](https://www.minimax.io/audio/text-to-speech/dutch) ·
[API overview](https://platform.minimax.io/docs/api-reference/api-overview) ·
[Speech pricing](https://platform.minimax.io/docs/guides/pricing-speech) ·
[Speech-02 series](https://www.minimax.io/news/speech-02-series)
**Commercials / privacy:** [Token Plan FAQ](https://platform.minimax.io/docs/token-plan/faq) ·
[App privacy policy (19 Jan 2026)](https://agent.minimax.io/doc/en/privacy-policy.html) ·
[ToS v2 (15 Apr 2026)](https://www.minimax.io/terms-of-service-v2.html) ·
[Roo Code — endpoints](https://roocodeinc.github.io/Roo-Code/providers/minimax) ·
[Ollama — M3 US/zero-retention](https://ollama.com/library/minimax-m3) ·
[Flowith — pricing/limits](https://flowith.io/blog/minimax-api-pricing-tokens-concurrency/)
