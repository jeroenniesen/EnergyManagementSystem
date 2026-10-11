"""#224 — GET /api/consumers read-only insight contract."""
from fastapi.testclient import TestClient

from ems.consumers.mock import MockConsumerSource
from ems.consumers.selection import MOCK_DEVICE_IDS_KEY
from ems.sources.mock import MockSource
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app
from ems.web.routes.consumers import gather_consumers


def _app(tmp_path, *, sources=None, dry_run=True):
    db = str(tmp_path / "ems.sqlite")
    kwargs = {}
    if sources is not None:
        kwargs["consumer_sources"] = sources
    return create_app(
        MockSource(),
        dry_run=dry_run,
        config_dry_run=False,
        dev_mode="mock",
        store=HistoryStore(db),
        settings_store=SettingsStore(db),
        **kwargs,
    )


def test_consumers_empty_when_nothing_opted_in(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.get("/api/consumers")
        assert r.status_code == 200
        body = r.json()
    assert body["read_only"] is True
    assert body["title"] == "Appliances"
    assert body["devices"] == []
    assert "selected" in body["summary"].lower() or "empty" in body["summary"].lower()


def test_consumers_lists_opted_in_mock_devices(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        assert c.post(
            "/api/settings",
            json={MOCK_DEVICE_IDS_KEY: '["mock-washer-1"]'},
        ).status_code == 200
        body = c.get("/api/consumers").json()
    assert len(body["devices"]) == 1
    d = body["devices"][0]
    assert d["id"] == "mock-washer-1"
    assert d["name"] == "Mock washer"
    assert d["kind"] == "washer"
    assert d["opted_in"] is True
    assert d["status"]["state"] == "idle"
    assert "Idle" in d["status"]["state_label"]
    assert d["status"]["power_w"] == 0.0
    # Tracebacks must never appear in homeowner copy.
    assert "Traceback" not in body["summary"]
    assert "Traceback" not in (d["status"].get("detail") or "")


def test_consumers_no_sources_honest_summary(tmp_path):
    with TestClient(_app(tmp_path, sources=[])) as c:
        body = c.get("/api/consumers").json()
    assert body["devices"] == []
    assert "connected" in body["summary"].lower()


def test_gather_consumers_offline_when_status_missing():
    class _Silent:
        integration = "mock"

        def list_devices(self):
            from ems.consumers.models import ConsumerCapabilities, ConsumerDevice, ConsumerKind
            return [
                ConsumerDevice(
                    id="x", name="X", kind=ConsumerKind.OTHER, integration="mock",
                    capabilities=ConsumerCapabilities(), opted_in=True,
                ),
            ]

        def get_status(self, device_id: str):
            return None

        def set_settings(self, settings):
            return None

    # Bypass opt-in filter by pre-marking opted_in; settings unused for filter if already set.
    # filter_opted_in requires opted_in True — good.
    body = gather_consumers([_Silent()], {})  # type: ignore[list-item]
    assert len(body["devices"]) == 1
    assert body["devices"][0]["status"]["state"] == "offline"
    assert "can’t reach" in body["devices"][0]["status"]["state_label"].lower() or \
        "cant reach" in body["devices"][0]["status"]["state_label"].lower() or \
        "reach" in body["devices"][0]["status"]["state_label"].lower()


def test_consumers_path_has_no_write_methods():
    src = MockConsumerSource()
    assert not hasattr(src, "apply")
    assert not hasattr(src, "start")
