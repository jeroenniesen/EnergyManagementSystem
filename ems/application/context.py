"""Explicit typed collaborators shared by application services and HTTP adapters."""
from __future__ import annotations

from asyncio import Task
from dataclasses import dataclass, field

from ems.application.protocols import (
    DiagnosticsProvider,
    FinanceProvider,
    PlanProvider,
    PlanValidator,
    ReportProvider,
    SampleProvider,
    SavingsProvider,
    TariffPolicyProvider,
    TariffWarningsProvider,
)
from ems.clock import Clock, SystemClock
from ems.control.mode_controller import ModeController
from ems.freshness import FreshnessTracker
from ems.sense import Recorder
from ems.sources.ports import Source
from ems.storage.context import StorageContext


def require_collaborator[T](collaborator: T | None, name: str) -> T:
    if collaborator is None:
        raise ValueError(f"Application collaborator {name} is not configured")
    return collaborator


@dataclass
class ApplicationContext:
    """Passive dependencies; create_app owns construction and lifecycle."""

    source: Source | None
    controller: ModeController | None = None
    recorder: Recorder | None = None
    freshness: FreshnessTracker | None = None
    settings: dict[str, object] = field(default_factory=dict)
    repositories: StorageContext | None = None
    storage: StorageContext | None = None
    runtime_state: dict[str, object] = field(default_factory=dict)
    control_state: dict[str, object] = field(default_factory=dict)
    background_tasks: dict[str, Task[object]] = field(default_factory=dict)
    clock: Clock = field(default_factory=SystemClock)
    current_plan: PlanProvider | None = None
    current_sample: SampleProvider | None = None
    validate_plan: PlanValidator | None = None
    policy: TariffPolicyProvider | None = None
    tariff_warnings: TariffWarningsProvider | None = None
    report_for_window: ReportProvider | None = None
    finance_window: FinanceProvider | None = None
    savings_snapshot: SavingsProvider | None = None
    diagnostics_snapshot: DiagnosticsProvider | None = None
