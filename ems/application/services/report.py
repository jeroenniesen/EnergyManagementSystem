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
                               "grid_import_kwh", "grid_export_kwh",
                               "solar_self_use_eur", "avoided_expensive_eur",
                               "battery_contribution_eur",
                               "saved_vs_auto_eur", "auto_cost_eur"]) -> float | None:
            vals = [value for d in days if (value := d.get(key)) is not None]
            return round(sum(vals), 2) if vals else None

        vs_auto_days = sum(
            1 for d in days
            if d.get("vs_auto_has_sim") and d.get("saved_vs_auto_eur") is not None
        )
        totals = {
            "grid_cost_eur": total("grid_cost_eur"),
            "battery_cost_eur": total("battery_cost_eur"),
            "saved_eur": total("saved_eur"),
            "solar_self_use_eur": total("solar_self_use_eur"),
            "avoided_expensive_eur": total("avoided_expensive_eur"),
            "battery_contribution_eur": total("battery_contribution_eur"),
            "saved_vs_auto_eur": total("saved_vs_auto_eur"),
            "auto_cost_eur": total("auto_cost_eur"),
            "vs_auto_days": vs_auto_days,
            "grid_import_kwh": total("grid_import_kwh") or 0.0,
            "grid_export_kwh": total("grid_export_kwh") or 0.0,
            "days_with_prices": sum(1 for d in days if d.get("price_coverage", 0) > 0),
            "days_with_data": sum(1 for d in days if d.get("has_data")),
            "days_with_coverage_gap": sum(
                1 for d in days
                if d.get("has_data") and (
                    float(d.get("price_coverage") or 0) < 1.0 - 1e-9
                    or float(d.get("sample_coverage") or 0) < 1.0 - 1e-9
                )
            ),
            "days_without_data": sum(1 for d in days if not d.get("has_data")),
        }
        # #131: 90-day rolling EMS-vs-AUTO (per-request; never stored). Falls back to the
        # period-window sum when the rolling collaborator is absent (unit tests).
        vs_auto: dict[str, object] | None = None
        if self.context.vs_auto_rolling is not None:
            vs_auto = await self.context.vs_auto_rolling(90, now_local)
        elif totals["saved_vs_auto_eur"] is not None:
            vs_auto = {
                "saved_eur": totals["saved_vs_auto_eur"],
                "auto_cost_eur": totals["auto_cost_eur"],
                "days_simulated": vs_auto_days,
                "label": "gesimuleerd",
            }
        return {"period": period, "label": label,
                "window_start": start.astimezone(UTC).isoformat(),
                "window_end": end.astimezone(UTC).isoformat(),
                "partial": partial, "days": days, "totals": totals,
                "vs_auto": vs_auto}

    def savings(self) -> dict[str, object]:
        savings = require_collaborator(self.context.savings_snapshot, "savings_snapshot")
        return savings(self.context.clock.now_utc())
