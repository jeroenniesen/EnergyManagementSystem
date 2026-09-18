from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from ems.application.context import ApplicationContext, require_collaborator


class ReportService:
    """Read-only report, finance, and savings orchestration."""

    def __init__(self, context: ApplicationContext):
        self.context = context

    async def report(self, period: str, start: datetime, end: datetime, label: str,
                     partial: bool, now_local: datetime) -> dict[str, object]:
        report = require_collaborator(self.context.report_for_window, "report_for_window")
        return await report(
            period, start, end, label, partial, now_local
        )

    async def finance(self, start: datetime, end: datetime, now_local: datetime,
                      period: str, label: str, partial: bool) -> dict[str, object]:
        finance = require_collaborator(self.context.finance_window, "finance_window")
        days = await finance(start, end, now_local)

        def total(key: Literal["grid_cost_eur", "battery_cost_eur", "saved_eur",
                               "grid_import_kwh", "grid_export_kwh"]) -> float | None:
            vals = [value for d in days if (value := d.get(key)) is not None]
            return round(sum(vals), 2) if vals else None

        totals = {
            "grid_cost_eur": total("grid_cost_eur"),
            "battery_cost_eur": total("battery_cost_eur"),
            "saved_eur": total("saved_eur"),
            "grid_import_kwh": total("grid_import_kwh") or 0.0,
            "grid_export_kwh": total("grid_export_kwh") or 0.0,
            "days_with_prices": sum(1 for d in days if d.get("price_coverage", 0) > 0),
            "days_with_data": sum(1 for d in days if d.get("has_data")),
        }
        return {"period": period, "label": label,
                "window_start": start.astimezone(UTC).isoformat(),
                "window_end": end.astimezone(UTC).isoformat(),
                "partial": partial, "days": days, "totals": totals}

    def savings(self) -> dict[str, object]:
        savings = require_collaborator(self.context.savings_snapshot, "savings_snapshot")
        return savings(self.context.clock.now_utc())
