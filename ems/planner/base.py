"""Rule-based Planner adapter (SPEC §8 / §13.2, BACKLOG B-47).

Wraps the existing summer/winter/adaptive dispatch (`strategy.build_plan`) behind the `Planner`
port and stamps every emitted `Plan` with identity + `PlannerInputSnapshot` for audit/replay.
Pure planners (`plan_rule_based` / `plan_summer` / `plan_adaptive`) stay unchanged unit-test entry
points; this module is the composition face the control loop calls.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime

from ems.domain import PlannerInputSnapshot, PlannerMode
from ems.planner.adaptive import AdaptiveConfig
from ems.planner.rule_based import PlannerConfig
from ems.planner.schedule import Plan
from ems.planner.strategy import build_plan
from ems.planner.summer import SummerConfig
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot

SLOT_MINUTES = 15
_DIGEST_LEN = 16
_CONFIG_HASH_LEN = 12


@dataclass(frozen=True)
class PlannerRequest:
    """Inputs the Planner port consumes — identical shape for rule_based | ml | advisory."""

    now: datetime
    prices: list[PriceSlot]
    forecast: list[ForecastSlot]
    soc_pct: float
    strategy: str  # already-resolved 'summer' | 'winter'
    winter_cfg: PlannerConfig
    summer_cfg: SummerConfig
    load_w_by: dict[datetime, float] | None = None
    adaptive_cfg: AdaptiveConfig | None = None
    planner_mode: PlannerMode | str = PlannerMode.RULE_BASED
    price_provenance: str | None = None
    forecast_provider: str | None = None
    forecast_issued_at: datetime | None = None
    baseline: str | None = None
    capability_report_ref: str | None = None


def _mode_value(mode: PlannerMode | str) -> str:
    return mode.value if isinstance(mode, PlannerMode) else str(mode)


def config_hash_for_request(request: PlannerRequest, *, strategy: str) -> str:
    """Fingerprint of the configs the adapter actually received (no hand-maintained key list).

    Hashes `winter_cfg` / `summer_cfg` / `adaptive_cfg` plus `planner_mode` and the resolved
    strategy. Anything the control-loop builders fold into those dataclasses (import fees,
    night-reserve-driven summer target, top-up caps, …) is covered automatically.
    """
    payload = {
        "planner_mode": _mode_value(request.planner_mode),
        "strategy": strategy,
        "winter_cfg": asdict(request.winter_cfg),
        "summer_cfg": asdict(request.summer_cfg),
        "adaptive_cfg": asdict(request.adaptive_cfg) if request.adaptive_cfg is not None else None,
    }
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:_CONFIG_HASH_LEN]


def _digest(lines: list[str]) -> str | None:
    """Stable short content hash; None when there is nothing to fingerprint."""
    if not lines:
        return None
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:_DIGEST_LEN]


def digest_prices(prices: list[PriceSlot]) -> str | None:
    """Content digest of a price series (start + €/kWh). Detects drift without storing slots."""
    return _digest([f"{p.start.isoformat()}|{p.eur_per_kwh:.8f}" for p in prices])


def digest_forecast(forecast: list[ForecastSlot]) -> str | None:
    """Content digest of a forecast series (start + P10/P50/P90)."""
    return _digest([
        f"{s.start.isoformat()}|{s.p10_w:.4f}|{s.p50_w:.4f}|{s.p90_w:.4f}"
        for s in forecast
    ])


def digest_load(load_w_by: dict[datetime, float] | None) -> str | None:
    """Content digest of a load profile keyed by slot start (sorted for stability)."""
    if not load_w_by:
        return None
    return _digest([
        f"{t.isoformat()}|{float(w):.4f}" for t, w in sorted(load_w_by.items())
    ])


def _forecast_p50_kwh(forecast: list[ForecastSlot]) -> float | None:
    if not forecast:
        return None
    # 15-min slots: Wh = W * 0.25 h; sum → kWh.
    return round(sum(max(0.0, s.p50_w) for s in forecast) * 0.25 / 1000.0, 3)


def build_input_snapshot(request: PlannerRequest, *, strategy: str) -> PlannerInputSnapshot:
    """Build the compact+digest audit snapshot attached to every Plan from the port."""
    prices = request.prices
    euros = [p.eur_per_kwh for p in prices] if prices else []
    return PlannerInputSnapshot(
        taken_at=request.now,
        planner_mode=_mode_value(request.planner_mode),
        strategy=strategy,
        soc_pct=float(request.soc_pct),
        price_slots=len(prices),
        price_resolution_minutes=SLOT_MINUTES,
        price_provenance=request.price_provenance,
        price_min_eur=min(euros) if euros else None,
        price_max_eur=max(euros) if euros else None,
        prices_digest=digest_prices(prices),
        forecast_slots=len(request.forecast),
        forecast_provider=request.forecast_provider,
        forecast_issued_at=request.forecast_issued_at,
        forecast_p50_kwh=_forecast_p50_kwh(request.forecast),
        forecast_digest=digest_forecast(request.forecast),
        load_digest=digest_load(request.load_w_by),
        baseline=request.baseline,
        capability_report_ref=request.capability_report_ref,
        config_hash=config_hash_for_request(request, strategy=strategy),
    )


def _plan_id(request: PlannerRequest, plan: Plan) -> str:
    deadline = plan.deadline.isoformat() if plan.deadline is not None else ""
    raw = (
        f"{_mode_value(request.planner_mode)}|{plan.strategy}|{plan.target_soc}|"
        f"{deadline}|{request.now.isoformat()}|{request.soc_pct:.2f}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class RuleBasedPlanner:
    """Adapter: seasonal rule-based planners behind the `Planner` Protocol."""

    def plan(self, request: PlannerRequest) -> Plan:
        plan = build_plan(
            request.strategy,
            prices=request.prices,
            forecast=request.forecast,
            now=request.now,
            soc_pct=request.soc_pct,
            winter_cfg=request.winter_cfg,
            summer_cfg=request.summer_cfg,
            load_w_by=request.load_w_by,
            adaptive_cfg=request.adaptive_cfg,
        )
        strategy = plan.strategy or request.strategy
        snapshot = build_input_snapshot(request, strategy=strategy)
        mode = _mode_value(request.planner_mode)
        stamped = replace(
            plan,
            strategy=strategy,
            id=_plan_id(request, plan),
            version=1,
            input_snapshot=snapshot,
            planner_mode=mode,
        )
        return stamped
