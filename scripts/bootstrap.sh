#!/usr/bin/env bash
#
# One-line bootstrap. Run this single command on your Mac:
#
#   curl -fsSL https://raw.githubusercontent.com/jeroenniesen/EnergyManagementSystem/main/scripts/bootstrap.sh | bash
#
# It downloads the app to ~/EnergyManagementSystem (override with EMS_DIR=...) and runs the
# installer, which builds everything, starts the app, and prints the URL. Everything else — your
# devices, prices, location, tokens — is configured afterwards in the web UI.
#
# Extra installer flags pass straight through, e.g. run in the foreground:
#   curl -fsSL .../bootstrap.sh | bash -s -- --foreground
set -euo pipefail

REPO_URL="https://github.com/jeroenniesen/EnergyManagementSystem"
TARGET="${EMS_DIR:-$HOME/EnergyManagementSystem}"
BRANCH="main"

say() { printf '\033[1;36m▸ %s\033[0m\n' "$*"; }

if command -v git >/dev/null 2>&1; then
  if [ -d "$TARGET/.git" ]; then
    say "Updating the existing checkout in ${TARGET}…"
    git -C "$TARGET" pull --ff-only
  else
    say "Cloning into ${TARGET}…"
    git clone "$REPO_URL.git" "$TARGET"
  fi
else
  # No git (rare on macOS) — fetch a snapshot tarball instead. Your data dir (ems/data) is never in
  # the tarball, so re-running this preserves your configuration.
  say "git not found — downloading a snapshot into ${TARGET}…"
  mkdir -p "$TARGET"
  curl -fsSL "$REPO_URL/archive/refs/heads/$BRANCH.tar.gz" \
    | tar -xz -C "$TARGET" --strip-components=1
fi

cd "$TARGET"
say "Running the installer…"
exec ./scripts/install.sh "$@"
