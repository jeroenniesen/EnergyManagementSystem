#!/usr/bin/env bash
# Restart the running app (after changing device/connection settings in the UI, which take effect
# on restart). Most other settings apply instantly and need no restart.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.jeroenniesen.ems"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
OS="$(uname -s)"
PORT="$(grep -E '^[[:space:]]*port:' "$REPO/config.yaml" | head -1 | grep -oE '[0-9]+' || echo 8080)"

if [ "$OS" = "Darwin" ] && [ ! -f "$PLIST" ]; then
  echo "No auto-start service is installed."
  echo "  • If you started it with 'make dev' / --foreground: just Ctrl-C in that terminal and re-run it."
  echo "  • Otherwise run ./scripts/install.sh to set it up."
  exit 1
fi

if [ "$OS" = "Linux" ]; then
  SERVICE_NAME="smart-energy-manager.service"
  if ! systemctl --user restart "$SERVICE_NAME"; then
    echo "No systemd user service is installed. Run ./scripts/install.sh to set it up." >&2
    exit 1
  fi
else
  # Modern macOS: kickstart -k restarts the service in place. Fall back to unload/load if unavailable.
  if ! launchctl kickstart -k "$DOMAIN/$LABEL" 2>/dev/null; then
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
  fi
fi

# Confirm it came back up.
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$PORT/health/live" >/dev/null 2>&1; then
    echo "✓ Restarted — http://localhost:$PORT   (logs: $REPO/ems/data/server.log)"
    exit 0
  fi
  sleep 1
done
echo "Restart issued, but the app isn't answering yet — check $REPO/ems/data/server.log"
