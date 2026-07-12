#!/usr/bin/env python3
"""Bundle-size gate for the EMS web dashboard (SPEC §9.1 / B-44).

Stdlib-only (no npm/node dependency, so it runs the same way in CI or
locally): gzips the built SPA's JS entry assets and fails if their
combined gzip size exceeds the budget.

Build the frontend first, then run from the repo root:

    cd ems/web/frontend && npm ci && npm run build && cd ../../..
    python3 scripts/check_bundle_size.py

Budget: initial JS bundle <= 300 KB gzipped (SPEC §9.1: "initial bundle
<= 300 KB gzipped, checked in CI").
"""

from __future__ import annotations

import gzip
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST_ASSETS = REPO_ROOT / "ems" / "web" / "static" / "dist" / "assets"
BUDGET_BYTES = 300 * 1024  # 300 KB gzipped (SPEC §9.1)


def gzip_size(path: Path) -> int:
    return len(gzip.compress(path.read_bytes(), compresslevel=9))


def main() -> int:
    if not DIST_ASSETS.is_dir():
        print(
            f"error: {DIST_ASSETS} not found — build the frontend first "
            "(cd ems/web/frontend && npm ci && npm run build)",
            file=sys.stderr,
        )
        return 2

    js_files = sorted(DIST_ASSETS.glob("*.js"))
    if not js_files:
        print(f"error: no .js assets found under {DIST_ASSETS}", file=sys.stderr)
        return 2

    print("Bundle-size gate (SPEC §9.1: initial JS <= 300 KB gzipped)")
    total = 0
    for path in js_files:
        size = gzip_size(path)
        total += size
        print(f"  {path.name}: {size / 1024:.1f} KB gz (raw {path.stat().st_size / 1024:.1f} KB)")

    budget_kb = BUDGET_BYTES / 1024
    total_kb = total / 1024
    print(f"  total: {total_kb:.1f} KB gz (budget {budget_kb:.0f} KB gz)")

    if total > BUDGET_BYTES:
        print(
            f"FAIL: JS bundle is {total_kb:.1f} KB gz, over the {budget_kb:.0f} KB gz "
            f"budget by {total_kb - budget_kb:.1f} KB",
            file=sys.stderr,
        )
        return 1

    print(f"OK: {total_kb:.1f} KB gz, within the {budget_kb:.0f} KB gz budget")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
