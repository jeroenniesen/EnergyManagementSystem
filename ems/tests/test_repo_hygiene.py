"""Repo-hygiene guards for GitHub issue #72 (slice 1: gitignore + untrack build junk).

Later slices (injectable clock, ``ems/ev`` / ``ems/insights`` packaging) are intentionally
out of scope here — those criteria stay unchecked until their own PRs land.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Patterns that must appear in the root .gitignore (issue #72 Klaar-als #1).
REQUIRED_GITIGNORE_FRAGMENTS = (
    "ios/**/build/",
    "ios/**/.build/",
    "**/xcuserdata/",
)

# Ad-hoc validation screenshots / agent scratch that must not live at the repo root.
FORBIDDEN_ROOT_ARTIFACTS = (
    "behavior-panels.png",
    "ems-dashboard-validation.png",
    "ems-dashboard-mobile-validation.png",
    "task-1-report.md",
)


def _git_ls_files(*pathspecs: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z", "--", *pathspecs],
        check=True,
        capture_output=True,
    )
    if not result.stdout:
        return []
    return [p.decode() for p in result.stdout.split(b"\0") if p]


def test_gitignore_lists_ios_build_artifact_rules():
    """Klaar-als #1: root .gitignore covers ios build / .build / xcuserdata trees."""
    text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    missing = [frag for frag in REQUIRED_GITIGNORE_FRAGMENTS if frag not in text]
    assert not missing, f"root .gitignore missing iOS build rules: {missing}"


def test_no_ios_build_artifacts_are_tracked():
    """Klaar-als #1: ios/**/build, ios/**/.build, and xcuserdata must not be tracked."""
    tracked = _git_ls_files(
        "ios/**/build/**",
        "ios/**/.build/**",
        "**/xcuserdata/**",
    )
    # Also catch odd paths that pathspec might miss (e.g. xcuserdata at unexpected depth).
    all_ios = _git_ls_files("ios/")
    extras = [
        p
        for p in all_ios
        if "/build/" in p or "/.build/" in p or "/xcuserdata/" in p or p.endswith("/xcuserdata")
    ]
    offenders = sorted(set(tracked) | set(extras))
    assert not offenders, (
        f"{len(offenders)} iOS build artifact(s) still tracked (show first 10): "
        f"{offenders[:10]}"
    )


def test_root_validation_artifacts_are_gone():
    """Slice-1 hygiene: validation PNGs and task-1-report.md must not remain at repo root."""
    present_on_disk = [name for name in FORBIDDEN_ROOT_ARTIFACTS if (REPO_ROOT / name).exists()]
    tracked = _git_ls_files(*FORBIDDEN_ROOT_ARTIFACTS)
    assert not present_on_disk, f"forbidden root artifacts still on disk: {present_on_disk}"
    assert not tracked, f"forbidden root artifacts still tracked: {tracked}"
