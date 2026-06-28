"""Strategy selection + dispatch (SPEC §8.2).

Two strategies, one `Plan` interface:
  - **summer** 'solar-first': fill from PV, run the night on the battery, grid only the shortfall.
  - **winter** arbitrage: charge the cheap window, discharge the expensive peaks.

`select_strategy` resolves the runtime mode (`auto`|`summer`|`winter`) to one of the two — `auto`
chooses by the local season. `build_plan` dispatches to the matching planner. Both planners emit
the same `Plan`, so the projection, validator, UI and controller paths are unchanged.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from ems.planner.adaptive import AdaptiveConfig, plan_adaptive
from ems.planner.rule_based import PlannerConfig, plan_rule_based
from ems.planner.schedule import Plan
from ems.planner.summer import SummerConfig, plan_summer
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot

# Northern-hemisphere "solar season": April–September. Configurable by the caller.
DEFAULT_SUMMER_MONTHS: frozenset[int] = frozenset({4, 5, 6, 7, 8, 9})


def select_strategy(
    now: datetime,
    mode: str | None,
    tz: ZoneInfo,
    *,
    summer_months: frozenset[int] = DEFAULT_SUMMER_MONTHS,
) -> str:
    """Resolve the runtime mode to 'summer' or 'winter'. An explicit mode is honoured; anything
    else (including 'auto', None or an unknown value) picks by the LOCAL month."""
    m = (mode or "auto").lower()
    if m in ("summer", "winter"):
        return m
    return "summer" if now.astimezone(tz).month in summer_months else "winter"


def build_plan(
    strategy: str,
    *,
    prices: list[PriceSlot],
    forecast: list[ForecastSlot] | None,
    now: datetime,
    soc_pct: float,
    winter_cfg: PlannerConfig,
    summer_cfg: SummerConfig,
    load_w_by: dict[datetime, float] | None = None,
    adaptive_cfg: AdaptiveConfig | None = None,
) -> Plan:
    """Dispatch to the chosen strategy's planner. `strategy` is already resolved (not 'auto').

    Summer uses the demand-aware adaptive charger (peak-shaving, near-optimal — validated by the
    backtest) when an expected-load profile + AdaptiveConfig are supplied; otherwise it falls back
    to the simpler solar-first planner."""
    if strategy == "summer":
        if adaptive_cfg is not None and load_w_by is not None:
            return plan_adaptive(prices, forecast or [], now, soc_pct=soc_pct,
                                 load_w_by=load_w_by, cfg=adaptive_cfg)
        return plan_summer(prices, forecast or [], now, soc_pct=soc_pct, cfg=summer_cfg)
    return plan_rule_based(prices, now, winter_cfg)
