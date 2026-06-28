"""Build the "what will the algorithm do next 24h" detail (SPEC §9.1).

Joins the plan, prices and solar forecast onto ONE shared timeline (the plan's own 15-min slots,
starting at the current slot) so the dashboard can render them aligned — the cheap price windows
line up exactly with the charge actions. Pure + unit-tested.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ems.domain import BatteryIntent
from ems.planner.projection import SLOT_HOURS, ProjectedSlot
from ems.planner.schedule import Plan
from ems.savings import estimate_daily_savings_eur
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot

_INTENT_LABEL = {
    BatteryIntent.ALLOW_SELF_CONSUMPTION: "self-consume",
    BatteryIntent.GRID_CHARGE_TO_TARGET: "charge",
    BatteryIntent.HOLD_RESERVE: "hold",
    BatteryIntent.DISCHARGE_FOR_LOAD: "discharge",
}


def _summary(slots, price_by: dict[datetime, float]) -> str:
    """A one-line plain-English summary of the next-24h plan."""
    if not slots:
        return "No plan yet."
    counts = Counter(s.intent for s in slots)
    charge = [price_by[s.start] for s in slots
              if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET and s.start in price_by]
    discharge = [price_by[s.start] for s in slots
                 if s.intent is BatteryIntent.DISCHARGE_FOR_LOAD and s.start in price_by]
    parts: list[str] = []
    if charge:
        parts.append(f"charge {len(charge)}×15m at ≤€{max(charge):.2f}/kWh")
    if discharge:
        parts.append(f"discharge {len(discharge)}×15m at ≥€{min(discharge):.2f}/kWh")
    if counts.get(BatteryIntent.HOLD_RESERVE):
        parts.append(f"hold {counts[BatteryIntent.HOLD_RESERVE]}×15m")
    sc = counts.get(BatteryIntent.ALLOW_SELF_CONSUMPTION, 0)
    if sc:
        parts.append(f"self-consume {sc}×15m")
    return "Next 24h — " + ", ".join(parts) + "." if parts else "Next 24h — self-consumption."


def plan_metrics(plan: Plan, prices: list[PriceSlot]) -> dict:
    """Headline metrics for a plan — used to show the IMPACT of a settings change (before/after)."""
    price_by = {p.start: p.eur_per_kwh for p in prices}
    counts = Counter(s.intent for s in plan.slots)
    return {
        "summary": _summary(plan.slots, price_by),
        "savings_eur": round(estimate_daily_savings_eur(plan, price_by), 2),
        "charge_slots": counts.get(BatteryIntent.GRID_CHARGE_TO_TARGET, 0),
        "discharge_slots": counts.get(BatteryIntent.DISCHARGE_FOR_LOAD, 0),
        "hold_slots": counts.get(BatteryIntent.HOLD_RESERVE, 0),
        "self_consume_slots": counts.get(BatteryIntent.ALLOW_SELF_CONSUMPTION, 0),
    }


def summarize_projection(projected: list[ProjectedSlot]) -> dict:
    """Headline numbers + a plain-English narrative of the projected next-24h energy behaviour.
    Clock times are left to the UI (the timestamps are returned); the text stays tz-agnostic.
    `*_kwh` integrate power over the 15-min slots (energy = W × 0.25 h ÷ 1000)."""
    if not projected:
        return {"summary": "No projection yet.", "soc_end_pct": None, "soc_min_pct": None,
                "soc_max_pct": None, "soc_min_at": None, "soc_max_at": None,
                "import_kwh": 0.0, "export_kwh": 0.0, "solar_kwh": 0.0, "load_kwh": 0.0}
    lo = min(projected, key=lambda p: p.soc_pct)
    hi = max(projected, key=lambda p: p.soc_pct)
    end = projected[-1].soc_pct
    imp = sum(p.grid_w for p in projected if p.grid_w > 0) * SLOT_HOURS / 1000.0
    exp = sum(-p.grid_w for p in projected if p.grid_w < 0) * SLOT_HOURS / 1000.0
    solar = sum(p.solar_w for p in projected) * SLOT_HOURS / 1000.0
    load = sum(p.load_w for p in projected) * SLOT_HOURS / 1000.0
    # Honest, shape-agnostic phrasing: report peak / end / lowest as facts (the "lowest" is often
    # just the starting slot, so never imply a mid-window "dip" that doesn't happen). "Planned
    # window" not "24h" — the horizon is only as long as prices are published (≈11h until tomorrow).
    summary = (
        f"Projected SoC peaks at {round(hi.soc_pct)}% and ends the planned window near "
        f"{round(end)}% (lowest {round(lo.soc_pct)}%). Projected grid: {imp:.1f} kWh in / "
        f"{exp:.1f} kWh out, on {solar:.1f} kWh solar and {load:.1f} kWh of load."
    )
    return {
        "summary": summary,
        "soc_end_pct": round(end, 1),
        "soc_min_pct": round(lo.soc_pct, 1), "soc_min_at": lo.start.isoformat(),
        "soc_max_pct": round(hi.soc_pct, 1), "soc_max_at": hi.start.isoformat(),
        "import_kwh": round(imp, 2), "export_kwh": round(exp, 2),
        "solar_kwh": round(solar, 2), "load_kwh": round(load, 2),
    }


# ---------------------------------------------------------------------------------------------
# Explainer port (SPEC §8.6 / docs/ml-layer.md §7) — M6c prototype.
#
# The deterministic reason is ALWAYS computed elsewhere (the planner / mode_controller). An
# Explainer only *rephrases* it into natural prose; it may never invent a number or touch control.
# `TemplateExplainer` (offline, default) returns the reason verbatim. `ExternalLlmExplainer` sends a
# MINIMAL REDACTED payload (the reason + the few cited facts — never raw history, location, or
# secrets) to an OpenAI-compatible chat API (e.g. MiniMax), with a grounding guard that rejects any
# output containing a number not present in the inputs, and falls back to the template on ANY
# failure. The HTTP transport is INJECTED (a `chat_post` callable) so the adapter carries no network
# dependency and is fully unit-testable with a fake — mirroring `indevolt_driver.make_setdata_post`.
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Explanation:
    """A phrased explanation, tagged with its source and the deterministic reason it came from
    (traceability requirement, docs/ml-layer.md §7)."""

    text: str
    source: str       # "template" | "external_llm" | "local_llm"
    base_reason: str  # the deterministic reason this was derived from


class Explainer(Protocol):
    def explain(self, reason: str, facts: dict | None = None) -> Explanation: ...


class TemplateExplainer:
    """Offline default: the deterministic reason, verbatim. Always available, never fails."""

    def explain(self, reason: str, facts: dict | None = None) -> Explanation:
        return Explanation(text=reason, source="template", base_reason=reason)


# An OpenAI-compatible chat transport: (messages, params) -> response dict in OpenAI shape
# ({"choices": [{"message": {"content": "..."}}]}). Injected so no httpx import lives here.
ChatPost = Callable[[list[dict], dict], dict]

_NUM_RE = re.compile(r"-?\d[\d.,]*")


def _number_candidates(token: str) -> set[str]:
    """The plausible normalised forms of one numeric token, so 0.30 / 0,30 / €0.30 can match. Comma
    is tried as both a decimal point and a thousands separator; each variant is parsed to a
    canonical float string where possible (else kept as the cleaned token)."""
    cleaned = token.strip().strip(".,")
    cands: set[str] = set()
    for variant in {cleaned, cleaned.replace(",", "."), cleaned.replace(",", "")}:
        try:
            cands.add(f"{float(variant):g}")
        except ValueError:
            if variant:
                cands.add(variant)
    return cands


def _allowed_numbers(source: str) -> set[str]:
    """Every normalised numeric form present in the grounding source (reason + cited facts)."""
    allowed: set[str] = set()
    for tok in _NUM_RE.findall(source):
        allowed |= _number_candidates(tok)
    return allowed


def _has_ungrounded_number(text: str, allowed_source: str) -> bool:
    """True if `text` contains a number whose every interpretation is absent from `allowed_source`.
    A token is grounded if ANY of its candidate forms is allowed (so reformatted/localised numbers
    pass); only a token with no matching candidate is ungrounded. Conservative by design — an
    unmatched number → reject and fall back to the template, never accept an invented figure. (The
    numeric guard is the safety-critical check; qualitative drift is mitigated by the rephrase-only
    prompt + low temperature.)"""
    allowed = _allowed_numbers(allowed_source)
    for tok in _NUM_RE.findall(text):
        cands = _number_candidates(tok)
        if cands and not (cands & allowed):
            return True
    return False


class ExternalLlmExplainer:
    """Rephrase the deterministic reason via an OpenAI-compatible chat API (e.g. MiniMax). The
    bounded, opt-in, off-device exception (SPEC §12): minimal redacted payload, grounded,
    template fallback on any failure."""

    def __init__(
        self,
        chat_post: ChatPost,
        *,
        model: str,
        language: str = "English",
        max_tokens: int = 200,
        temperature: float = 0.2,
    ) -> None:
        self._chat_post = chat_post
        self._model = model
        self._language = language
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._fallback = TemplateExplainer()

    def _messages(self, reason: str, facts: dict) -> list[dict]:
        # The ONLY dynamic content sent off-device: the deterministic reason + the cited facts.
        # Never raw history, location, tokens, or secrets — the caller passes a minimal facts dict.
        facts_line = "; ".join(f"{k}={v}" for k, v in facts.items()) if facts else "(none)"
        system = (
            f"You rephrase a home-battery system's decision into one clear sentence in "
            f"{self._language}. Use ONLY the facts given. Do NOT introduce any number, price, "
            f"percentage, time, or claim that is not in the input. Do not give advice."
        )
        user = f"Decision: {reason}\nFacts: {facts_line}\nRephrase in {self._language}:"
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def explain(self, reason: str, facts: dict | None = None) -> Explanation:
        facts = facts or {}
        try:
            resp = self._chat_post(
                self._messages(reason, facts),
                {"model": self._model, "max_tokens": self._max_tokens,
                 "temperature": self._temperature},
            )
            text = (resp["choices"][0]["message"]["content"] or "").strip()
        except Exception:
            return self._fallback.explain(reason)  # network error / timeout / bad shape → template
        allowed_source = reason + " " + " ".join(str(v) for v in facts.values())
        if not text or _has_ungrounded_number(text, allowed_source):
            return self._fallback.explain(reason)  # empty or invented a number → template
        return Explanation(text=text, source="external_llm", base_reason=reason)

    def chat(self, question: str, context: str) -> Explanation:
        """Answer a user question grounded ONLY in `context` (a redacted snapshot of the plan +
        dashboard). Same numeric guard + graceful fallbacks as explain(): an answer that invents a
        number not in the context is replaced with a safe "I don't have that" message. `source` is
        external_llm (answered) | guard (rejected) | error (LLM unreachable)."""
        system = (
            "You are the assistant inside a home-battery energy manager. Answer the user's "
            "question using ONLY the CONTEXT below. If the context does not contain the answer, "
            "say you don't have that information — do not guess. Never state a number that is not "
            f"in the context. Be concise (2-3 sentences). Answer in {self._language}."
        )
        user = f"CONTEXT:\n{context}\n\nQUESTION: {question}"
        try:
            resp = self._chat_post(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                {"model": self._model, "max_tokens": self._max_tokens,
                 "temperature": self._temperature},
            )
            text = (resp["choices"][0]["message"]["content"] or "").strip()
        except Exception:
            return Explanation(
                "Sorry — the assistant isn't reachable right now.", "error", question
            )
        if not text:
            return Explanation("Sorry — I couldn't produce an answer.", "error", question)
        if _has_ungrounded_number(text, context + " " + question):
            return Explanation(
                "I can only answer from the current plan and dashboard, and I don't have that "
                "exact figure.", "guard", question,
            )
        return Explanation(text, "external_llm", question)


def make_openai_chat_post(base_url: str, api_key: str, *, timeout: float = 8.0) -> ChatPost:
    """Build a `ChatPost` transport for any OpenAI-compatible chat endpoint (e.g. MiniMax). httpx is
    imported lazily inside the call so the core/Pi path carries no hard network dependency (the same
    pattern the live device/price sources use)."""
    url = base_url.rstrip("/") + "/chat/completions"

    def chat_post(messages: list[dict], params: dict) -> dict:
        import httpx

        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"messages": messages, **params},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()

    return chat_post


def build_plan_detail(
    now: datetime,
    prices: list[PriceSlot],
    plan: Plan,
    forecast_slots: list[ForecastSlot] | None,
    horizon: int = 96,
) -> dict:
    """Per-slot {start, intent, reason, eur_per_kwh, solar_w} on the plan's timeline + a summary."""
    price_by = {p.start: p.eur_per_kwh for p in prices}
    fc_by = {f.start: f.p50_w for f in (forecast_slots or [])}
    window = plan.slots[:horizon]
    cur = plan.intent_at(now)
    return {
        "current_intent": cur.intent if cur else None,
        "summary": _summary(window, price_by),
        "slots": [
            {
                "start": s.start.isoformat(),
                "intent": s.intent,
                "label": _INTENT_LABEL.get(s.intent, str(s.intent)),
                "reason": s.reason,
                "eur_per_kwh": price_by.get(s.start),
                "solar_w": fc_by.get(s.start),
            }
            for s in window
        ],
    }
