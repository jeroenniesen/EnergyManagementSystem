"""Strategy selection + dispatch (SPEC §8.2).

Two strategies, one `Plan` interface:
  - **summer** 'solar-first': fill from PV, run the night on the battery, grid only the shortfall.
  - **winter** arbitrage: charge the cheap window, discharge the expensive peaks.

`select_strategy` resolves the runtime mode (`auto`|`summer`|`winter`) to one of the two — `auto`
chooses by the local season. `build_plan` dispatches to the matching planner. Both planners emit
the same `Plan`, so the projection, validator, UI and controller paths are unchanged.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date as date_cls
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
# Seasonal-transition hysteresis (SPEC §8.4, BACKLOG B-15): a season change must persist this many
# consecutive daily evaluations before it commits, so a borderline shoulder-month day can't flap
# summer↔winter. 0 disables (switch instantly, the pre-B-15 behaviour).
DEFAULT_HYSTERESIS_DAYS = 3


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


# --------------------------------------------------------------------------------------------------
# Seasonal-transition hysteresis (SPEC §8.4 / BACKLOG B-15)
# --------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class HysteresisState:
    """Cross-cycle memory that dampens summer↔winter flips. Restart-safe: persisted as JSON in the
    KV cache and reloaded on boot, following the digest-dedupe / car-anchor pattern.

    `committed` is the season currently in force (None = fresh install, no memory yet). `pending` is
    the candidate season a run of disagreeing days is counting toward; `count` is how many
    consecutive days it has held; `last_day` is the local date (ISO) of the last day the counter
    moved — so a 5-min control loop advances it at most ONCE per calendar day (§8.4 counts DAYS,
    not cycles)."""

    committed: str | None = None
    pending: str | None = None
    count: int = 0
    last_day: str | None = None

    def to_json(self) -> str:
        return json.dumps({"committed": self.committed, "pending": self.pending,
                           "count": self.count, "last_day": self.last_day})

    @classmethod
    def from_json(cls, raw: str | None) -> HysteresisState:
        """Rehydrate from the KV cache; any absent/corrupt value → a fresh (empty) state, which
        behaves exactly like today (the current pick commits immediately, no switch delay)."""
        if not raw:
            return cls()
        try:
            d = json.loads(raw)
            return cls(committed=d.get("committed"), pending=d.get("pending"),
                       count=int(d.get("count", 0)), last_day=d.get("last_day"))
        except (ValueError, TypeError):
            return cls()


def apply_hysteresis(
    raw: str,
    state: HysteresisState,
    *,
    hysteresis_days: int,
    day: date_cls | str,
) -> tuple[str, HysteresisState]:
    """Dampen the instantaneous season pick `raw` (SPEC §8.4). Returns (committed_season, state).

    Fresh memory (`state.committed is None`) OR `hysteresis_days <= 0` commits `raw` at once — so a
    fresh install / disabled hysteresis behaves exactly like the pre-B-15 instantaneous pick, with
    NO first-evaluation delay. Hysteresis only ever damps a *change* away from the committed season:
    the opposite-season signal must hold `hysteresis_days` consecutive days (a single agreeing day
    resets the run) before the switch commits. The day counter advances at most once per distinct
    `day`, so the control loop's cadence (every 5 min) can't fast-forward it."""
    day_key = day.isoformat() if isinstance(day, date_cls) else str(day)
    # Fresh memory OR disabled → commit the raw pick straight away.
    if state.committed is None or hysteresis_days <= 0:
        return raw, HysteresisState(committed=raw, last_day=day_key)
    # Signal agrees with the committed season → clear any pending switch (rewrite only if needed).
    if raw == state.committed:
        if state.pending is None and state.count == 0:
            # Keep the persisted day marker current; callers can use it to refresh TTL once/day.
            return state.committed, state
        # A transient agreeing tick must not erase a multi-day run.  Reset only on the first
        # evaluation of a new local day; the control loop runs every few minutes.
        if state.last_day == day_key:
            return state.committed, state
        return state.committed, HysteresisState(committed=state.committed, last_day=day_key)
    # Disagreement: advance the consecutive-day counter at most once per calendar day.
    if state.last_day == day_key:
        return state.committed, state  # already evaluated today — hold, don't double-count
    count = state.count + 1 if state.pending == raw else 1
    if count >= hysteresis_days:  # held long enough — the switch commits
        return raw, HysteresisState(committed=raw, last_day=day_key)
    return state.committed, HysteresisState(committed=state.committed, pending=raw,
                                            count=count, last_day=day_key)


def resolve_strategy_hysteretic(
    now: datetime,
    mode: str | None,
    tz: ZoneInfo,
    state: HysteresisState,
    *,
    surplus_kwh: float | None = None,
    price_spread_eur: float | None = None,
    summer_months: frozenset[int] = DEFAULT_SUMMER_MONTHS,
    hysteresis_days: int = DEFAULT_HYSTERESIS_DAYS,
) -> tuple[str, str, HysteresisState]:
    """`select_strategy_with_reason` + seasonal hysteresis (SPEC §8.4). Returns
    (strategy, reason, new_state); persist `new_state` so the counter survives restarts.

    A forced mode (`summer`/`winter`) is honoured verbatim and bypasses hysteresis — the user's
    explicit choice takes effect now — but re-baselines the memory to that season (clearing any
    stale pending count) so a later return to `auto` starts clean. For `auto`, the instantaneous
    energy-condition pick is dampened: a season change waits `hysteresis_days` steady days."""
    raw, why = select_strategy_with_reason(
        now, mode, tz, surplus_kwh=surplus_kwh, price_spread_eur=price_spread_eur,
        summer_months=summer_months,
    )
    m = (mode or "auto").lower()
    if m in ("summer", "winter"):
        return raw, why, HysteresisState(committed=raw, last_day=state.last_day)
    committed, new_state = apply_hysteresis(
        raw, state, hysteresis_days=hysteresis_days, day=now.astimezone(tz).date(),
    )
    if committed == raw:
        return committed, why, new_state
    # A switch is pending but hasn't held long enough — stay put and SAY why (explainability first:
    # "why it is NOT acting" on the season, CLAUDE.md).
    label = "solar-first" if committed == "summer" else "price-smart"
    other = "solar-first" if raw == "summer" else "price-smart"
    held = (f"Holding {label} — today's signal leans {other}, but a season switch waits for "
            f"{new_state.count}/{hysteresis_days} steady days (avoids shoulder-season flip-flop).")
    return committed, held, new_state


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
