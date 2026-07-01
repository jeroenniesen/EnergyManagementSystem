"""Current cloud cover for the sky backdrop, from Open-Meteo (keyless, server-side — the same shape
as the Forecast.Solar call, so no new frontend network access and nothing sensitive leaves the home
beyond the site coordinates). Best-effort: any failure returns None and the sky just shows clear.
`http_get` is injectable so tests never touch the network.
"""
from __future__ import annotations

from collections.abc import Callable

_URL = "https://api.open-meteo.com/v1/forecast"

# (lat, lon, timeout) -> parsed JSON dict.
CloudGet = Callable[[float, float, float], dict]


def _default_get(lat: float, lon: float, timeout: float) -> dict:
    import httpx

    r = httpx.get(
        _URL, params={"latitude": lat, "longitude": lon, "current": "cloud_cover"}, timeout=timeout
    )
    r.raise_for_status()
    return r.json()


def cloud_cover_pct(
    lat: float, lon: float, *, http_get: CloudGet | None = None, timeout: float = 2.5
) -> float | None:
    """Current cloud cover 0–100 % at lat/lon, or None if unavailable (offline/timeout/bad data)."""
    try:
        data = (http_get or _default_get)(lat, lon, timeout)
        cc = data.get("current", {}).get("cloud_cover")
        if cc is None:
            return None
        return max(0.0, min(100.0, float(cc)))
    except Exception:
        return None
