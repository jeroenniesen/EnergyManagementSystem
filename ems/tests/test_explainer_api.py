"""Iteration 1: the AI explainer wired into /api/decision + /api/explainer status.

Off by default (template → the deterministic reason verbatim). When `explainer.mode=external_llm`
AND a key are set, the reason is rephrased via an OpenAI-compatible transport — faked here, so no
network. The phrasing is cached per reason, so polling does not re-hit the LLM."""
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

import ems.web.api as api
from ems.control.mode_controller import ModeController
from ems.domain import RawSample
from ems.lifecycle import Lifecycle
from ems.planner.schedule import SLOT
from ems.sources.battery import MockBatteryDriver
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.prices import PriceSlot
from ems.storage.cache import CacheStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")


class _Source:
    def read(self) -> RawSample:
        return RawSample(grid_power_w=0.0, solar_power_w=0.0, battery_power_w=0.0,
                         ev_power_w=0.0, soc_pct=55.0)


class _FlatPrices:
    """Flat prices → winter finds no trade → the plan is plain self-consumption (stable reason)."""

    def __init__(self) -> None:
        now = datetime.now(UTC)
        base = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
        self._slots = [PriceSlot(base + i * SLOT, 0.25) for i in range(-2, 96)]

    def slots(self) -> list[PriceSlot]:
        return self._slots


def _app(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    controller = ModeController(MockBatteryDriver(), Lifecycle(dry_run=True), dry_run=True)
    return create_app(
        _Source(), dry_run=True, dev_mode="mock", tz=AMS,
        price_source=_FlatPrices(), solar_forecast=MockSolarForecastSource(AMS),
        controller=controller, settings_store=SettingsStore(db), cache_store=CacheStore(db),
    )


def test_explainer_off_by_default_returns_verbatim(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/settings", json={"strategy.mode": "winter"})
        st = c.get("/api/explainer").json()
        assert st["mode"] == "template" and st["active"] is False
        d = c.get("/api/decision").json()
        assert d["explanation_source"] == "template"
        assert d["plan_reason_explained"] == d["plan_reason"]  # verbatim, no AI


def test_external_llm_needs_a_key_to_activate(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/settings", json={"explainer.mode": "external_llm"})  # no key
        assert c.get("/api/explainer").json()["active"] is False


def test_external_llm_rephrases_and_caches(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_factory(base_url, api_key, *, timeout=8.0):
        def chat_post(messages, params):
            calls["n"] += 1
            return {"choices": [{"message": {"content": "Running your home on the battery now."}}]}
        return chat_post

    monkeypatch.setattr(api, "make_openai_chat_post", fake_factory)
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/settings", json={
            "strategy.mode": "winter", "explainer.mode": "external_llm",
            "explainer.api_key": "test-key",
        })
        assert c.get("/api/explainer").json()["active"] is True
        d = c.get("/api/decision").json()
        assert d["explanation_source"] == "external_llm"
        assert d["plan_reason_explained"] == "Running your home on the battery now."
        assert d["plan_reason"]  # the deterministic reason is still present alongside
        c.get("/api/decision").json()  # second poll, same reason
        assert calls["n"] == 1  # cached — polling never re-hits the LLM


def test_explanation_cache_survives_restart(tmp_path, monkeypatch):
    """The persistent cache means a restart doesn't re-spend tokens re-explaining the same decision:
    a fresh app on the same DB serves the stored phrasing without a second LLM call."""
    calls = {"n": 0}

    def fake_factory(base_url, api_key, *, timeout=8.0):
        def chat_post(messages, params):
            calls["n"] += 1
            return {"choices": [{"message": {"content": "Running your home on the battery now."}}]}
        return chat_post

    monkeypatch.setattr(api, "make_openai_chat_post", fake_factory)
    cfg = {"strategy.mode": "winter", "explainer.mode": "external_llm", "explainer.api_key": "k"}
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/settings", json=cfg)
        assert c.get("/api/decision").json()["explanation_source"] == "external_llm"
    assert calls["n"] == 1
    # "restart": a brand-new app on the SAME db (fresh in-memory cache, settings reloaded from
    # store). The persistent cache must serve the explanation — no second LLM call.
    with TestClient(_app(tmp_path)) as c:
        d = c.get("/api/decision").json()
        assert d["explanation_source"] == "external_llm"
        assert d["plan_reason_explained"] == "Running your home on the battery now."
    assert calls["n"] == 1  # NOT re-spent across the restart


def test_decision_gates_on_plan_validation_and_holds_self_consumption(tmp_path):
    """§8.11 integration: with no fresh sensor feed (this harness has no freshness tracker), data
    quality is unsafe, so the hard validator blocks and the effective decision holds
    self-consumption — and /api/decision surfaces the verdict."""
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/settings", json={"strategy.mode": "winter"})
        d = c.get("/api/decision").json()
        assert d["plan_validation"]["status"] == "unsafe"
        assert d["plan_validation"]["ok"] is False
        assert d["intent"] == "allow_self_consumption"
        assert "holding self-consumption" in d["plan_reason"]
        # /api/plan carries the same verdict + the energy contract fields per slot.
        plan = c.get("/api/plan").json()
        assert plan["validation"]["status"] == "unsafe"
        assert all("target_soc" in s and "power_w" in s for s in plan["slots"])


def test_meter_data_never_enters_persistent_cache(tmp_path, monkeypatch):
    """Guardrail (CLAUDE.md: meter data must always be actual): only explanations / prices /
    forecast may be persisted. Polling /api/decision both reads the live meters (for SoC + the car
    guard) AND caches the explanation — afterwards the cache must hold ONLY allowed key prefixes,
    never any meter/SoC sample."""
    import sqlite3

    _enable_ai(monkeypatch)
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/settings", json={
            "strategy.mode": "winter", "explainer.mode": "external_llm", "explainer.api_key": "k",
        })
        for _ in range(3):
            c.get("/api/decision")  # reads live meters via _current_sample, caches the explanation
    con = sqlite3.connect(str(tmp_path / "ems.sqlite"))
    keys = [r[0] for r in con.execute("SELECT key FROM cache").fetchall()]
    con.close()
    assert keys, "the explanation should have been persisted"
    allowed = ("explain:", "tibber:", "forecast_solar:")
    assert all(any(k.startswith(p) for p in allowed) for k in keys), keys
    # belt-and-braces: nothing meter-shaped ever lands in the persistent cache
    assert not any(t in k.lower() for k in keys for t in ("soc", "meter", "sample", "grid", "p1"))


# ---- chat -------------------------------------------------------------------------------------

def _enable_ai(monkeypatch, capture=None, answer="Your home is running on the battery now."):
    def fake_factory(base_url, api_key, *, timeout=8.0):
        def chat_post(messages, params):
            if capture is not None:
                capture["msgs"] = messages
            return {"choices": [{"message": {"content": answer}}]}
        return chat_post
    monkeypatch.setattr(api, "make_openai_chat_post", fake_factory)


def test_chat_off_by_default(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        b = c.post("/api/chat", json={"question": "why?"}).json()
        assert b["source"] == "disabled" and "Settings" in b["answer"]


def test_faq_answers_deterministically_without_ai(tmp_path):
    # Grounded FAQ must work even with AI off (emotional review #8): 'Is my battery safe?' always
    # has an answer, built from readiness/plan — not the LLM.
    with TestClient(_app(tmp_path)) as c:
        b = c.get("/api/faq").json()
    assert b["ai_on"] is False
    keys = {i["key"] for i in b["items"]}
    assert "battery_safe" in keys
    safe = next(i for i in b["items"] if i["key"] == "battery_safe")
    assert safe["answer"] and isinstance(safe["answer"], str)


def test_chat_rejects_empty_question(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        assert c.post("/api/chat", json={"question": "   "}).status_code == 400


def test_chat_answers_grounded_when_enabled(tmp_path, monkeypatch):
    _enable_ai(monkeypatch)
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/settings", json={
            "strategy.mode": "winter", "explainer.mode": "external_llm", "explainer.api_key": "k",
        })
        b = c.post("/api/chat", json={"question": "Why isn't the battery charging?"}).json()
        assert b["source"] == "external_llm"
        assert "battery" in b["answer"].lower()


def test_chat_context_is_redacted(tmp_path, monkeypatch):
    capture: dict = {}
    _enable_ai(monkeypatch, capture=capture, answer="All good.")
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/settings", json={
            "strategy.mode": "winter", "explainer.mode": "external_llm",
            "explainer.api_key": "secret-key-123",
        })
        c.post("/api/chat", json={"question": "what's my status?"})
    blob = " ".join(m["content"] for m in capture["msgs"])
    assert "winter" in blob  # real plan facts ARE grounded on...
    for forbidden in ("192.168", "secret-key-123", "52.13", "5.29"):  # ...but nothing identifying
        assert forbidden not in blob


# ---- scheduled AI second-opinion (validation) -------------------------------------------------

def test_ai_validation_off_by_default(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        b = c.get("/api/ai/validation").json()
        assert b["latest"] is None and b["active"] is False
        assert c.post("/api/ai/validate").json()["latest"] is None  # no-op when off


def test_ai_validation_runs_and_surfaces_when_enabled(tmp_path, monkeypatch):
    _enable_ai(monkeypatch, answer="The plan looks reasonable: it charges in the cheap window and "
               "covers the evening peak from the battery.")
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/settings", json={
            "strategy.mode": "winter", "explainer.mode": "external_llm", "explainer.api_key": "k",
        })
        r = c.post("/api/ai/validate").json()
        assert r["active"] is True and r["latest"] and "reasonable" in r["latest"]["text"]
        # the latest is surfaced read-only for the dashboard
        assert c.get("/api/ai/validation").json()["latest"]["text"] == r["latest"]["text"]
