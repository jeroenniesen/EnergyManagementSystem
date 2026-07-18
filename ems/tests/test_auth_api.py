from fastapi.testclient import TestClient

from ems.sources.mock import MockSource
from ems.storage.auth import AuthStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app


def _app(db: str, *, token: str | None = None):
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock",
        settings_store=SettingsStore(db),
        auth_store=AuthStore(db),
        web_auth_token=token,
    )


def test_app_boots_with_auth_store_and_users_exist_flag(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    app = _app(db)
    with TestClient(app) as c:
        assert app.state.users_exist is False  # fresh DB → no users
        assert c.get("/api/auth").status_code == 200
