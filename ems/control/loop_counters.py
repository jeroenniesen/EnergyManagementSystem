"""Thin since-boot control-loop counters for soak diagnostics (#179).

In-memory only — surfaced on ``GET /api/diagnostics`` and as ``control.cycle_summary``
log lines. **Not** Prometheus, ``/metrics``, Grafana, or OpenTelemetry.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Outcomes we keep a dedicated breakdown for (issue #179).
_OUTCOME_KEYS = ("idempotent", "dry_run", "fail_safe", "applied")


@dataclass
class LoopCounters:
    """Process-local counters for one EMS process (reset on restart)."""

    cycles: int = 0
    cycle_latency_ms_sum: float = 0.0
    cycle_latency_ms_last: float | None = None
    stale_sensor_cycles: int = 0
    http_retries: int = 0
    mode_applies: int = 0
    validator_rejects: int = 0
    outcomes: dict[str, int] = field(
        default_factory=lambda: {k: 0 for k in _OUTCOME_KEYS}
    )
    last_outcome: str | None = None
    _pending_fail_safe: bool = False

    @property
    def cycle_latency_ms_avg(self) -> float | None:
        if self.cycles <= 0:
            return None
        return self.cycle_latency_ms_sum / self.cycles

    def reset(self) -> None:
        self.cycles = 0
        self.cycle_latency_ms_sum = 0.0
        self.cycle_latency_ms_last = None
        self.stale_sensor_cycles = 0
        self.http_retries = 0
        self.mode_applies = 0
        self.validator_rejects = 0
        self.outcomes = {k: 0 for k in _OUTCOME_KEYS}
        self.last_outcome = None
        self._pending_fail_safe = False

    def incr_http_retries(self, n: int = 1) -> None:
        self.http_retries += max(0, int(n))

    def incr_validator_rejects(self, n: int = 1) -> None:
        self.validator_rejects += max(0, int(n))
        # A §8.11 reject forces self-consumption — count as fail-safe on the next record_cycle.
        self._pending_fail_safe = True

    def record_cycle(
        self,
        *,
        latency_ms: float | None = None,
        outcome: str | None = None,
        fail_safe: bool = False,
        stale_sensors: bool = False,
    ) -> None:
        """Record one completed control tick (hermetic / operational)."""
        self.cycles += 1
        if latency_ms is not None:
            ms = float(latency_ms)
            self.cycle_latency_ms_last = ms
            self.cycle_latency_ms_sum += ms
        if stale_sensors:
            self.stale_sensor_cycles += 1
        if outcome == "applied":
            self.mode_applies += 1
            self.outcomes["applied"] += 1
        elif outcome == "idempotent":
            self.outcomes["idempotent"] += 1
        elif outcome == "dry_run":
            self.outcomes["dry_run"] += 1
        if fail_safe or self._pending_fail_safe:
            self.outcomes["fail_safe"] += 1
            self._pending_fail_safe = False
        self.last_outcome = outcome

    def snapshot(self) -> dict[str, object]:
        avg = self.cycle_latency_ms_avg
        return {
            "cycles": self.cycles,
            "cycle_latency_ms_last": self.cycle_latency_ms_last,
            "cycle_latency_ms_avg": None if avg is None else round(avg, 1),
            "stale_sensor_cycles": self.stale_sensor_cycles,
            "http_retries": self.http_retries,
            "mode_applies": self.mode_applies,
            "validator_rejects": self.validator_rejects,
            "outcomes": dict(self.outcomes),
            "last_outcome": self.last_outcome,
        }


# Process singleton — same pattern as ``ems.perf.REGISTRY``.
LOOP_COUNTERS = LoopCounters()
