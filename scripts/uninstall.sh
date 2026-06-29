#!/usr/bin/env bash
# Stop + remove the auto-start service. Leaves your data (ems/data) and the checkout intact.
set -euo pipefail
LABEL="com.jeroenniesen.ems"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
if [ -f "$PLIST" ]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "✓ Stopped and removed the LaunchAgent. Your data in ems/data is untouched."
else
  echo "No LaunchAgent installed (nothing to remove)."
fi
