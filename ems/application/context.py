"""Typed boundary for application dependencies and runtime state.

The context is deliberately a passive dataclass: it owns no lifecycle and does not alter
control decisions.  ``create_app`` remains the composition root while services can depend on a
single explicit object instead of a growing list of closure variables.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ApplicationContext:
    """Dependencies shared by application services and HTTP adapters."""

    source: Any
    controller: Any = None
    recorder: Any = None
    freshness: Any = None
    settings: dict[str, Any] = field(default_factory=dict)
    repositories: Any = None
    storage: Any = None
    # Named mutable holders make lifecycle/runtime state explicit without imposing a new model on
    # the existing control loop.  They are shared by reference and are safe for tests to replace.
    runtime_state: dict[str, Any] = field(default_factory=dict)
    control_state: dict[str, Any] = field(default_factory=dict)
    background_tasks: dict[str, Any] = field(default_factory=dict)

