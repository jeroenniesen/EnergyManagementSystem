# Task 5 Report: App Store Polish, Xcode App Target, Validation Evidence

## What I implemented

- Added `ServerDiscovery` in `ios/EMSControl/Sources/EMSControlCore/ServerDiscovery.swift` with:
  - `normalizedManualURL(_:)`
  - `parsePairingPayload(_:)`
  - explicit rejection of embedded token fields, URL credentials, and token-like query items
- Added discovery coverage in `ios/EMSControl/Tests/EMSControlCoreTests/ServerDiscoveryTests.swift`.
- Updated `DemoDataStore` so demo fixtures load in both Swift Package and Xcode target builds.
- Created a real iOS app project:
  - `ios/EMSControl/project.yml`
  - generated `ios/EMSControl/EMSControl.xcodeproj`
- Added app metadata and assets:
  - `ios/EMSControl/Sources/EMSControlApp/Info.plist`
  - `ios/EMSControl/Sources/EMSControlApp/Assets.xcassets/...`
  - local-network usage text present
  - ATS local-network allowance present
  - no camera permission added
- Added `ios/EMSControl/README.md`.
- Added validation notes in `docs/ios-validation/iteration-5-app-store-polish.md`.
- Fixed one pre-existing SwiftUI compile issue required to make the app target build:
  - `ios/EMSControl/Sources/EMSControlApp/ChatView.swift` had a missing `return` in `disabledReason`.

## TDD RED/GREEN evidence

### RED

After creating `ios/EMSControl/Tests/EMSControlCoreTests/ServerDiscoveryTests.swift` and before adding production code, I ran:

```bash
cd ios/EMSControl
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test
```

Result: failed as expected because `ServerDiscovery` and `ServerDiscoveryError` did not exist yet.

Key failure lines:

```text
error: cannot find 'ServerDiscovery' in scope
error: cannot find type 'ServerDiscoveryError' in scope
```

### GREEN

After implementing `ServerDiscovery` and fixing payload decoding, I reran:

```bash
cd ios/EMSControl
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test
```

Result: passed.

Key passing lines:

```text
Executed 26 tests, with 0 failures (0 unexpected)
```

## Build and validation evidence

### Required simulator attempt

Attempted:

```bash
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild -project ios/EMSControl/EMSControl.xcodeproj -scheme EMSControl -destination 'platform=iOS Simulator,name=iPhone 17,OS=26.5' build
```

Result: blocked by machine-local simulator services before a usable simulator destination could be used.

Key blocker lines:

```text
CoreSimulatorService connection became invalid.
Unable to locate device set
Failed to initialize simulator device set.
```

I also attempted:

```bash
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcrun simctl list devices available
```

Result: same blocker, no available destination list returned.

### Best available app-target build evidence

Because simulator services were unavailable, I validated the Xcode app target with a generic iOS build instead:

```bash
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild -project ios/EMSControl/EMSControl.xcodeproj -scheme EMSControl -destination 'generic/platform=iOS' -derivedDataPath /private/tmp/emscontrol-derived CODE_SIGNING_ALLOWED=NO build
```

Result: passed.

Key passing line:

```text
** BUILD SUCCEEDED **
```

This build compiled the SwiftUI app sources and produced:

```text
/tmp/emscontrol-derived/Build/Products/Debug-iphoneos/EMSControl.app
```

## Screenshot evidence

- Not captured.
- Exact blocker: CoreSimulator services were unavailable on this machine, so no simulator could be booted for screenshots.

## Verification commands run

- `cd ios/EMSControl && DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test`
- `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild -project ios/EMSControl/EMSControl.xcodeproj -scheme EMSControl -destination 'platform=iOS Simulator,name=iPhone 17,OS=26.5' build`
- `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcrun simctl list devices available`
- `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild -project ios/EMSControl/EMSControl.xcodeproj -scheme EMSControl -destination 'generic/platform=iOS' -derivedDataPath /private/tmp/emscontrol-derived CODE_SIGNING_ALLOWED=NO build`
- `git diff --check -- ios/EMSControl docs/ios-validation/iteration-5-app-store-polish.md`

## Files changed

- Added: `ios/EMSControl/Sources/EMSControlCore/ServerDiscovery.swift`
- Modified: `ios/EMSControl/Sources/EMSControlCore/DemoDataStore.swift`
- Added: `ios/EMSControl/Tests/EMSControlCoreTests/ServerDiscoveryTests.swift`
- Added: `ios/EMSControl/Sources/EMSControlApp/Info.plist`
- Added: `ios/EMSControl/Sources/EMSControlApp/Assets.xcassets/Contents.json`
- Added: `ios/EMSControl/Sources/EMSControlApp/Assets.xcassets/AccentColor.colorset/Contents.json`
- Added: `ios/EMSControl/Sources/EMSControlApp/Assets.xcassets/AppIcon.appiconset/Contents.json`
- Added: `ios/EMSControl/Sources/EMSControlApp/Assets.xcassets/AppIcon.appiconset/*.png`
- Modified: `ios/EMSControl/Sources/EMSControlApp/ChatView.swift`
- Added: `ios/EMSControl/README.md`
- Added: `ios/EMSControl/project.yml`
- Added: `ios/EMSControl/EMSControl.xcodeproj/...`
- Added: `docs/ios-validation/iteration-5-app-store-polish.md`

## Concerns

- Superseded by the controller follow-up below: simulator access became available with escalation, the simulator build/install/launch path was verified, and the first-launch screenshot was captured.
- A dashboard/chat walkthrough screenshot was not captured because this pass only used command-line simulator install/launch/screenshot, not UI automation for tapping **View Demo**.

## Controller follow-up

- Root cause: the app target compiled but was not installable because `Info.plist` omitted `CFBundleIdentifier`; after that fix, the installed app exposed missing demo fixtures because the Xcode app target did not copy `Resources/*.json`.
- Fixes:
  - Added standard bundle identity/version keys, `UILaunchScreen`, and supported orientation keys to `Sources/EMSControlApp/Info.plist`.
  - Added `Resources` to the app target in `project.yml` and regenerated `EMSControl.xcodeproj`.
  - Updated `DemoDataStore` to fall back from the framework bundle to `Bundle.main` outside Swift Package builds.
- Verification:
  - `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test` passed 26 tests, 0 failures.
  - `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild -project ios/EMSControl/EMSControl.xcodeproj -scheme EMSControl -destination 'platform=iOS Simulator,name=iPhone 17,OS=26.5' -derivedDataPath /private/tmp/emscontrol-sim-derived CODE_SIGNING_ALLOWED=NO build` passed.
  - `xcrun simctl install` and `xcrun simctl launch ... com.jeroenniesen.emscontrol` passed on iPhone 17 / iOS 26.5.
  - Captured `docs/ios-validation/iteration-5-iphone-first-launch.png`; the app renders first launch with visible **View Demo** and no missing fixture error.
