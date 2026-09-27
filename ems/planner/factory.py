"""Planner adapter registry + factory (SPEC §8 / B-47).

Adapters implement the shared `Planner` port (`plan(PlannerRequest) → Plan`). This module is the
**only** place that picks which adapter to construct from `planner.mode` — the control loop calls
`build_planner` and never branches on mode names itself.

Register a new producer with `@register_planner("…")`. ML / advisory adapters are **not** shipped
here (M6); requesting those modes falls back to `rule_based` so the Pi path never loads
accelerator code. Every adapter's `Plan` still passes the unchanged §8.11 validator.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ems.domain import PlannerMode
from ems.planner.base import RuleBasedPlanner

_log = logging.getLogger("ems.planner.factory")

PlannerBuilder = Callable[[], Any]

_REGISTRY: dict[str, PlannerBuilder] = {}

# Modes that are SPEC-valid but not yet backed by an adapter — seam only until M6.
_UNAVAILABLE_MODES = frozenset({PlannerMode.ML.value, PlannerMode.ADVISORY.value})


def register_planner(name: str) -> Callable[[PlannerBuilder], PlannerBuilder]:
    """Decorator: register `name` as a `planner.mode` adapter id."""

    def deco(fn: PlannerBuilder) -> PlannerBuilder:
        key = name.strip().lower()
        if not key:
            raise ValueError("planner name must be non-empty")
        if key in _REGISTRY:
            raise ValueError(f"planner already registered: {key}")
        _REGISTRY[key] = fn
        return fn

    return deco


def registered_planners() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


@register_planner("rule_based")
def _build_rule_based() -> RuleBasedPlanner:
    return RuleBasedPlanner()


def build_planner(mode: str | PlannerMode | None = None) -> RuleBasedPlanner:
    """Return a Planner adapter for `mode`. Unknown / unavailable modes → rule_based (fail-safe)."""
    if isinstance(mode, PlannerMode):
        key = mode.value
    else:
        key = (mode or PlannerMode.RULE_BASED.value).strip().lower() or PlannerMode.RULE_BASED.value

    if key in _UNAVAILABLE_MODES:
        _log.warning(
            "planner.mode=%s has no adapter yet (M6 seam); falling back to rule_based",
            key,
        )
        key = PlannerMode.RULE_BASED.value
    elif key not in _REGISTRY:
        _log.warning("unknown planner.mode=%s; falling back to rule_based", key)
        key = PlannerMode.RULE_BASED.value

    builder = _REGISTRY[key]
    return builder()
