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
    assert "selected" in body["summary"].lower()


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

    # gather_consumers re-applies apply_opt_in from settings — pre-marked opted_in alone
    # is not enough; the device must be in the settings selection list.
    body = gather_consumers(
        [_Silent()],  # type: ignore[list-item]
        {MOCK_DEVICE_IDS_KEY: '["x"]'},
    )
    assert len(body["devices"]) == 1
    assert body["devices"][0]["status"]["state"] == "offline"
    label = body["devices"][0]["status"]["state_label"].lower()
    assert "can’t reach" in label or "cant reach" in label
    assert body["devices"][0]["status"]["detail"] == (
        "No status from the integration right now."
    )


def test_gather_consumers_ignores_self_marked_opt_in_without_settings():
    """Adapter that sets opted_in=True must not bypass the settings selection list."""
    class _SelfMarked:
        integration = "mock"

        def list_devices(self):
            from ems.consumers.models import ConsumerCapabilities, ConsumerDevice, ConsumerKind
            return [
                ConsumerDevice(
                    id="sneaky", name="Sneaky", kind=ConsumerKind.OTHER, integration="mock",
                    capabilities=ConsumerCapabilities(), opted_in=True,
                ),
            ]

        def get_status(self, device_id: str):
            return None

        def set_settings(self, settings):
            return None

    body = gather_consumers([_SelfMarked()], {})  # type: ignore[list-item]
    assert body["devices"] == []
    assert "selected" in body["summary"].lower()


def test_consumers_path_has_no_write_methods():
    src = MockConsumerSource()
    assert not hasattr(src, "apply")
    assert not hasattr(src, "start")


def test_consumers_live_defaults_to_empty_sources(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    app = create_app(
        MockSource(),
        dry_run=True,
        config_dry_run=False,
        dev_mode="live",
        store=HistoryStore(db),
        settings_store=SettingsStore(db),
    )
    with TestClient(app) as c:
        body = c.get("/api/consumers").json()
    assert body["devices"] == []
    assert "connected" in body["summary"].lower()


def test_gather_list_failure_honest_summary():
    class _Boom:
        integration = "mock"

        def list_devices(self):
            raise RuntimeError("cloud down")

        def get_status(self, device_id: str):
            return None

        def set_settings(self, settings):
            return None

    body = gather_consumers([_Boom()], {})  # type: ignore[list-item]
    assert body["devices"] == []
    summary = body["summary"].lower()
    assert "can’t reach" in summary or "cant reach" in summary
    assert "appliance integration" in summary
