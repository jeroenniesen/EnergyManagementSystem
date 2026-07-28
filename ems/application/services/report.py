from __future__ import annotations

import inspect
from datetime import UTC, datetime

from ems.application.context import ApplicationContext


class ReportService:
    """Read-only report, finance, and savings orchestration."""

    def __init__(self, context: ApplicationContext):
        self.context = context

    async def report(self, period: str, start: datetime, end: datetime, label: str,
                     partial: bool, now_local: datetime) -> dict[str, object]:
        return await self.context.runtime_state["report_for_window"](
            period, start, end, label, partial, now_local
        )

    async def finance(self, start: datetime, end: datetime, now_local: datetime,
                      period: str, label: str, partial: bool) -> dict[str, object]:
        result = self.context.runtime_state["finance_window"](start, end, now_local)
        days = await result if inspect.isawaitable(result) else result

        def total(key: str) -> float | None:
            vals = [d[key] for d in days if d.get(key) is not None]
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
        return self.context.runtime_state["savings"]()
