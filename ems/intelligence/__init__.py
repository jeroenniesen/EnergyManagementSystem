"""Prediction + optimization boundary for EMS intelligence work.

The intelligence layer prepares probabilistic planning inputs. It deliberately delegates execution
planning to the deterministic planner/validator path so model uncertainty can improve decisions
without becoming a safety authority.
"""
from .planning import (
    PlanningScenario,
    RiskPolicy,
    build_planning_scenarios,
    plan_risk_aware_adaptive,
)

__all__ = [
    "PlanningScenario",
    "RiskPolicy",
    "build_planning_scenarios",
    "plan_risk_aware_adaptive",
]
