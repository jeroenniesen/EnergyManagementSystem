#!/usr/bin/env bash
# Stop + remove the auto-start service. Leaves your data (ems/data) and the checkout intact.
set -euo pipefail
LABEL="com.jeroenniesen.ems"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
OS="$(uname -s)"
if [ "$OS" = "Darwin" ] && [ -f "$PLIST" ]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "✓ Stopped and removed the LaunchAgent. Your data in ems/data is untouched."
elif [ "$OS" = "Linux" ] && command -v systemctl >/dev/null 2>&1; then
  SERVICE_NAME="smart-energy-manager.service"
  systemctl --user disable --now "$SERVICE_NAME" 2>/dev/null || true
  rm -f "$HOME/.config/systemd/user/$SERVICE_NAME"
  systemctl --user daemon-reload 2>/dev/null || true
  echo "✓ Stopped and removed the systemd user service. Your data in ems/data is untouched."
else
  echo "No auto-start service installed (nothing to remove)."
fi
