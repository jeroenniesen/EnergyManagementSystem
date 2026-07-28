import asyncio
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from ems.application.context import ApplicationContext
from ems.application.services.report import ReportService


def test_report_service_delegates_report_and_finance():
    async def report(*args):
        return {"ok": True}

    async def finance(*args):
        return [{"saved_eur": 1.25, "grid_cost_eur": 2, "battery_cost_eur": 0.5,
                 "grid_import_kwh": 3, "grid_export_kwh": 1, "price_coverage": 1,
                 "has_data": True}]

    ctx = ApplicationContext(source=None, runtime_state={
        "report_for_window": report, "finance_window": finance,
        "savings": lambda: {"today_eur": None},
    })
    svc = ReportService(ctx)
    now = datetime.now(UTC)
    assert asyncio.run(svc.report("day", now, now, "x", False, now)) == {"ok": True}
    result = asyncio.run(svc.finance(now, now, now, "day", "x", False))
    assert result["totals"]["saved_eur"] == 1.25
    local = datetime(2026, 7, 28, 12, tzinfo=ZoneInfo("Europe/Amsterdam"))
    result = asyncio.run(svc.finance(local, local, local, "day", "x", False))
    assert result["window_start"] == "2026-07-28T10:00:00+00:00"
    assert result["window_end"] == "2026-07-28T10:00:00+00:00"
