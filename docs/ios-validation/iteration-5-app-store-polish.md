# Iteration 5 App Store Polish Validation

## Scope

- Added a native Xcode app target for `EMSControl`.
- Set the app bundle identifier to `com.jeroenniesen.emscontrol`.
- Added Local Network usage copy and ATS local-network allowance.
- Added `ServerDiscovery` parsing with explicit token rejection for QR payloads.
- Kept camera permission out of the app because this iteration only parses pairing payloads and does not implement QR camera scanning UI.

## Verification commands

1. `cd ios/EMSControl && DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test`
2. `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild -project ios/EMSControl/EMSControl.xcodeproj -scheme EMSControl -destination '<actual simulator destination>' build`
3. `git diff --check -- ios/EMSControl docs/ios-validation/iteration-5-app-store-polish.md`

## Results

- Swift package tests passed with the Xcode toolchain.
- Simulator-specific `xcodebuild` passed with:
  `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild -project ios/EMSControl/EMSControl.xcodeproj -scheme EMSControl -destination 'platform=iOS Simulator,name=iPhone 17,OS=26.5' -derivedDataPath /private/tmp/emscontrol-sim-derived CODE_SIGNING_ALLOWED=NO build`
- The simulator build installed and launched on iPhone 17 / iOS 26.5.
- A generic iOS app-target compilation also passed with:
  `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild -project ios/EMSControl/EMSControl.xcodeproj -scheme EMSControl -destination 'generic/platform=iOS' -derivedDataPath /private/tmp/emscontrol-derived CODE_SIGNING_ALLOWED=NO build`
- `git diff --check -- ios/EMSControl docs/ios-validation/iteration-5-app-store-polish.md` passed.

## Expected reviewer path

- Launch the app.
- Use **View Demo** on first launch to inspect the dashboard and chat flow without a private EMS server.
- For live validation, enter a LAN or VPN server URL manually.
- QR pairing payloads may provide only `base_url` and optional `server_label`; tokens must be entered separately.

## Evidence

- Swift package test output and `xcodebuild` output are recorded in the Task 5 report.
- First-launch simulator screenshot: `docs/ios-validation/iteration-5-iphone-first-launch.png`.
- The first-launch screenshot shows the app-rendered connection sheet and visible **View Demo** path without fixture-loading errors.
