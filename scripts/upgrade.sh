#!/usr/bin/env bash
#
# Upgrade a RUNNING install to the latest version. One line on your Mac:
#
#   curl -fsSL https://raw.githubusercontent.com/jeroenniesen/EnergyManagementSystem/main/scripts/upgrade.sh | bash
#
# It finds your install (~/EnergyManagementSystem, or $EMS_DIR), pulls the latest code, rebuilds the
# dashboard, syncs the Python env, and restarts the service. Your configuration and history in
# ems/data are preserved (they're never tracked, so a pull/build can't touch them).
#
# Extra installer flags pass through, e.g.:  curl … | bash -s -- --foreground
set -euo pipefail

TARGET="${EMS_DIR:-$HOME/EnergyManagementSystem}"
BRANCH="main"
REPO_URL="https://github.com/jeroenniesen/EnergyManagementSystem"
say() { printf '\033[1;36m▸ %s\033[0m\n' "$*"; }
die() { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

[ -d "$TARGET" ] || die "No install found at $TARGET.
  Install first:  curl -fsSL $REPO_URL/raw/$BRANCH/scripts/bootstrap.sh | bash
  Or point at it: EMS_DIR=/path/to/EnergyManagementSystem curl … | bash"

cd "$TARGET"
if [ -d .git ] && command -v git >/dev/null 2>&1; then
  say "Pulling the latest code into ${TARGET}…"
  git pull --ff-only
else
  say "Downloading the latest snapshot into $TARGET (preserving ems/data)…"
  curl -fsSL "$REPO_URL/archive/refs/heads/$BRANCH.tar.gz" \
    | tar -xz -C "$TARGET" --strip-components=1
fi

# install.sh is idempotent: it rebuilds the dashboard, re-syncs the Python env, and reloads the
# auto-start service (= restart). ems/data is left untouched.
say "Rebuilding + restarting…"
exec ./scripts/install.sh "$@"
