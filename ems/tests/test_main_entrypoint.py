"""Regression: production entrypoint must not double-build the app (#234 review)."""
from __future__ import annotations

import ems.main as main_mod


def test_main_passes_built_app_to_uvicorn(monkeypatch):
    captured: dict = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(main_mod.uvicorn, "run", fake_run)
    monkeypatch.setattr(
        main_mod,
        "boot_onboarding_posture",
        lambda _db, _tok, _yaml=None: (False, False),
    )
    main_mod.main()
    assert captured["args"][0] is main_mod.app
    assert captured["kwargs"]["access_log"] is False
    assert captured["kwargs"]["proxy_headers"] is False
