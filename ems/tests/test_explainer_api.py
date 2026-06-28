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
        controller=controller, settings_store=SettingsStore(db),
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
