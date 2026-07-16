import asyncio
from datetime import UTC, datetime

import httpx

from ems.sources.ha import (
    EntityMap,
    HaAuthError,
    HaClient,
    HaEntityNotFound,
    HaMalformedResponse,
    HaState,
    HaUnreachable,
    NotAvailable,
    parse_numeric_state,
)

GRID_ENTITY = "sensor.p1_meter_active_power"
SOC_ENTITY = "sensor.indevolt_state_of_charge"


def _state_payload(entity_id: str, state: str, *, last_updated: str = "2026-07-16T10:00:00+00:00",
                    attributes: dict | None = None) -> dict:
    return {
        "entity_id": entity_id,
        "state": state,
        "attributes": attributes or {"unit_of_measurement": "W"},
        "last_changed": last_updated,
        "last_updated": last_updated,
        "context": {"id": "abc", "parent_id": None, "user_id": None},
    }


def _client_for(handler, *, token: str = "s3cr3t") -> HaClient:
    transport = httpx.MockTransport(handler)
    async_client = httpx.AsyncClient(transport=transport)
    return HaClient("http://homeassistant.local:8123", token, client=async_client)


def _run(coro):
    return asyncio.run(coro)


# --- parse_numeric_state ---------------------------------------------------------------------


def test_parse_numeric_state_happy_path():
    assert parse_numeric_state("245.0") == 245.0
    assert parse_numeric_state("-12") == -12.0


def test_parse_numeric_state_unavailable_and_unknown_are_not_exceptions():
    assert parse_numeric_state("unavailable") == NotAvailable("unavailable")
    assert parse_numeric_state("unknown") == NotAvailable("unknown")


def test_parse_numeric_state_non_numeric_degrades_not_raises():
    assert parse_numeric_state("charging") == NotAvailable("charging")


# --- EntityMap ---------------------------------------------------------------------------------


def test_entity_map_from_config_dict():
    em = EntityMap.from_config({
        "grid_power": GRID_ENTITY,
        "battery_soc": SOC_ENTITY,
    })
    assert em.entity_id("grid_power") == GRID_ENTITY
    assert em.entity_id("battery_soc") == SOC_ENTITY


def test_entity_map_is_extensible_to_new_roles():
    # Roles aren't hardcoded — an arbitrary role (e.g. a future heating entity) just works.
    em = EntityMap.from_config({"thermostat_setpoint": "climate.living_room"})
    assert em.entity_id("thermostat_setpoint") == "climate.living_room"


def test_entity_map_unmapped_role_raises_keyerror():
    em = EntityMap.from_config({"grid_power": GRID_ENTITY})
    try:
        em.entity_id("solar_power")
        raise AssertionError("expected KeyError")
    except KeyError:
        pass


def test_entity_map_from_config_copies_defensively():
    src = {"grid_power": GRID_ENTITY}
    em = EntityMap.from_config(src)
    src["grid_power"] = "sensor.something_else"
    assert em.entity_id("grid_power") == GRID_ENTITY  # unaffected by later mutation


# --- HaClient.get_state: happy path -------------------------------------------------------------


def test_get_state_happy_path_parses_value_and_last_updated():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer s3cr3t"
        assert request.url.path == f"/api/states/{GRID_ENTITY}"
        return httpx.Response(200, json=_state_payload(GRID_ENTITY, "245.0"))

    client = _client_for(handler)

    async def run():
        state = await client.get_state(GRID_ENTITY)
        await client.aclose()
        return state

    state = _run(run())
    assert isinstance(state, HaState)
    assert state.entity_id == GRID_ENTITY
    assert state.value == 245.0
    assert state.raw_state == "245.0"
    assert state.last_updated == datetime(2026, 7, 16, 10, 0, 0, tzinfo=UTC)
    assert state.attributes["unit_of_measurement"] == "W"


def test_get_state_never_logs_the_token(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_state_payload(GRID_ENTITY, "1.0"))

    client = _client_for(handler, token="super-secret-token")

    async def run():
        await client.get_state(GRID_ENTITY)
        await client.aclose()

    with caplog.at_level("DEBUG"):
        _run(run())
    assert "super-secret-token" not in caplog.text


# --- HaClient.get_state: error paths -------------------------------------------------------------


def test_get_state_401_raises_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "401: Unauthorized"})

    client = _client_for(handler)

    async def run():
        try:
            await client.get_state(GRID_ENTITY)
            raise AssertionError("expected HaAuthError")
        except HaAuthError:
            pass
        finally:
            await client.aclose()

    _run(run())


def test_get_state_404_raises_entity_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Entity not found"})

    client = _client_for(handler)

    async def run():
        try:
            await client.get_state("sensor.does_not_exist")
            raise AssertionError("expected HaEntityNotFound")
        except HaEntityNotFound:
            pass
        finally:
            await client.aclose()

    _run(run())


def test_get_state_unreachable_raises_ha_unreachable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = _client_for(handler)

    async def run():
        try:
            await client.get_state(GRID_ENTITY)
            raise AssertionError("expected HaUnreachable")
        except HaUnreachable:
            pass
        finally:
            await client.aclose()

    _run(run())


def test_get_state_500_raises_ha_unreachable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "internal error"})

    client = _client_for(handler)

    async def run():
        try:
            await client.get_state(GRID_ENTITY)
            raise AssertionError("expected HaUnreachable")
        except HaUnreachable:
            pass
        finally:
            await client.aclose()

    _run(run())


def test_get_state_malformed_json_raises_malformed_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    client = _client_for(handler)

    async def run():
        try:
            await client.get_state(GRID_ENTITY)
            raise AssertionError("expected HaMalformedResponse")
        except HaMalformedResponse:
            pass
        finally:
            await client.aclose()

    _run(run())


def test_get_state_missing_fields_raise_malformed_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"entity_id": GRID_ENTITY})  # no state/last_updated

    client = _client_for(handler)

    async def run():
        try:
            await client.get_state(GRID_ENTITY)
            raise AssertionError("expected HaMalformedResponse")
        except HaMalformedResponse:
            pass
        finally:
            await client.aclose()

    _run(run())


def test_get_state_unavailable_state_is_not_an_exception():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_state_payload(SOC_ENTITY, "unavailable"))

    client = _client_for(handler)

    async def run():
        state = await client.get_state(SOC_ENTITY)
        await client.aclose()
        return state

    state = _run(run())
    assert state.value == NotAvailable("unavailable")


# --- HaClient.get_states ------------------------------------------------------------------------


def test_get_states_batches_sequentially_over_multiple_entities():
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        entity_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=_state_payload(entity_id, "42.0"))

    client = _client_for(handler)

    async def run():
        states = await client.get_states([GRID_ENTITY, SOC_ENTITY])
        await client.aclose()
        return states

    states = _run(run())
    assert set(states) == {GRID_ENTITY, SOC_ENTITY}
    assert states[GRID_ENTITY].value == 42.0
    assert seen_paths == [f"/api/states/{GRID_ENTITY}", f"/api/states/{SOC_ENTITY}"]


def test_get_states_propagates_first_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(SOC_ENTITY):
            return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(200, json=_state_payload(GRID_ENTITY, "1.0"))

    client = _client_for(handler)

    async def run():
        try:
            await client.get_states([GRID_ENTITY, SOC_ENTITY])
            raise AssertionError("expected HaEntityNotFound")
        except HaEntityNotFound:
            pass
        finally:
            await client.aclose()

    _run(run())


# --- HaClient.read_snapshot ----------------------------------------------------------------------


def test_read_snapshot_reports_value_and_staleness_per_role():
    def handler(request: httpx.Request) -> httpx.Response:
        entity_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(
            200,
            json=_state_payload(entity_id, "300.0", last_updated="2026-07-16T09:59:00+00:00"),
        )

    client = _client_for(handler)
    entity_map = EntityMap.from_config({"grid_power": GRID_ENTITY, "battery_soc": SOC_ENTITY})
    now = datetime(2026, 7, 16, 10, 0, 0, tzinfo=UTC)  # 60s after last_updated

    async def run():
        snap = await client.read_snapshot(entity_map, now=now)
        await client.aclose()
        return snap

    snap = _run(run())
    assert set(snap) == {"grid_power", "battery_soc"}
    r = snap["grid_power"]
    assert r.entity_id == GRID_ENTITY
    assert r.state is not None and r.state.value == 300.0
    assert r.error is None
    assert r.age_seconds == 60.0


def test_read_snapshot_one_bad_role_does_not_sink_the_others():
    def handler(request: httpx.Request) -> httpx.Response:
        entity_id = request.url.path.rsplit("/", 1)[-1]
        if entity_id == SOC_ENTITY:
            return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(200, json=_state_payload(entity_id, "12.5"))

    client = _client_for(handler)
    entity_map = EntityMap.from_config({"grid_power": GRID_ENTITY, "battery_soc": SOC_ENTITY})

    async def run():
        snap = await client.read_snapshot(entity_map)
        await client.aclose()
        return snap

    snap = _run(run())
    assert snap["grid_power"].state is not None
    assert snap["grid_power"].error is None
    assert snap["battery_soc"].state is None
    assert isinstance(snap["battery_soc"].error, HaEntityNotFound)
    assert snap["battery_soc"].age_seconds is None


def test_read_snapshot_defaults_now_to_current_time_when_not_supplied():
    def handler(request: httpx.Request) -> httpx.Response:
        entity_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=_state_payload(entity_id, "1.0"))

    client = _client_for(handler)
    entity_map = EntityMap.from_config({"grid_power": GRID_ENTITY})

    async def run():
        snap = await client.read_snapshot(entity_map)
        await client.aclose()
        return snap

    snap = _run(run())
    # last_updated in the fixture is 2026-07-16T10:00:00+00:00 (in the past); age should be a
    # large-but-sane positive number, not None/negative.
    assert snap["grid_power"].age_seconds is not None
    assert snap["grid_power"].age_seconds >= 0


# --- async context manager + client lifecycle ----------------------------------------------------


def test_aclose_closes_an_owned_client():
    # No client injected -> HaClient built its own AsyncClient and must close it (no network I/O
    # is triggered by construction/close, so this needs no mock transport).
    client = HaClient("http://homeassistant.local:8123", "tok")

    async def run():
        await client.aclose()

    _run(run())
    assert client._client.is_closed


def test_aclose_does_not_close_a_caller_supplied_client():
    async_client = httpx.AsyncClient()
    client = HaClient("http://homeassistant.local:8123", "tok", client=async_client)

    async def run():
        await client.aclose()

    _run(run())
    assert not async_client.is_closed

    async def cleanup():
        await async_client.aclose()

    _run(cleanup())


def test_async_context_manager_closes_owned_client_on_exit():
    async def run():
        async with HaClient("http://homeassistant.local:8123", "tok") as client:
            inner = client._client
            assert not inner.is_closed
        assert inner.is_closed

    _run(run())
