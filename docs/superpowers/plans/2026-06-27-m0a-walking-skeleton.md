# M0a Walking Skeleton — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A runnable, fully-tested read-only walking skeleton of the EMS — it reconstructs house load from (mock) meter data per SPEC §4 and serves it at `/api/status`, runnable on a Mac with no hardware (dev/mock mode).

**Architecture:** Python 3.12 + FastAPI service. Sources are behind a `Source` port (ports/adapters, SOLID); a `MockSource` feeds synthetic data so the app runs with no Home Assistant/battery (SPEC §11.6 dev mode). The energy model (`load_model.py`) is the correctness core — pure functions, sign conventions fixed (SPEC §4.1), house load is *reconstructed* (`grid + solar + battery`), never read from one meter. All datetime work is tz-aware (SPEC §13.1).

**Tech Stack:** Python 3.12 (managed by `uv`), FastAPI, Uvicorn, PyYAML, pytest, ruff, httpx (TestClient), Docker (runnable container). React/Vite UI is M0b — not in this plan.

## Global Constraints

- Python **3.12** (SPEC §4 / CLAUDE.md). Provisioned via `uv` — do not rely on system Python.
- **Fail-safe / read-only:** this milestone performs **no writes** to any device; `dry_run` is hard-true.
- **Sign conventions are fixed** (SPEC §4.1): `grid_power_w` + import/− export; `solar_power_w` ≥ 0; `battery_power_w` + discharge/− charge; `ev_power_w` ≥ 0; `soc_pct` 0–100.
- **House load is reconstructed**, never read from one meter: `house_load_w = grid_power_w + solar_power_w + battery_power_w` (SPEC §4.2).
- **No naive datetimes** in any logic (SPEC §13.1 / §4.7).
- **KISS / YAGNI / TDD:** smallest design that satisfies the slice; test-first; frequent commits.
- Package root is `ems/`; tests in `ems/tests/` (SPEC §13 module tree). Web UI port **8080**.

---

### Task 0: Project scaffolding & toolchain

**Files:**
- Create: `pyproject.toml`, `ems/__init__.py`, `ems/tests/__init__.py`, `ruff.toml`
- Modify: `.gitignore`

**Interfaces:**
- Produces: a `uv`-managed Python 3.12 env; `uv run pytest` and `uv run ruff check` work; git repo initialised.

- [ ] **Step 1: Initialise git** (repo is not yet a git repo)

```bash
cd /Users/jeroenniesen/Development/EnergyManagementSystem
git init
git add -A && git commit -m "chore: docs baseline (SPEC, GOAL, docs)"
```

- [ ] **Step 2: Install uv (no system-Python mutation) and Python 3.12**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv python install 3.12
```

- [ ] **Step 3: Create `pyproject.toml`**

```toml
[project]
name = "ems"
version = "0.0.1"
description = "Smart Energy Manager (HEMS)"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "httpx>=0.27", "ruff>=0.4"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
testpaths = ["ems/tests"]
addopts = "-q"

[tool.hatch.build.targets.wheel]
packages = ["ems"]
```

- [ ] **Step 4: Create `ruff.toml`**

```toml
line-length = 100
target-version = "py312"
[lint]
select = ["E", "F", "I", "UP", "B"]
```

- [ ] **Step 5: Create package files + update `.gitignore`**

```bash
mkdir -p ems/tests ems/sources ems/web
touch ems/__init__.py ems/tests/__init__.py ems/sources/__init__.py ems/web/__init__.py
```

Append to `.gitignore`:
```
.venv/
__pycache__/
*.pyc
.pytest_cache/
/data/
```

- [ ] **Step 6: Sync env and verify the toolchain**

Run:
```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
```
Expected: pytest reports "no tests ran"; ruff passes (no errors).

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "chore: python 3.12 + uv scaffolding, pyproject, ruff"
```

---

### Task 1: Timezone-aware 15-minute slot utilities (`timeutil.py`)

**Files:**
- Create: `ems/timeutil.py`
- Test: `ems/tests/test_timeutil.py`

**Interfaces:**
- Produces:
  - `SLOT_MINUTES: int = 15`
  - `slot_start(dt: datetime, tz: ZoneInfo) -> datetime` — floor a tz-aware dt to its slot start (in `tz`); raises `ValueError` on naive dt.
  - `day_slot_count(day: date, tz: ZoneInfo) -> int` — DST-aware count of 15-min slots in a local calendar day.

- [ ] **Step 1: Write the failing tests**

```python
# ems/tests/test_timeutil.py
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from ems.timeutil import day_slot_count, slot_start

AMS = ZoneInfo("Europe/Amsterdam")


def test_slot_start_floors_to_quarter_hour():
    dt = datetime(2026, 6, 27, 10, 7, 30, tzinfo=AMS)
    assert slot_start(dt, AMS) == datetime(2026, 6, 27, 10, 0, tzinfo=AMS)
    dt2 = datetime(2026, 6, 27, 10, 49, tzinfo=AMS)
    assert slot_start(dt2, AMS) == datetime(2026, 6, 27, 10, 45, tzinfo=AMS)


def test_slot_start_rejects_naive():
    with pytest.raises(ValueError):
        slot_start(datetime(2026, 6, 27, 10, 0), AMS)


def test_slot_start_converts_utc_to_local_slot():
    dt = datetime(2026, 6, 27, 8, 7, tzinfo=timezone.utc)  # 10:07 CEST
    assert slot_start(dt, AMS) == datetime(2026, 6, 27, 10, 0, tzinfo=AMS)


def test_day_slot_counts_dst_amsterdam():
    assert day_slot_count(date(2026, 6, 27), AMS) == 96   # normal
    assert day_slot_count(date(2026, 3, 29), AMS) == 92   # spring forward (23h)
    assert day_slot_count(date(2026, 10, 25), AMS) == 100  # fall back (25h)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest ems/tests/test_timeutil.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ems.timeutil'`.

- [ ] **Step 3: Write the implementation**

```python
# ems/timeutil.py
"""Timezone-aware 15-minute slot utilities (SPEC §13.1). Naive datetimes are rejected."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

SLOT_MINUTES = 15


def slot_start(dt: datetime, tz: ZoneInfo) -> datetime:
    """Floor a tz-aware datetime to the start of its 15-minute slot, expressed in `tz`."""
    if dt.tzinfo is None:
        raise ValueError("naive datetime not allowed (SPEC §13.1)")
    local = dt.astimezone(tz)
    floored = (local.minute // SLOT_MINUTES) * SLOT_MINUTES
    return local.replace(minute=floored, second=0, microsecond=0)


def day_slot_count(day: date, tz: ZoneInfo) -> int:
    """Number of 15-minute slots in the local calendar day `day` (DST-aware: 96/92/100)."""
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    nxt = day + timedelta(days=1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz)
    seconds = (end - start).total_seconds()
    return int(seconds // (SLOT_MINUTES * 60))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest ems/tests/test_timeutil.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add ems/timeutil.py ems/tests/test_timeutil.py
git commit -m "feat: tz-aware 15-min slot utils with DST-correct day counts"
```

---

### Task 2: Domain types (`domain.py`)

**Files:**
- Create: `ems/domain.py`
- Test: `ems/tests/test_domain.py`

**Interfaces:**
- Produces:
  - `class BatteryIntent(str, Enum)`: `ALLOW_SELF_CONSUMPTION`, `GRID_CHARGE_TO_TARGET`, `HOLD_RESERVE`, `DISCHARGE_FOR_LOAD`.
  - `class PlannerMode(str, Enum)`: `RULE_BASED`, `ML`, `ADVISORY`.
  - `@dataclass(frozen=True) class RawSample` with floats `grid_power_w, solar_power_w, battery_power_w, ev_power_w, soc_pct`.

- [ ] **Step 1: Write the failing tests**

```python
# ems/tests/test_domain.py
import dataclasses

import pytest

from ems.domain import BatteryIntent, PlannerMode, RawSample


def test_battery_intent_values():
    assert BatteryIntent.ALLOW_SELF_CONSUMPTION.value == "allow_self_consumption"
    assert {i.value for i in BatteryIntent} == {
        "allow_self_consumption", "grid_charge_to_target", "hold_reserve", "discharge_for_load",
    }


def test_planner_mode_default_is_rule_based():
    assert PlannerMode.RULE_BASED.value == "rule_based"
    assert {m.value for m in PlannerMode} == {"rule_based", "ml", "advisory"}


def test_raw_sample_is_frozen():
    s = RawSample(grid_power_w=200, solar_power_w=0, battery_power_w=800, ev_power_w=0, soc_pct=55)
    assert s.grid_power_w == 200
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.grid_power_w = 1  # type: ignore[misc]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest ems/tests/test_domain.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ems.domain'`.

- [ ] **Step 3: Write the implementation**

```python
# ems/domain.py
"""Core domain types (SPEC §7.1, §13.2). Sign conventions per SPEC §4.1."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BatteryIntent(str, Enum):
    ALLOW_SELF_CONSUMPTION = "allow_self_consumption"
    GRID_CHARGE_TO_TARGET = "grid_charge_to_target"
    HOLD_RESERVE = "hold_reserve"
    DISCHARGE_FOR_LOAD = "discharge_for_load"


class PlannerMode(str, Enum):
    RULE_BASED = "rule_based"
    ML = "ml"
    ADVISORY = "advisory"


@dataclass(frozen=True)
class RawSample:
    """Sign-normalised instantaneous readings (SPEC §4.1)."""
    grid_power_w: float       # + import / - export
    solar_power_w: float      # >= 0 production
    battery_power_w: float    # + discharge / - charge
    ev_power_w: float         # >= 0 charging
    soc_pct: float            # 0..100
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest ems/tests/test_domain.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add ems/domain.py ems/tests/test_domain.py
git commit -m "feat: domain types (BatteryIntent, PlannerMode, RawSample)"
```

---

### Task 3: Energy model — reconstruction, solar clamp, SoC plausibility (`load_model.py`)

**Files:**
- Create: `ems/load_model.py`
- Test: `ems/tests/test_load_model.py`

**Interfaces:**
- Consumes: `ems.domain.RawSample`.
- Produces:
  - `@dataclass(frozen=True) class DerivedSample` with `house_load_w: float`, `non_ev_load_w: float`.
  - `reconstruct(raw: RawSample, ev_charging_threshold_w: float = 200.0) -> DerivedSample` — SPEC §4.2/§4.5.
  - `normalise_solar(raw_solar_w: float) -> float` — clamp negatives to 0 (SPEC §4.7).
  - `is_soc_jump_implausible(prev_soc, new_soc, minutes_elapsed, max_jump_pct_per_5min=20.0) -> bool` — SPEC §4.7.

- [ ] **Step 1: Write the failing tests** (the five consistency cases from energy-model.md §3)

```python
# ems/tests/test_load_model.py
import pytest

from ems.domain import RawSample
from ems.load_model import DerivedSample, is_soc_jump_implausible, normalise_solar, reconstruct


def _raw(grid, solar, batt, ev=0.0, soc=50.0):
    return RawSample(grid_power_w=grid, solar_power_w=solar, battery_power_w=batt, ev_power_w=ev, soc_pct=soc)


@pytest.mark.parametrize(
    "grid,solar,batt,ev,expected_house,expected_non_ev",
    [
        (1000, 0, 0, 0, 1000, 1000),       # grid-only
        (-500, 1500, 0, 0, 1000, 1000),    # solar covers + export
        (200, 0, 800, 0, 1000, 1000),      # battery covers
        (1500, 0, -500, 0, 1000, 1000),    # charging from grid
        (200, 1500, 0, 700, 1700, 1000),   # solar + EV charging
    ],
)
def test_reconstruction_consistency_cases(grid, solar, batt, ev, expected_house, expected_non_ev):
    d = reconstruct(_raw(grid, solar, batt, ev))
    assert d == DerivedSample(house_load_w=expected_house, non_ev_load_w=expected_non_ev)


def test_ev_not_subtracted_below_threshold():
    # EV drawing 100 W (< 200 threshold) is treated as not charging -> not subtracted
    d = reconstruct(_raw(300, 0, 0, ev=100), ev_charging_threshold_w=200.0)
    assert d.house_load_w == 300
    assert d.non_ev_load_w == 300


def test_normalise_solar_clamps_negative():
    assert normalise_solar(-5.0) == 0.0
    assert normalise_solar(1234.0) == 1234.0


def test_soc_jump_plausibility():
    assert is_soc_jump_implausible(50.0, 69.0, 5.0) is False   # 19% in 5 min, ok
    assert is_soc_jump_implausible(50.0, 75.0, 5.0) is True    # 25% in 5 min, implausible
    assert is_soc_jump_implausible(None, 80.0, 5.0) is False   # no prior reading
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest ems/tests/test_load_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ems.load_model'`.

- [ ] **Step 3: Write the implementation**

```python
# ems/load_model.py
"""Energy model: reconstruct house load from raw meters (SPEC §4). P1 is NET GRID, not load."""
from __future__ import annotations

from dataclasses import dataclass

from .domain import RawSample


@dataclass(frozen=True)
class DerivedSample:
    house_load_w: float   # total house demand (SPEC §4.2)
    non_ev_load_w: float  # house load excluding EV charging (what the planner learns)


def reconstruct(raw: RawSample, ev_charging_threshold_w: float = 200.0) -> DerivedSample:
    """house_load = grid + solar + battery; subtract EV only while it is actually charging (§4.5)."""
    house_load = raw.grid_power_w + raw.solar_power_w + raw.battery_power_w
    ev = raw.ev_power_w if raw.ev_power_w > ev_charging_threshold_w else 0.0
    return DerivedSample(house_load_w=house_load, non_ev_load_w=house_load - ev)


def normalise_solar(raw_solar_w: float) -> float:
    """Production is >= 0; clamp negatives to 0 rather than taking magnitude (§4.7)."""
    return max(0.0, raw_solar_w)


def is_soc_jump_implausible(
    prev_soc: float | None,
    new_soc: float,
    minutes_elapsed: float,
    max_jump_pct_per_5min: float = 20.0,
) -> bool:
    """Reject SoC jumps larger than the configured rate (SPEC §4.7)."""
    if prev_soc is None:
        return False
    allowed = max_jump_pct_per_5min * (minutes_elapsed / 5.0)
    return abs(new_soc - prev_soc) > allowed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest ems/tests/test_load_model.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add ems/load_model.py ems/tests/test_load_model.py
git commit -m "feat: energy model — house-load reconstruction + solar clamp + SoC plausibility (SPEC §4)"
```

---

### Task 4: Minimal config loader (`config.py`)

**Files:**
- Create: `ems/config.py`, `config.yaml`
- Test: `ems/tests/test_config.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Config` with `timezone: str`, `dev_mode: str`, `dry_run: bool`, `web_port: int`.
  - `load_config(path: str | Path) -> Config` — reads YAML; applies defaults (`timezone="Europe/Amsterdam"`, `dev_mode="mock"`, `dry_run=True`, `web_port=8080`); `dev_mode in {mock, replay}` forces `dry_run=True` (SPEC §11.6).

- [ ] **Step 1: Write the failing tests**

```python
# ems/tests/test_config.py
from pathlib import Path

from ems.config import Config, load_config


def test_load_defaults_when_minimal(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("site:\n  timezone: Europe/Amsterdam\n")
    cfg = load_config(p)
    assert cfg == Config(timezone="Europe/Amsterdam", dev_mode="mock", dry_run=True, web_port=8080)


def test_dev_mock_forces_dry_run(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("dev:\n  mode: mock\ncontrol:\n  dry_run: false\n")
    cfg = load_config(p)
    assert cfg.dev_mode == "mock"
    assert cfg.dry_run is True  # mock/replay force dry_run regardless of config (SPEC §11.6)


def test_live_mode_respects_dry_run_flag(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("dev:\n  mode: live\ncontrol:\n  dry_run: false\nweb:\n  port: 9000\n")
    cfg = load_config(p)
    assert cfg.dev_mode == "live"
    assert cfg.dry_run is False
    assert cfg.web_port == 9000
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest ems/tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ems.config'`.

- [ ] **Step 3: Write the implementation + a starter `config.yaml`**

```python
# ems/config.py
"""Minimal effective-config loader (SPEC §9). Expanded in later milestones."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Config:
    timezone: str
    dev_mode: str
    dry_run: bool
    web_port: int


def load_config(path: str | Path) -> Config:
    data = yaml.safe_load(Path(path).read_text()) or {}
    site = data.get("site", {}) or {}
    dev = data.get("dev", {}) or {}
    control = data.get("control", {}) or {}
    web = data.get("web", {}) or {}

    dev_mode = dev.get("mode", "mock")
    dry_run = bool(control.get("dry_run", True))
    if dev_mode in ("mock", "replay"):
        dry_run = True  # SPEC §11.6: simulated modes can never write

    return Config(
        timezone=site.get("timezone", "Europe/Amsterdam"),
        dev_mode=dev_mode,
        dry_run=dry_run,
        web_port=int(web.get("port", 8080)),
    )
```

```yaml
# config.yaml (starter — subset of SPEC §9; grows with later milestones)
site:
  timezone: Europe/Amsterdam
dev:
  mode: mock          # mock | replay | live  (mock/replay need no HA/battery and force dry_run)
control:
  dry_run: true
web:
  port: 8080
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest ems/tests/test_config.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add ems/config.py ems/tests/test_config.py config.yaml
git commit -m "feat: minimal config loader; dev mock/replay forces dry_run"
```

---

### Task 5: Source port + MockSource (`sources/base.py`, `sources/mock.py`)

**Files:**
- Create: `ems/sources/base.py`, `ems/sources/mock.py`
- Test: `ems/tests/test_sources.py`

**Interfaces:**
- Consumes: `ems.domain.RawSample`.
- Produces:
  - `class Source(Protocol)` with `def read(self) -> RawSample: ...`
  - `class MockSource` implementing `Source`, returning a deterministic plausible sample (battery-covering case: grid 200, solar 0, battery 800, ev 0, soc 55).

- [ ] **Step 1: Write the failing test**

```python
# ems/tests/test_sources.py
from ems.domain import RawSample
from ems.sources.mock import MockSource


def test_mock_source_returns_plausible_sample():
    s = MockSource().read()
    assert isinstance(s, RawSample)
    # battery-covering steady state: house load = 200 + 0 + 800 = 1000 W
    assert s.grid_power_w + s.solar_power_w + s.battery_power_w == 1000
    assert 0 <= s.soc_pct <= 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest ems/tests/test_sources.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ems.sources.mock'`.

- [ ] **Step 3: Write the implementation**

```python
# ems/sources/base.py
"""The Source port: any telemetry source returns a sign-normalised RawSample (SOLID, SPEC §13)."""
from __future__ import annotations

from typing import Protocol

from ems.domain import RawSample


class Source(Protocol):
    def read(self) -> RawSample: ...
```

```python
# ems/sources/mock.py
"""Deterministic synthetic source for dev/mock mode (SPEC §11.6) — no HA/battery needed."""
from __future__ import annotations

from ems.domain import RawSample


class MockSource:
    def read(self) -> RawSample:
        # Battery-covering steady state: 1000 W house load, solar off, mid SoC.
        return RawSample(
            grid_power_w=200.0,
            solar_power_w=0.0,
            battery_power_w=800.0,
            ev_power_w=0.0,
            soc_pct=55.0,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest ems/tests/test_sources.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ems/sources/base.py ems/sources/mock.py ems/tests/test_sources.py
git commit -m "feat: Source port + MockSource for dev mode"
```

---

### Task 6: FastAPI app + entrypoint (`web/api.py`, `main.py`)

**Files:**
- Create: `ems/web/api.py`, `ems/main.py`
- Test: `ems/tests/test_api.py`

**Interfaces:**
- Consumes: `ems.sources.base.Source`, `ems.load_model.reconstruct`, `ems.config.Config`.
- Produces:
  - `create_app(source: Source, *, dry_run: bool, dev_mode: str) -> FastAPI` with routes `GET /health/live`, `GET /health/ready`, `GET /api/status`.
  - `ems/main.py` building `app` from `load_config("config.yaml")` + `MockSource`, runnable with uvicorn on the configured port.

- [ ] **Step 1: Write the failing tests**

```python
# ems/tests/test_api.py
from fastapi.testclient import TestClient

from ems.sources.mock import MockSource
from ems.web.api import create_app


def _client():
    return TestClient(create_app(MockSource(), dry_run=True, dev_mode="mock"))


def test_health_live():
    r = _client().get("/health/live")
    assert r.status_code == 200
    assert r.json()["status"] == "alive"


def test_health_ready_reports_mode():
    r = _client().get("/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert body["dev_mode"] == "mock"


def test_status_reconstructs_house_load():
    r = _client().get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["house_load_w"] == 1000  # 200 + 0 + 800 (MockSource)
    assert body["non_ev_load_w"] == 1000
    assert body["soc_pct"] == 55
    assert body["dry_run"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest ems/tests/test_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ems.web.api'`.

- [ ] **Step 3: Write the implementation**

```python
# ems/web/api.py
"""Read-only status API (SPEC §9.1). No device writes in M0a."""
from __future__ import annotations

from fastapi import FastAPI

from ems.load_model import reconstruct
from ems.sources.base import Source


def create_app(source: Source, *, dry_run: bool, dev_mode: str) -> FastAPI:
    app = FastAPI(title="Smart Energy Manager", version="0.0.1")

    @app.get("/health/live")
    def live() -> dict:
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready() -> dict:
        return {"status": "ready", "dry_run": dry_run, "dev_mode": dev_mode}

    @app.get("/api/status")
    def status() -> dict:
        raw = source.read()
        derived = reconstruct(raw)
        return {
            "dry_run": dry_run,
            "dev_mode": dev_mode,
            "soc_pct": raw.soc_pct,
            "grid_power_w": raw.grid_power_w,
            "solar_power_w": raw.solar_power_w,
            "battery_power_w": raw.battery_power_w,
            "house_load_w": derived.house_load_w,
            "non_ev_load_w": derived.non_ev_load_w,
        }

    return app
```

```python
# ems/main.py
"""Entrypoint: build the app from config + a source, run uvicorn."""
from __future__ import annotations

import uvicorn

from ems.config import load_config
from ems.sources.mock import MockSource
from ems.web.api import create_app


def build_app():
    cfg = load_config("config.yaml")
    # M0a: only the mock source exists; live sources arrive with the HA client (later M0a task).
    source = MockSource()
    return create_app(source, dry_run=cfg.dry_run, dev_mode=cfg.dev_mode), cfg


app, _cfg = build_app()


def main() -> None:
    _, cfg = build_app()
    uvicorn.run("ems.main:app", host="0.0.0.0", port=cfg.web_port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests + lint**

Run: `uv run pytest -v && uv run ruff check .`
Expected: ALL PASS (timeutil + domain + load_model + config + sources + api).

- [ ] **Step 5: Commit**

```bash
git add ems/web/api.py ems/main.py ems/tests/test_api.py
git commit -m "feat: read-only status API (/health, /api/status) + entrypoint"
```

---

### Task 7: Runnable container (`Dockerfile`, `docker-compose.dev.yml`)

**Files:**
- Create: `Dockerfile`, `docker-compose.dev.yml`

**Interfaces:**
- Produces: `docker compose -f docker-compose.dev.yml up` serves the status API on `http://localhost:8080`.

- [ ] **Step 1: Create `Dockerfile`** (lean Python 3.12 image; frontend build stage added in M0b)

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml ./
RUN uv pip install --system fastapi "uvicorn[standard]" pyyaml
COPY ems ./ems
COPY config.yaml ./config.yaml
EXPOSE 8080
CMD ["uvicorn", "ems.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

- [ ] **Step 2: Create `docker-compose.dev.yml`** (SPEC §11.6 — dev/mock, no HA/battery/GPU)

```yaml
services:
  ems:
    build: .
    ports: ["8080:8080"]
    volumes:
      - ./ems/data:/data
    environment:
      EMS_DEV_MODE: mock
    restart: unless-stopped
```

- [ ] **Step 3: Build and run; verify the live behaviour**

Run:
```bash
docker compose -f docker-compose.dev.yml up --build -d
sleep 3
curl -s http://localhost:8080/health/ready
curl -s http://localhost:8080/api/status
docker compose -f docker-compose.dev.yml down
```
Expected: `/health/ready` → `{"status":"ready","dry_run":true,"dev_mode":"mock"}`; `/api/status` → JSON with `"house_load_w":1000.0`.

- [ ] **Step 4: Commit**

```bash
git add Dockerfile docker-compose.dev.yml
git commit -m "feat: runnable dev container (docker-compose.dev.yml), mock mode on :8080"
```

---

## Roadmap after this plan (separate plans, in build order — SPEC §15)

- **M0a-part-2:** SQLite history store (raw vs derived tables, retention/vacuum), the full `ports.py` (`LoadForecaster`/`Planner`/`Explainer`/`SolarForecaster`/`PriceSource`/`BatteryDriver`), `capabilities.py`, the HA read client + startup validation + `entity_map`, the ownership state machine + boot sequence + startup grace, the runtime settings store, per-signal freshness/alerts. Completes M0a.
- **M0b:** React + Vite dashboard + setup page; Playwright/visual-test harness; freshness indicators; dry-run/live badge. Multi-stage Dockerfile (Node build → copy `dist/`).
- **M0c:** Tibber (cache, quarter↔hourly, completeness) + Solcast/Forecast.Solar (budget ledger, provenance, bounded correction) → 15-min slots; first graphs.
- **M1a/M1b:** Indevolt capability probe (read-only) → battery writes (idempotent + confirmed + restore-original).
- **M2/M3:** winter arbitrage / summer solar planners (dry-run → enable).
- **M4:** polish + 3 global visual-polish passes.
- **M6:** optional ML layer (accelerator-gated) + external-LLM explainer.

---

## Self-Review

**Spec coverage (M0a slice):** sign conventions + reconstruction (Task 3 ✓ SPEC §4.1/§4.2/§4.5), solar clamp + SoC plausibility (Task 3 ✓ §4.7), tz-aware slots + DST counts (Task 1 ✓ §13.1/§6.2), dev/mock mode forcing dry-run (Task 4/5 ✓ §11.6), read-only status API (Task 6 ✓ §9.1), runnable container on :8080 (Task 7 ✓ §11.6). Deferred-and-listed in the roadmap: SQLite store, full ports, HA client, lifecycle/ownership, settings store, freshness/alerts (M0a-part-2) — intentionally out of this slice.

**Placeholder scan:** none — every code/test step has complete content and exact commands.

**Type consistency:** `RawSample` fields (Task 2) are used identically in `reconstruct` (Task 3), `MockSource` (Task 5), and the API (Task 6). `Config` fields (Task 4) match `build_app` usage (Task 6). `Source.read() -> RawSample` (Task 5) matches the `create_app(source, ...)` consumer (Task 6).
