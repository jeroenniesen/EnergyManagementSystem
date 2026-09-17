from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ems.control.service import ControlService
from ems.domain import BatteryIntent
from ems.planner.schedule import Plan, PlanSlot
from ems.savings import estimate_daily_savings_eur
from ems.sources.mock import MockSource
from ems.sources.prices import PriceSlot
from ems.tariffs import policy_from_settings
from ems.web.api import create_app


@pytest.mark.parametrize('efficiency,wear,risk,expected', [
    (0.5, 0.2, 0.1, 0.0),
    (0.95, 0.01, 0.01, 0.10),
])
def test_savings_uses_the_assumptions_published_with_the_result(efficiency, wear, risk, expected):
    now = datetime(2026, 9, 17, 0, tzinfo=UTC)
    later = now + timedelta(minutes=15)
    plan = Plan(created_at=now, slots=(
        PlanSlot(now, BatteryIntent.GRID_CHARGE_TO_TARGET, 'charge'),
        PlanSlot(later, BatteryIntent.DISCHARGE_FOR_LOAD, 'discharge'),
    ))
    prices = [PriceSlot(now, 0.10), PriceSlot(later, 0.40)]
    with patch.object(ControlService, 'current_plan', return_value=(now, prices, plan)):
        app = create_app(MockSource(), dry_run=True, dev_mode='mock')
        settings = app.state.application_context.settings
        settings.update({
            'planner.round_trip_efficiency': efficiency,
            'planner.degradation_eur_per_kwh': wear,
            'planner.risk_margin_eur_per_kwh': risk,
        })
        with TestClient(app) as client:
            response = client.get('/api/savings')
        assert response.status_code == 200
        result = response.json()
        assumptions = result['economic_snapshot']
        assert assumptions['round_trip_efficiency'] == efficiency
        assert assumptions['degradation_eur_per_kwh'] == wear
        assert assumptions['risk_margin_eur_per_kwh'] == risk
        recomputed = estimate_daily_savings_eur(
            plan, {p.start: p.eur_per_kwh for p in prices},
            efficiency=assumptions['round_trip_efficiency'],
            degradation_eur_per_kwh=assumptions['degradation_eur_per_kwh'],
            risk_margin_eur_per_kwh=assumptions['risk_margin_eur_per_kwh'],
            tariff_policy=policy_from_settings(settings),
        )
        assert recomputed == expected
        assert result['today_eur'] == recomputed
