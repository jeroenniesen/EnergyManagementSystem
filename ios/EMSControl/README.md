# EMS Control iOS

Native SwiftUI iOS app for the Energy Management System.

## First Launch

- Enter a LAN or VPN EMS URL such as `http://192.168.1.20:8080`.
- Or choose Demo mode to inspect the app without a server.
- QR pairing payloads use JSON such as `{"base_url":"http://ems.local:8080","server_label":"Home EMS"}`.
- Tokens are entered separately and are never embedded in QR payloads.

## App Store Review

Use Demo mode from first launch. It shows synthetic data and does not require a private EMS server.

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
