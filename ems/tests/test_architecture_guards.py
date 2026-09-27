"""Architecture guard tests for epic #111 invariants I1 and I2 (#138).

Hermetic AST/import scans over production code under `ems/`. They replace the manual
grep/review checklist from the modular-adapters epic and run in the ordinary pytest suite
(no separate CI step).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EMS_ROOT = _REPO_ROOT / "ems"

# ---------------------------------------------------------------------------
# I1 — only ModeController.decide and CommandExecutionBoundary.apply may call
# battery-driver `.apply(...)`. Explicit allowlist entries carry a reason.
# ---------------------------------------------------------------------------

# Relative paths (under ems/) whose battery-driver `.apply(` calls are the approved write sites.
_I1_ALLOWED_FILES = frozenset({
    "control/mode_controller.py",  # ModeController.decide → driver.apply
    "control/execution.py",        # CommandExecutionBoundary.apply → controller.driver.apply
})

# (relative path under ems/, exact source line stripped, reason). New call sites must be added
# here with a reason — or, preferably, routed through the two allowed files above.
_I1_ALLOWLIST: frozenset[tuple[str, str, str]] = frozenset({
    (
        "control/service.py",
        "else bool(self._controller.driver.apply(PhysicalMode.AUTO))",
        "Defensive fallback when CommandExecutionBoundary is absent on the startup "
        "safe-AUTO path; production always constructs the boundary when a controller "
        "exists. Cleanup tracked under epic #111 (follow-up after #138).",
    ),
})

# ---------------------------------------------------------------------------
# I2 — adapters under ems.sources.* must not import control / validator / settings.
# Existing violations (none today) go here with an issue reference that will remove them.
# ---------------------------------------------------------------------------

# (relative path under ems/sources/, imported module prefix, reason + issue).
_I2_ALLOWLIST: frozenset[tuple[str, str, str]] = frozenset()

_I2_FORBIDDEN_PREFIXES = (
    "ems.control",
    "ems.planner.validator",
    "ems.settings",
)


def _iter_ems_py_files(*, under: Path | None = None) -> list[Path]:
    """Production `.py` files under ems/ (or a subdir), excluding tests and caches."""
    root = under or _EMS_ROOT
    out: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        rel_parts = path.relative_to(_EMS_ROOT).parts
        if not rel_parts:
            continue
        if rel_parts[0] == "tests" or "__pycache__" in rel_parts:
            continue
        out.append(path)
    return out


def _call_source_line(path: Path, node: ast.Call) -> str:
    """Exact source text of the call's first line (for stable allowlist matching)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not node.lineno or node.lineno > len(lines):
        return ""
    return lines[node.lineno - 1].strip()


def _is_battery_driver_apply(node: ast.AST) -> bool:
    """True for any `*driver.apply(...)` call (Name or Attribute ending in ``driver``)."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr != "apply":
        return False
    value = node.func.value
    if isinstance(value, ast.Name):
        return value.id.endswith("driver")
    if isinstance(value, ast.Attribute):
        return value.attr.endswith("driver")
    return False


def test_i1_battery_driver_apply_only_via_allowed_sites():
    """I1: no battery-driver `.apply(` outside mode_controller / execution (+ allowlist)."""
    violations: list[str] = []
    allow_hits: set[tuple[str, str, str]] = set()

    for path in _iter_ems_py_files():
        rel = path.relative_to(_EMS_ROOT).as_posix()
        if rel in _I1_ALLOWED_FILES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not _is_battery_driver_apply(node):
                continue
            line = _call_source_line(path, node)
            matched = next(
                (entry for entry in _I1_ALLOWLIST if entry[0] == rel and entry[1] == line),
                None,
            )
            if matched is not None:
                allow_hits.add(matched)
                continue
            violations.append(f"{rel}:{node.lineno}: {line}")

    assert not violations, (
        "I1 violated: battery-driver .apply() outside ModeController.decide / "
        "CommandExecutionBoundary.apply (and allowlist):\n  - " + "\n  - ".join(violations)
    )
    stale = _I1_ALLOWLIST - allow_hits
    assert not stale, (
        "I1 allowlist entries no longer match any call site — remove or update them:\n  - "
        + "\n  - ".join(f"{p}: {line} ({reason})" for p, line, reason in sorted(stale))
    )


def _imported_modules(tree: ast.AST, package: str) -> set[str]:
    """Absolute names of imported modules, incl. `from pkg import mod` and relative imports."""
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")
                # level=1 → current package; level=2 → parent; etc.
                parent = parts[: len(parts) - (node.level - 1)]
                base = ".".join(parent + ([base] if base else []))
            if base:
                imported.add(base)
            imported.update(
                f"{base}.{alias.name}" if base else alias.name for alias in node.names
            )
    return imported


def _is_forbidden_import(module: str) -> bool:
    for prefix in _I2_FORBIDDEN_PREFIXES:
        if module == prefix or module.startswith(prefix + "."):
            return True
    return False


def test_i2_sources_do_not_import_control_validator_or_settings():
    """I2: ems.sources.* must not import ems.control, ems.planner.validator, or ems.settings."""
    sources_root = _EMS_ROOT / "sources"
    violations: list[str] = []
    allow_hits: set[tuple[str, str, str]] = set()

    for path in _iter_ems_py_files(under=sources_root):
        rel = path.relative_to(_EMS_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        package = ".".join(("ems", *path.relative_to(_EMS_ROOT).parent.parts))
        for module in sorted(_imported_modules(tree, package)):
            if not _is_forbidden_import(module):
                continue
            matched = next(
                (
                    entry
                    for entry in _I2_ALLOWLIST
                    if entry[0] == rel and (module == entry[1] or module.startswith(entry[1] + "."))
                ),
                None,
            )
            if matched is not None:
                allow_hits.add(matched)
                continue
            violations.append(f"{rel} imports {module}")

    assert not violations, (
        "I2 violated: ems.sources.* imports control/validator/settings "
        "(add an allowlist entry with an issue reference, or remove the import):\n  - "
        + "\n  - ".join(violations)
    )
    stale = _I2_ALLOWLIST - allow_hits
    assert not stale, (
        "I2 allowlist entries no longer match any import — remove or update them:\n  - "
        + "\n  - ".join(f"{p}: {mod} ({reason})" for p, mod, reason in sorted(stale))
    )


@pytest.mark.parametrize("rel", sorted(_I1_ALLOWED_FILES))
def test_i1_allowed_files_exist(rel: str):
    assert (_EMS_ROOT / rel).is_file(), f"I1 allowed file missing: ems/{rel}"


def _first_call(snippet: str) -> ast.Call:
    tree = ast.parse(snippet)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            return node
    raise AssertionError(f"no Call in snippet: {snippet!r}")


@pytest.mark.parametrize(
    "snippet",
    [
        "driver.apply(mode)",
        "self.driver.apply(mode)",
        "controller_driver.apply(mode)",
        "wiring.controller_driver.apply(mode)",
        "self._driver.apply(mode)",
    ],
)
def test_i1_helper_flags_driver_named_apply_forms(snippet: str):
    """Prove I1 catches *driver.apply forms that previously slipped past exact-name matching."""
    assert _is_battery_driver_apply(_first_call(snippet))


@pytest.mark.parametrize(
    "snippet",
    [
        "super().apply(mode)",
        "m.apply(db)",
        "boundary.apply(mode)",
        "self._execution.apply(mode)",
    ],
)
def test_i1_helper_ignores_non_driver_apply_forms(snippet: str):
    assert not _is_battery_driver_apply(_first_call(snippet))


@pytest.mark.parametrize(
    ("snippet", "package", "forbidden"),
    [
        ("from ems.control import mode_controller", "ems.sources", "ems.control"),
        ("import ems.settings", "ems.sources", "ems.settings"),
        ("from ems.planner import validator", "ems.sources", "ems.planner.validator"),
        ("from ems import settings", "ems.sources", "ems.settings"),
        ("from ..control import mode_controller", "ems.sources", "ems.control"),
        ("from ...control import mode_controller", "ems.sources.nested", "ems.control"),
    ],
)
def test_i2_helper_flags_forbidden_import_forms(snippet: str, package: str, forbidden: str):
    """Prove I2 catches from-pkg-import-mod, from-pkg-import-name, and relative forms."""
    modules = _imported_modules(ast.parse(snippet), package)
    assert any(
        m == forbidden or m.startswith(forbidden + ".") for m in modules
    ), f"{snippet!r} under {package!r} → {sorted(modules)}; expected {forbidden}"


@pytest.mark.parametrize(
    ("snippet", "package"),
    [
        ("from ems.planner import economics", "ems.sources"),
        ("from .ports import BatteryDriver", "ems.sources"),
        ("from ems.domain import PhysicalMode", "ems.sources"),
        ("import ems.domain", "ems.sources"),
    ],
)
def test_i2_helper_allows_benign_imports(snippet: str, package: str):
    modules = _imported_modules(ast.parse(snippet), package)
    assert not any(_is_forbidden_import(m) for m in modules), (
        f"{snippet!r} under {package!r} unexpectedly forbidden: {sorted(modules)}"
    )
