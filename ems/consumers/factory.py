"""Build ConsumerPort adapters from effective settings (#225/#226 / epic #222)."""
from __future__ import annotations

import logging
from typing import Any

from ems.consumers.homeassistant import HomeAssistantConsumerSource
from ems.consumers.mock import MockConsumerSource
from ems.consumers.ports import ConsumerPort
from ems.consumers.smartthings import SmartThingsConsumerSource
from ems.ha_url import normalize_ha_url

_log = logging.getLogger(__name__)


def _http_get(http: Any | None, profile: str):
    if http is None:
        return None
    try:
        from ems.http_client import make_json_get_headers
        return make_json_get_headers(http, profile)
    except Exception:
        _log.debug(
            "consumers: HttpRuntime inject failed (profile=%s); using default httpx",
            profile,
            exc_info=True,
        )
        return None


def build_consumer_sources(
    settings: dict[str, Any],
    *,
    http: Any | None = None,
    dev_mode: str | None = None,
) -> list[ConsumerPort]:
    """Return wired consumer adapters. Empty when nothing is enabled/configured.

    SmartThings uses the ``cloud`` HTTP profile; Home Assistant uses ``lan_read``
    (typical LAN install — #266). Mock catalog is appended in mock/replay.
    """
    out: list[ConsumerPort] = []

    enabled = bool(settings.get("consumers.smartthings.enabled", False))
    token = str(settings.get("consumers.smartthings.pat") or "").strip()
    if enabled and token:
        out.append(SmartThingsConsumerSource(
            token, http_get=_http_get(http, "cloud"), settings=settings,
        ))
    elif enabled and not token:
        _log.info(
            "consumers.smartthings.enabled is on but no PAT is configured — "
            "SmartThings adapter not started"
        )

    ha_enabled = bool(settings.get("consumers.ha.enabled", False))
    ha_url = str(settings.get("consumers.ha.url") or "").strip()
    ha_token = str(settings.get("consumers.ha.token") or "").strip()
    if ha_enabled and ha_url and ha_token:
        try:
            ha_base = normalize_ha_url(ha_url)
        except ValueError:
            _log.info(
                "consumers.ha.enabled is on but consumers.ha.url is invalid — "
                "Home Assistant adapter not started"
            )
        else:
            out.append(HomeAssistantConsumerSource(
                ha_base,
                ha_token,
                http_get=_http_get(http, "lan_read"),
                settings=settings,
            ))
    elif ha_enabled:
        _log.info(
            "consumers.ha.enabled is on but URL/token incomplete — "
            "Home Assistant adapter not started"
        )

    if dev_mode in ("mock", "replay"):
        out.append(MockConsumerSource(settings=settings))

    return out
