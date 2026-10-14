#!/usr/bin/env bash
# Generate App Store screenshots (6.9" iPhone) from demo mode (#259).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT="$REPO_ROOT/ios/EMSControl/EMSControl.xcodeproj"
SCHEME="EMSControl"
OUTPUT_DIR="${EMS_SCREENSHOT_OUTPUT:-$REPO_ROOT/ios/EMSControl/build/app-store-screenshots}"
SIM_NAME="EMS App Store 6.9"
# 6.9" display class — mandatory App Store size (Pro Max).
DEVICE_TYPES=("iPhone 16 Pro Max" "iPhone 17 Pro Max" "iPhone 15 Pro Max")

if [[ -d /Applications/Xcode.app/Contents/Developer ]]; then
  export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
elif [[ -n "${DEVELOPER_DIR:-}" && -d "$DEVELOPER_DIR" ]]; then
  :
else
  echo "error: Xcode.app not found; set DEVELOPER_DIR to a full Xcode developer directory" >&2
  exit 1
fi

pick_runtime() {
  xcrun simctl list runtimes available -j \
    | python3 -c '
import json, sys
runtimes = json.load(sys.stdin).get("runtimes", [])
ios = [r for r in runtimes if r.get("isAvailable") and "iOS" in r.get("name", "")]
if not ios:
    sys.exit("no available iOS simulator runtime")
ios.sort(key=lambda r: r.get("version", ""), reverse=True)
print(ios[0]["identifier"])
'
}

find_or_create_sim() {
  local runtime_id="$1"
  local udid
  udid="$(xcrun simctl list devices available -j \
    | python3 -c "
import json, sys
name = sys.argv[1]
data = json.load(sys.stdin)
for devs in data.get(\"devices\", {}).values():
    for d in devs:
        if d.get(\"name\") == name and d.get(\"isAvailable\"):
            print(d[\"udid\"])
            sys.exit(0)
sys.exit(1)
" "$SIM_NAME" 2>/dev/null || true)"
  if [[ -n "$udid" ]]; then
    echo "$udid"
    return
  fi
  local dt new_udid
  for dt in "${DEVICE_TYPES[@]}"; do
    new_udid="$(xcrun simctl create "$SIM_NAME" "$dt" "$runtime_id" 2>/dev/null)" && echo "$new_udid" && return
  done
  echo "error: could not create a 6.9\" Pro Max simulator (tried: ${DEVICE_TYPES[*]})" >&2
  exit 1
}

RUNTIME_ID="$(pick_runtime)"
SIM_UDID="$(find_or_create_sim "$RUNTIME_ID")"
BOOTED_BY_SCRIPT=0

cleanup() {
  if [[ "$BOOTED_BY_SCRIPT" -eq 1 ]]; then
    xcrun simctl status_bar "$SIM_UDID" clear 2>/dev/null || true
    xcrun simctl shutdown "$SIM_UDID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

mkdir -p "$OUTPUT_DIR"
rm -f "$OUTPUT_DIR"/*.png

if xcrun simctl list devices booted 2>/dev/null | grep -qE '(Booted)'; then
  echo "error: another simulator is already booted; shut it down first (shared machine rule)" >&2
  exit 1
fi

xcrun simctl boot "$SIM_UDID" 2>/dev/null || true
BOOTED_BY_SCRIPT=1
xcrun simctl bootstatus "$SIM_UDID" -b

xcrun simctl status_bar "$SIM_UDID" override \
  --time "9:41" \
  --batteryState charged \
  --batteryLevel 100 \
  --cellularMode active \
  --cellularBars 4 \
  --wifiBars 3

xcodebuild \
  -project "$PROJECT" \
  -scheme "$SCHEME" \
  -destination "platform=iOS Simulator,id=$SIM_UDID" \
  -only-testing:EMSControlUITests/DemoModeScreenshotsUITests/testDemoModeScreenshotsMainTabs \
  test

xcrun simctl status_bar "$SIM_UDID" clear

echo "Screenshots in $OUTPUT_DIR"
for png in "$OUTPUT_DIR"/*.png; do
  [[ -f "$png" ]] || continue
  dims="$(sips -g pixelWidth -g pixelHeight "$png" 2>/dev/null | awk '/pixelWidth|pixelHeight/ {print $2}' | tr '\n' x | sed 's/x$//')"
  echo "  $(basename "$png"): ${dims}"
  case "$dims" in
    1320x2868|1290x2796) ;;
    *)
      echo "error: unexpected size $dims for $(basename "$png") (expected 1320x2868 or 1290x2796)" >&2
      exit 1
      ;;
  esac
done

count="$(find "$OUTPUT_DIR" -maxdepth 1 -name '*.png' | wc -l | tr -d ' ')"
if [[ "$count" -lt 5 ]]; then
  echo "error: expected 5 PNGs, found $count" >&2
  exit 1
fi
