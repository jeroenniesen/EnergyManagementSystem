"""Build ConsumerPort adapters from effective settings (#225 / epic #222)."""
from __future__ import annotations

import logging
from typing import Any

from ems.consumers.mock import MockConsumerSource
from ems.consumers.ports import ConsumerPort
from ems.consumers.smartthings import SmartThingsConsumerSource

_log = logging.getLogger(__name__)


def build_consumer_sources(
    settings: dict[str, Any],
    *,
    http: Any | None = None,
    dev_mode: str | None = None,
) -> list[ConsumerPort]:
    """Return wired consumer adapters. Empty when nothing is enabled/configured.

    SmartThings is added only when ``consumers.smartthings.enabled`` is on AND a
    non-empty PAT is present. Mock catalog is appended in mock/replay so Insights
    keeps a dry path without cloud credentials.
    """
    out: list[ConsumerPort] = []

    enabled = bool(settings.get("consumers.smartthings.enabled", False))
    token = str(settings.get("consumers.smartthings.pat") or "").strip()
    if enabled and token:
        http_get = None
        if http is not None:
            try:
                from ems.http_client import make_json_get_headers
                http_get = make_json_get_headers(http, "cloud")
            except Exception:
                _log.debug("consumers: HttpRuntime inject failed; using default httpx",
                           exc_info=True)
        out.append(SmartThingsConsumerSource(
            token, http_get=http_get, settings=settings,
        ))
    elif enabled and not token:
        _log.info(
            "consumers.smartthings.enabled is on but no PAT is configured — "
            "SmartThings adapter not started"
        )

    if dev_mode in ("mock", "replay"):
        out.append(MockConsumerSource(settings=settings))

    return out
