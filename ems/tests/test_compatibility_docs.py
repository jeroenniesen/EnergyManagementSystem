"""Guards for GitHub issue #78 / B-41 — compatibility & positioning docs page.

Hermetic file checks only (no network, no devices). Product/control code is untouched.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
README = REPO_ROOT / "README.md"
COMPAT = REPO_ROOT / "docs" / "compatibility.md"

# Klaar-als: page names battery, meters, tariffs, forecast providers, EV support.
REQUIRED_HEADINGS = (
    "## Battery",
    "## Smart meters",
    "## Tariffs & electricity prices",
    "## Solar forecast providers",
    "## EV (advice only)",
)

# Honest limitations / untested hardware must be explicit somewhere on the page.
REQUIRED_PHRASES = (
    "Indevolt",
    "HomeWizard",
    "Tibber",
    "Forecast.Solar",
    "Solcast",
    "advice only",
    "Untested",
    "Not supported",
)


def test_readme_links_compatibility_page():
    """Klaar-als: docs/compatibility.md is linked from README."""
    text = README.read_text(encoding="utf-8")
    assert "docs/compatibility.md" in text, "README must link docs/compatibility.md"
    assert COMPAT.is_file(), "docs/compatibility.md must exist"


def test_compatibility_page_covers_required_topics():
    """Klaar-als: page names battery, meters, tariffs, forecast providers, and EV."""
    text = COMPAT.read_text(encoding="utf-8")
    missing_headings = [h for h in REQUIRED_HEADINGS if h not in text]
    assert not missing_headings, f"compatibility.md missing headings: {missing_headings}"
    missing_phrases = [p for p in REQUIRED_PHRASES if p not in text]
    assert not missing_phrases, f"compatibility.md missing required phrases: {missing_phrases}"
