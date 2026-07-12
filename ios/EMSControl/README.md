# EMS Control iOS

Native SwiftUI iOS app for the Energy Management System.

## First Launch

- Enter a LAN or VPN EMS URL such as `http://192.168.1.20:8080`.
- Or choose Demo mode to inspect the app without a server.
- QR pairing payloads use JSON such as `{"base_url":"http://ems.local:8080","server_label":"Home EMS"}`.
- Tokens are entered separately and are never embedded in QR payloads.

## App Store Review

Use Demo mode from first launch. It shows synthetic data and does not require a private EMS server.

## Home-screen widget (B-59)

The `EMSWidget` app-extension target adds a WidgetKit widget (systemSmall + systemMedium) showing
battery SoC, the verdict word (Charging / Self-use / Holding) with a LIVE/WATCHING dot, and — on
medium — the status headline and the next planned car-charge window.

- **Shared config:** on a successful connect the app mirrors `{baseURL, token}` into the App Group
  `group.com.jeroenniesen.emscontrol` (`AppGroupConfigStore` in `EMSControlCore`). The widget reads
  it to reach the same server; with no config it shows "Open EMS to connect". The token lives in
  app-group `UserDefaults` rather than the Keychain — a deliberate tradeoff for a LAN-only app (see
  the comment in `WidgetSupport.swift`). Both targets carry the App Group entitlement (`project.yml`).
- **Refresh:** one timeline entry every 20 minutes; a failed fetch (5 s timeout) falls back to the
  last good data shown "as of HH:mm".
- **Live Activity is OUT OF SCOPE for v1.** A car-charge Live Activity would need push-to-start via
  APNs (a push key + server-side ActivityKit push), which this app does not have. When that lands,
  add an `ActivityConfiguration` to `EMSWidgetBundle.swift` and start it from the EMS server.

After adding/removing files under `Sources/EMSWidget`, regenerate the project:

```bash
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer /opt/homebrew/bin/xcodegen generate
```

## Local Validation

Run Swift package tests with the Xcode toolchain:

```bash
cd ios/EMSControl
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test
```

Plain `swift test` may select Command Line Tools on this machine and fail to resolve the expected SDK/toolchain.

Build the app target:

```bash
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild -project ios/EMSControl/EMSControl.xcodeproj -scheme EMSControl -destination 'platform=iOS Simulator,name=iPhone 17,OS=26.5' build
```
