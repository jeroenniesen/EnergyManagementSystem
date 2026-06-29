#!/usr/bin/env bash
#
# One-command installer for the Smart Energy Manager on a Mac (Apple Silicon, e.g. Mac Mini M5).
#
#   git clone https://github.com/jeroenniesen/EnergyManagementSystem.git
#   cd EnergyManagementSystem
#   ./scripts/install.sh
#
# Then open http://localhost:8080 and configure everything in the UI (devices, prices, location,
# tokens, AI key). No credentials live in this repo — you add them through the web interface.
#
# What it does (idempotent, no sudo, no system-wide changes):
#   1. installs `uv` (user-local) if missing — manages Python 3.12 for you
#   2. uses your Node if present (>=18), else downloads a repo-local Node just for the build
#   3. builds the React dashboard and syncs the Python environment
#   4. installs a LaunchAgent so the app starts on login and restarts if it crashes
#   5. starts it now and prints the URL
#
# Flags: --foreground (run in this terminal, skip the LaunchAgent)   --no-start (set up only)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
NODE_VERSION="v22.11.0"
LABEL="com.jeroenniesen.ems"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PORT="$(grep -E '^[[:space:]]*port:' config.yaml | head -1 | grep -oE '[0-9]+' || echo 8080)"
FOREGROUND=0; START=1
for a in "$@"; do case "$a" in --foreground) FOREGROUND=1;; --no-start) START=0;; esac; done

say() { printf '\033[1;36m▸ %s\033[0m\n' "$*"; }
die() { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || say "Note: this installer is tuned for macOS; continuing anyway."

# 1. uv (Python toolchain) -----------------------------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv (Python package/Python-version manager)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
# Make uv visible in this shell whether it landed in ~/.local/bin or ~/.cargo/bin.
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
command -v uv >/dev/null 2>&1 || die "uv install failed — see https://docs.astral.sh/uv/"
UV="$(command -v uv)"
say "uv: $UV"

# 2. Node (only needed to build the dashboard) ---------------------------------------------------
node_major() { node -v 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/'; }
if command -v node >/dev/null 2>&1 && [ "$(node_major)" -ge 18 ] 2>/dev/null; then
  say "Using existing Node $(node -v)"
else
  arch="$(uname -m)"; [ "$arch" = "arm64" ] && narch="arm64" || narch="x64"
  os="darwin"; [ "$(uname -s)" = "Linux" ] && os="linux"
  pkg="node-${NODE_VERSION}-${os}-${narch}"
  if [ ! -x ".tools/$pkg/bin/node" ]; then
    say "Downloading a local Node ($NODE_VERSION $os-$narch) just for the build…"
    mkdir -p .tools
    curl -LsSf "https://nodejs.org/dist/${NODE_VERSION}/${pkg}.tar.gz" | tar -xz -C .tools
  fi
  export PATH="$REPO/.tools/$pkg/bin:$PATH"
  say "Using local Node $(node -v)"
fi

# 3. Build the dashboard + sync the Python env ---------------------------------------------------
say "Building the dashboard (React/Vite)…"
( cd ems/web/frontend && npm ci --no-audit --no-fund && npm run build )
say "Syncing the Python environment (uv will fetch Python 3.12 if needed)…"
"$UV" sync
mkdir -p ems/data   # SQLite history + UI settings live here (gitignored)

START_CMD=("$UV" run uvicorn ems.main:app --host 0.0.0.0 --port "$PORT")

if [ "$START" = "0" ]; then
  say "Setup complete (not started). Run: ${START_CMD[*]}"; exit 0
fi

if [ "$FOREGROUND" = "1" ]; then
  say "Starting in the foreground on http://localhost:$PORT  (Ctrl-C to stop)…"
  exec "${START_CMD[@]}"
fi

# 4. LaunchAgent — auto-start on login, restart on crash -----------------------------------------
say "Installing the auto-start service (LaunchAgent)…"
mkdir -p "$HOME/Library/LaunchAgents" "$REPO/ems/data"
NODE_PATH_ENTRY=""; case ":$PATH:" in *":$REPO/.tools/"*) NODE_PATH_ENTRY="$(dirname "$(command -v node)"):";; esac
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>ProgramArguments</key>
  <array>
    <string>$UV</string><string>run</string><string>uvicorn</string>
    <string>ems.main:app</string>
    <string>--host</string><string>0.0.0.0</string>
    <string>--port</string><string>$PORT</string>
    <string>--no-access-log</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>${NODE_PATH_ENTRY}$HOME/.local/bin:$HOME/.cargo/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>EMS_LOG_FILE</key><string>$REPO/ems/data/server.log</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <!-- App logs go to the size-rotated server.log (EMS_LOG_FILE). launchd captures only pre-logging
       startup/crash output here, which stays tiny (per-request access logging is off). -->
  <key>StandardOutPath</key><string>$REPO/ems/data/server-crash.log</string>
  <key>StandardErrorPath</key><string>$REPO/ems/data/server-crash.log</string>
</dict></plist>
PLIST
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

# 5. Wait for health + report --------------------------------------------------------------------
say "Waiting for the app to come up…"
for _ in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:$PORT/health/live" >/dev/null 2>&1; then
    LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || echo "")"
    printf '\n\033[1;32m✅ Smart Energy Manager is running.\033[0m\n'
    printf '   On this Mac:  http://localhost:%s\n' "$PORT"
    [ -n "$LAN_IP" ] && printf '   On your LAN:  http://%s:%s\n' "$LAN_IP" "$PORT"
    printf '   Next: open it and set up your devices, prices and location in the UI.\n'
    printf '   Logs: %s/ems/data/server.log    Stop/remove: ./scripts/uninstall.sh\n\n' "$REPO"
    exit 0
  fi
  sleep 1
done
die "App did not become healthy in time — check $REPO/ems/data/server.log"
