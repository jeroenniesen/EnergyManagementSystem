"""#134 / PR #146 B2 — /api/charge-need returns 204 when SoC is unknown."""
from __future__ import annotations

from fastapi.testclient import TestClient

from ems.sources.live import HomeWizardMeter, LiveSource
from ems.sources.mock import MockSource
from ems.storage.settings import SettingsStore
from ems.web.api import create_app


class _FailingBattery:
    def read_power_soc(self):
        raise TimeoutError("Indevolt unreachable")


def _app(tmp_path, source):
    return create_app(
        source, dry_run=True, dev_mode="mock",
        settings_store=SettingsStore(str(tmp_path / "ems.sqlite")),
    )


def test_charge_need_unknown_soc_returns_204(tmp_path):
    """LiveSource seeds soc=0.0 but never marks soc fresh → EMS must not invent a charge card."""
    src = LiveSource(
        p1=HomeWizardMeter("x", http_get=lambda _u: {"active_power_w": 0}),
        battery=_FailingBattery(),
    )
    with TestClient(_app(tmp_path, src)) as c:
        r = c.get("/api/charge-need")
    assert r.status_code == 204
    assert r.content == b""


def test_charge_need_known_soc_still_returns_payload(tmp_path):
    with TestClient(_app(tmp_path, MockSource())) as c:
        r = c.get("/api/charge-need")
    assert r.status_code == 200
    body = r.json()
    assert body["current_soc_pct"] == 55.0
    assert isinstance(body["deficit_kwh"], float)
    assert isinstance(body["on_track"], bool)
