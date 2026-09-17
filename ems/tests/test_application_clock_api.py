from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from ems.clock import FrozenClock
from ems.freshness import FreshnessTracker
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.web.api import create_app


@pytest.mark.parametrize('instant,hours', [
    (datetime(2026, 3, 29, 12, tzinfo=UTC), 23),
    (datetime(2026, 10, 25, 12, tzinfo=UTC), 25),
])
def test_moved_api_surfaces_share_the_injected_instant(instant, hours):
    tz = ZoneInfo('Europe/Amsterdam')
    frozen = FrozenClock(instant)
    app = create_app(
        MockSource(), tz=tz, dry_run=True, dev_mode='mock', clock=frozen,
        price_source=MockPriceSource(tz, clock=frozen.now_utc),
        solar_forecast=MockSolarForecastSource(tz, clock=frozen.now_utc),
    )
    assert app.state.application_context.clock is frozen
    assert app.state.control_service._clock is frozen
    with TestClient(app) as client:
        plan_response = client.get('/api/plan')
        assert plan_response.status_code == 200
        plan = plan_response.json()
        assert plan['created_at'] == instant.isoformat()
        assert plan['current_intent'] is not None
        verification = client.get('/api/plan-verification').json()
        assert verification['checked_at'] == instant.isoformat()
        assert verification['planned']['intent'] == plan['current_intent']
        for path in ('/api/report', '/api/finance'):
            response = client.get(path)
            assert response.status_code == 200
            report = response.json()
            start = datetime.fromisoformat(report['window_start'])
            end = datetime.fromisoformat(report['window_end'])
            assert start.astimezone(tz).date() == instant.astimezone(tz).date()
            assert (end-start).total_seconds() == hours*3600


def test_diagnostics_uses_injected_time_for_freshness():
    instant = datetime(2020, 1, 1, tzinfo=UTC)
    freshness = FreshnessTracker()
    freshness.mark('grid', instant)
    app = create_app(MockSource(), freshness=freshness, dry_run=True, dev_mode='mock')
    app.state.application_context.clock = FrozenClock(instant)
    with TestClient(app) as client:
        response = client.get('/api/diagnostics')
    assert response.status_code == 200
    grid = next(c for c in response.json()['checks'] if c['key'] == 'sensor.grid')
    assert grid['status'] == 'ok'
