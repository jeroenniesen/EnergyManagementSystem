"""Home Assistant REST API client — READ-ONLY (SPEC §5/§9.2, BACKLOG B-18).

**B-18 status: this is a skeleton, NOT wired into the control loop or `ems/connection.py`.** Today
the EMS reads HomeWizard/Tibber/Forecast.Solar and the Indevolt battery **directly**
(`ems/sources/live.py`, `tibber.py`, `forecast_solar.py`, `indevolt*.py`) — Home Assistant is not
required to run it (SPEC §5.2 "Implemented reality", `docs/api-reference.md`'s HA section). The
HA-mediated architecture (read via HA, `entity_map`, MQTT discovery) remains the SPEC §5/§9.2
**target** for the Raspberry Pi deployment. This module builds the tested read client that target
needs, so later work (the autumn heating integration, and eventually B-18 itself) doesn't have to
invent config/parsing/staleness conventions from scratch. Wiring it into `sense`/`connection.py`
(config loading, the §11.5 startup entity validation, feeding `RawSample`) is future work.

**READ-ONLY BY DESIGN — there is no write path here, and none is planned in this module.**
CLAUDE.md's one-battery-writer rule means `ems/sources/battery.py` (via `indevolt_driver.py`)
remains the ONLY thing that ever writes to the battery. HA's `indevolt.charge`/`indevolt.discharge`
**service calls** and any future "command the battery via HA" support are explicitly out of scope
here — that would be a *write* client, a different, separately-gated module. When it is eventually
built, BACKLOG B-26 reconcile applies (SPEC and code updated together, not left to drift).

Home Assistant REST API (https://developers.home-assistant.io/docs/api/rest/):

    GET /api/states/<entity_id>
    Authorization: Bearer <long-lived access token>
    -> 200 {"entity_id": ..., "state": "23.5", "attributes": {...}, "last_updated": "<ISO8601>"}
    -> 401 unauthorized (bad/expired token)
    -> 404 no such entity_id

There is no bulk "get several states" endpoint (`GET /api/states` returns EVERY entity in the
system, not a filtered subset), so `get_states()` just calls `get_state()` once per id, in order —
deliberately simple. `read_snapshot()` is the fail-safe variant for callers that want one bad
entity to degrade that role only, rather than abort the whole read (mirrors the `attempt()` pattern
in `ems/sources/live.py`).

Config shape (documented here, NOT yet added to `config.yaml` or `ems/config.py` — the shipped
config has no `homeassistant:` block and `ems/config.py` is deliberately minimal per SPEC §9's
"Implemented reality" callout, and there is no existing commented-examples section to extend; see
SPEC §9 for the full target sample this mirrors):

    homeassistant:
      base_url: http://homeassistant.local:8123
      token: !secret ha_long_lived_token   # via !secret/env, never committed
      entity_map:                          # role -> entity id (don't rely on discovery names)
        grid_power: sensor.p1_meter_active_power
        solar_power: sensor.solar_kwh_meter_active_power
        ev_power: sensor.car_kwh_meter_active_power
        battery_soc: sensor.indevolt_state_of_charge
        battery_power: sensor.indevolt_power
        # roles are open-ended — add more entries as new roles are needed (e.g. the autumn heating
        # work's thermostat/valve entities); EntityMap is a plain role->entity_id dict, no code
        # change required here to add one.

Errors distinguish transport failure (`HaUnreachable`) from an auth failure (`HaAuthError`, 401), a
missing entity (`HaEntityNotFound`, 404) and a malformed response (`HaMalformedResponse` — not JSON,
or missing the fields we need). This lets a future caller apply SPEC §4.6's stale/missing handling
per failure kind instead of one catch-all.

The access token is **never logged** — log lines only ever include the base_url and entity ids.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from ems.timeutil import require_aware

_log = logging.getLogger("ems.sources.ha")

_UNAVAILABLE_STATES = {"unavailable", "unknown"}


class HaError(Exception):
    """Base class for every error this client raises."""


class HaUnreachable(HaError):
    """HA never gave us a usable answer: a transport failure (DNS/connect/timeout), or an
    unexpected HTTP status this client doesn't have a more specific type for."""


class HaAuthError(HaError):
    """HA responded 401 Unauthorized — the token is missing, wrong, or expired."""


class HaEntityNotFound(HaError):
    """HA responded 404 Not Found — no such `entity_id` (typo, or the integration was removed)."""


class HaMalformedResponse(HaError):
    """HA responded 200 but the payload wasn't the shape we need (not JSON, or missing the
    `state`/`last_updated` fields) — a future HA API change should surface as this, not a crash."""


@dataclass(frozen=True)
class NotAvailable:
    """A state that exists in HA but isn't a usable numeric reading right now — `state ==
    "unavailable"/"unknown"` (normal during an HA restart or integration reload), or a value that
    doesn't parse as a float. NOT an error: like an unreachable device elsewhere in the codebase
    (`ems/sources/live.py`), this degrades the *signal* — the caller applies the existing
    freshness/data-quality rules (SPEC §4.6) rather than getting an exception."""

    raw_state: str


def parse_numeric_state(raw_state: str) -> float | NotAvailable:
    """HA states are always strings. `"unavailable"`/`"unknown"` and anything else that doesn't
    parse as a float become `NotAvailable` rather than raising — defensive, so a future HA state
    format change degrades a reading instead of crashing the EMS (CLAUDE.md fail-safe)."""
    if raw_state in _UNAVAILABLE_STATES:
        return NotAvailable(raw_state)
    try:
        return float(raw_state)
    except (TypeError, ValueError):
        return NotAvailable(raw_state)


@dataclass(frozen=True)
class HaState:
    """One entity's parsed state (SPEC §9.2). `value` is the numeric reading, or a `NotAvailable`
    sentinel — never an exception for an unavailable/non-numeric state (fail-safe; the caller
    decides what that means for freshness)."""

    entity_id: str
    raw_state: str
    value: float | NotAvailable
    attributes: dict[str, Any]
    last_updated: datetime  # tz-aware


@dataclass(frozen=True)
class EntityMap:
    """EMS role -> HA entity id (SPEC §5.2/§9). Roles are **open-ended** — this is a plain dict, so
    a new role (e.g. from the autumn heating work) needs a new config entry, not a code change.
    `grid_power`/`solar_power`/`battery_soc`/... are just the seeded set from SPEC §9's example."""

    roles: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_config(cls, entity_map: dict[str, str]) -> EntityMap:
        """Build from the `homeassistant.entity_map` config dict. Copies defensively so a later
        mutation of the source dict (e.g. a runtime-settings reload) can't change the map out from
        under an already-built client."""
        return cls(roles=dict(entity_map or {}))

    def entity_id(self, role: str) -> str:
        """The entity id mapped to `role`. Raises `KeyError` for an unmapped role — loud and
        immediate, the same "unmapped is a config bug, not a runtime condition to swallow" contract
        as `ems.sources.battery.intent_to_mode`'s unmapped-intent `KeyError`."""
        try:
            return self.roles[role]
        except KeyError:
            raise KeyError(f"entity_map has no entity id for role {role!r}") from None


@dataclass(frozen=True)
class RoleReading:
    """One role's result from `read_snapshot` — either a parsed `state` with its `age_seconds`
    (vs. the snapshot's reference time), or `state=None` + `error` when reading that one role's
    entity failed. Reported per-role so ONE bad entity degrades ITS role only (SPEC §4.6)."""

    role: str
    entity_id: str
    state: HaState | None
    error: HaError | None
    age_seconds: float | None


def _parse_state(entity_id: str, payload: Any) -> HaState:
    """Pure parser: a `GET /api/states/<entity_id>` JSON body -> `HaState`. Raises
    `HaMalformedResponse` for anything that isn't the expected shape."""
    if not isinstance(payload, dict):
        raise HaMalformedResponse(f"HA state for {entity_id!r} was not a JSON object: {payload!r}")
    raw_state = payload.get("state")
    if raw_state is None:
        raise HaMalformedResponse(f"HA state for {entity_id!r} had no 'state' field")
    last_updated_raw = payload.get("last_updated")
    if not last_updated_raw:
        raise HaMalformedResponse(f"HA state for {entity_id!r} had no 'last_updated' field")
    try:
        last_updated = datetime.fromisoformat(str(last_updated_raw))
    except (TypeError, ValueError) as exc:
        raise HaMalformedResponse(
            f"HA state for {entity_id!r} had an unparseable last_updated "
            f"{last_updated_raw!r}: {exc}"
        ) from exc
    if last_updated.tzinfo is None:
        # HA's REST API always includes a UTC offset; a naive value would be a different bug
        # (never observed live) — treat it as UTC rather than crash on it (defensive, matches the
        # "malformed input degrades, it doesn't raise" stance of the rest of this module).
        last_updated = last_updated.replace(tzinfo=UTC)
    attributes = payload.get("attributes")
    if not isinstance(attributes, dict):
        attributes = {}
    return HaState(
        entity_id=entity_id,
        raw_state=str(raw_state),
        value=parse_numeric_state(str(raw_state)),
        attributes=attributes,
        last_updated=last_updated,
    )


class HaClient:
    """Read-only Home Assistant REST client (SPEC §5/§9.2). Async + httpx-based: owns a pooled
    `httpx.AsyncClient` for its lifetime (`aclose()` releases it, or use it as an async context
    manager) — unlike the other sources' one-shot module-level `httpx.get`/`.post` calls, this
    client is expected to make several requests per cycle (`get_states`/`read_snapshot` over an
    `entity_map`), so a pooled connection is worth the lifecycle management.

    A caller-supplied `client` (e.g. tests injecting an `httpx.MockTransport`) is never closed by
    `aclose()` — it's the caller's own client, with its own lifecycle. NEVER logs the token.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 8.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def __aenter__(self) -> HaClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def get_state(self, entity_id: str) -> HaState:
        """`GET /api/states/<entity_id>`. Raises `HaUnreachable` (transport failure or an
        unexpected status), `HaAuthError` (401), `HaEntityNotFound` (404) or `HaMalformedResponse`
        (200 but not the expected shape)."""
        url = f"{self._base_url}/api/states/{entity_id}"
        try:
            resp = await self._client.get(url, headers=self._headers())
        except httpx.HTTPError as exc:
            raise HaUnreachable(
                f"HA unreachable at {self._base_url} for {entity_id!r}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if resp.status_code == 401:
            raise HaAuthError(f"HA rejected the token (401) reading {entity_id!r}")
        if resp.status_code == 404:
            raise HaEntityNotFound(f"HA has no entity {entity_id!r} (404)")
        if resp.status_code >= 400:
            raise HaUnreachable(
                f"HA returned HTTP {resp.status_code} reading {entity_id!r}"
            )
        try:
            payload = resp.json()
        except ValueError as exc:
            raise HaMalformedResponse(
                f"HA response for {entity_id!r} was not JSON: {exc}"
            ) from exc
        return _parse_state(entity_id, payload)

    async def get_states(self, entity_ids: Iterable[str]) -> dict[str, HaState]:
        """One `get_state()` call per id, in order — HA has no bulk-get endpoint. Deliberately
        simple: a single entity's failure raises immediately (propagates whichever `HaError`
        subclass `get_state` raised); `read_snapshot` is the per-role fail-safe variant for callers
        that need one bad entity to degrade rather than abort the whole read."""
        out: dict[str, HaState] = {}
        for entity_id in entity_ids:
            out[entity_id] = await self.get_state(entity_id)
        return out

    async def read_snapshot(
        self, entity_map: EntityMap, *, now: datetime | None = None
    ) -> dict[str, RoleReading]:
        """Read every role's mapped entity and report its value + staleness (`last_updated` age vs.
        `now`) in one shot, so a caller can apply the SAME freshness rules (`ems/freshness.py`) as
        every other source instead of re-deriving them here. A single role's entity failing
        (unreachable/401/404/malformed) is caught and reported on THAT role only — mirrors the
        `attempt()` pattern in `ems/sources/live.py`: one bad signal degrades to missing/stale, it
        never sinks the whole snapshot (SPEC §4.6, fail-safe)."""
        at = require_aware(now, "now") if now is not None else datetime.now(UTC)
        out: dict[str, RoleReading] = {}
        for role, entity_id in entity_map.roles.items():
            try:
                state = await self.get_state(entity_id)
            except HaError as exc:
                _log.warning(
                    "HA role %r (%s) read failed (%s: %s)",
                    role, entity_id, type(exc).__name__, exc,
                )
                out[role] = RoleReading(
                    role=role, entity_id=entity_id, state=None, error=exc, age_seconds=None
                )
                continue
            age = max(0.0, (at - state.last_updated).total_seconds())
            out[role] = RoleReading(
                role=role, entity_id=entity_id, state=state, error=None, age_seconds=age
            )
        return out
