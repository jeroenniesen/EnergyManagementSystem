"""Strategy selection + dispatch (SPEC §8.2).

Two strategies, one `Plan` interface:
  - **summer** 'solar-first': fill from PV, run the night on the battery, grid only the shortfall.
  - **winter** arbitrage: charge the cheap window, discharge the expensive peaks.

`select_strategy` resolves the runtime mode (`auto`|`summer`|`winter`) to one of the two — `auto`
chooses by the local season. `build_plan` dispatches to the matching planner. Both planners emit
the same `Plan`, so the projection, validator, UI and controller paths are unchanged.
"""
from __future__ import annotations

from dataclasses import replace
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
# Energy-condition thresholds for `auto` (energy review P1.1 — don't decide by calendar alone):
# a forecast surplus this large means solar can carry the home (solar-first); otherwise a price
# spread this wide makes arbitrage (price-smart) worthwhile.
AUTO_SURPLUS_KWH = 3.0
AUTO_SPREAD_EUR = 0.10


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


def select_strategy_with_reason(
    now: datetime,
    mode: str | None,
    tz: ZoneInfo,
    *,
    surplus_kwh: float | None = None,
    price_spread_eur: float | None = None,
    summer_months: frozenset[int] = DEFAULT_SUMMER_MONTHS,
) -> tuple[str, str]:
    """Resolve the strategy AND a deterministic, human-readable reason (emotional review: 'why this
    strategy'). An explicit mode is honoured verbatim. For `auto`, choose by ENERGY CONDITIONS — a
    sunny day in March should run solar-first, a dull day in September price-smart — using the
    forecast surplus (kWh) and the day's price spread (€), falling back to the calendar season only
    when those inputs are absent."""
    m = (mode or "auto").lower()
    if m == "summer":
        return "summer", "You chose Solar-first — running the night on your own solar."
    if m == "winter":
        return "winter", "You chose Price-smart — arbitraging cheap vs. expensive grid windows."
    # auto: energy-condition driven.
    if surplus_kwh is not None and surplus_kwh >= AUTO_SURPLUS_KWH:
        return "summer", (f"Running solar-first — the forecast surplus (~{surplus_kwh:.0f} kWh) "
                          "should carry the home tonight.")
    if (surplus_kwh is not None and price_spread_eur is not None
            and price_spread_eur >= AUTO_SPREAD_EUR):
        return "winter", (f"Running price-smart — low solar (~{surplus_kwh:.0f} kWh) and a "
                          f"€{price_spread_eur:.2f} price spread make arbitrage worthwhile.")
    season = "summer" if now.astimezone(tz).month in summer_months else "winter"
    label = "solar-first" if season == "summer" else "price-smart"
    return season, f"Running {label} by season — not enough forecast/price data to decide yet."


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
    backtest) when a load profile + AdaptiveConfig are supplied, else the simpler solar-first
    planner. Winter uses the arbitrage planner, which — when a load profile + battery sizing are
    supplied — sizes the grid top-up to the evening peak load above reserve and carries target SoC +
    deadline (energy review P1.2: no longer price-only), keeping its distinct charge-cheap/
    discharge-peaks character so the season choice still changes the plan."""
    if strategy == "summer":
        if adaptive_cfg is not None and load_w_by is not None:
            plan = plan_adaptive(prices, forecast or [], now, soc_pct=soc_pct,
                                 load_w_by=load_w_by, cfg=adaptive_cfg)
        else:
            plan = plan_summer(prices, forecast or [], now, soc_pct=soc_pct, cfg=summer_cfg)
    elif load_w_by is not None and adaptive_cfg is not None:
        plan = plan_rule_based(
            prices, now, winter_cfg, soc_pct=soc_pct, load_w_by=load_w_by,
            usable_kwh=adaptive_cfg.usable_kwh, reserve_soc_pct=adaptive_cfg.reserve_soc_pct,
            max_charge_w=adaptive_cfg.max_charge_w,
        )
    else:
        plan = plan_rule_based(prices, now, winter_cfg)
    # The resolved strategy is authoritative on the returned plan (whichever planner ran).
    return replace(plan, strategy=strategy)
