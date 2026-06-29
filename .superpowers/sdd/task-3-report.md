# Task 3 Report: iOS API Client, Dashboard Store, and Live Dashboard View

## What I implemented

- Added `APIClient` with:
  - `fetchDashboard() async throws -> DashboardSnapshot`
  - bearer token authorization support
  - HTTP status validation
  - API version compatibility check for `/api/dashboard`
- Added `HTTPTransport` and `URLSessionTransport` so the client can be tested without live network calls.
- Added `DashboardStore` with:
  - `refresh() async`
  - `useDemo()`
  - `forgetServer()`
  - observable state for `snapshot`, `isLoading`, `isStale`, and `lastError`
- Added package-level SwiftUI shell sources:
  - `EMSControlApp.swift`
  - `AppShellView.swift`
  - `ConnectionView.swift`
  - `DashboardView.swift`
- Added iteration validation notes at `docs/ios-validation/iteration-3-dashboard-notes.md`.

## TDD evidence

### RED

I wrote the new tests first:

- `ios/EMSControl/Tests/EMSControlCoreTests/APIClientTests.swift`
- `ios/EMSControl/Tests/EMSControlCoreTests/DashboardStoreTests.swift`

Then I ran:

```bash
cd ios/EMSControl
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test
```

Observed failure evidence before implementation:

- `cannot find type 'HTTPTransport' in scope`
- `cannot find 'DashboardStore' in scope`
- `cannot find 'APIClient' in scope`
- `cannot find 'APIClientError' in scope`

This was the expected RED state from the brief.

### GREEN

After implementing the client and store, the first green attempt exposed a Swift 6 concurrency issue:

- `sending 'client' risks causing data races`

I fixed that by making the transport/client path `Sendable`, then re-ran the same command successfully.

Final GREEN verification:

```bash
cd ios/EMSControl
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test
```

Result:

- 9 tests executed
- 0 failures
- 0 unexpected failures

## Tests and results

- `cd ios/EMSControl && DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test`
  - Passed
- `git diff --check -- ios/EMSControl docs/ios-validation/iteration-3-dashboard-notes.md`
  - Passed with no output

## Files changed

- `ios/EMSControl/Sources/EMSControlCore/APIClient.swift`
- `ios/EMSControl/Sources/EMSControlCore/DashboardStore.swift`
- `ios/EMSControl/Sources/EMSControlApp/EMSControlApp.swift`
- `ios/EMSControl/Sources/EMSControlApp/AppShellView.swift`
- `ios/EMSControl/Sources/EMSControlApp/ConnectionView.swift`
- `ios/EMSControl/Sources/EMSControlApp/DashboardView.swift`
- `ios/EMSControl/Tests/EMSControlCoreTests/APIClientTests.swift`
- `ios/EMSControl/Tests/EMSControlCoreTests/DashboardStoreTests.swift`
- `docs/ios-validation/iteration-3-dashboard-notes.md`

## Commit created

- `db881e1` `feat: add iOS dashboard client foundation`

## Self-review

- Confirmed the implementation stayed within Task 3 ownership for code changes and the required validation note.
- Confirmed TDD order: tests were added before production code and the missing-type RED failure was captured first.
- Confirmed the API client handles authorization and incompatible future API versions exactly as requested.
- Confirmed the store preserves a stale snapshot on refresh failure and clears state on `forgetServer()`.
- Confirmed the required verification commands were run successfully after implementation.

## Concerns

- The SwiftUI app shell sources were added as requested, but they are not part of a package target yet, so `swift test` validates the core package and tests only. The validation note already reflects that simulator-level validation is deferred until the future app target work.

---

## Task 3 review fix pass

### What changed

- Reworked the Task 3 SwiftUI shell source to use `EMSTheme.dark` tokens instead of default SwiftUI surfaces for the app background, connection flow, and core dashboard telemetry cards.
- Removed `.regularMaterial` from telemetry cards and replaced it with opaque EMS panel styling, while keeping the lighter material treatment only on the non-critical demo badge.
- Updated `DashboardStore.useDemo()` to clear the live `client` so demo mode is isolated from prior live connection state.
- Added `DashboardStore.loadDemo()` so demo loading failures are captured in `lastError` instead of being dropped by `try?`, and switched `ConnectionView` to that path.
- Updated the iteration note to record the exact Xcode toolchain command and current validation scope.

### Tests run and exact result

- `cd ios/EMSControl && DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test`
  - Passed: 11 tests, 0 failures, 0 unexpected failures.
- `git diff --check -- ios/EMSControl docs/ios-validation/iteration-3-dashboard-notes.md`
  - Passed: no output.

### Files changed

- `ios/EMSControl/Sources/EMSControlCore/DashboardStore.swift`
- `ios/EMSControl/Sources/EMSControlApp/AppShellView.swift`
- `ios/EMSControl/Sources/EMSControlApp/ConnectionView.swift`
- `ios/EMSControl/Sources/EMSControlApp/DashboardView.swift`
- `ios/EMSControl/Tests/EMSControlCoreTests/DashboardStoreTests.swift`
- `docs/ios-validation/iteration-3-dashboard-notes.md`

### Self-review

- The live/demo state boundary is now explicit in the store, and the new tests cover both client clearing and captured demo-load failure.
- The UI changes stay within Task 3 source files and apply the EMS palette consistently to critical surfaces without introducing new product scope.
- The remaining limitation is unchanged: the app shell source is still not attached to a build target, so visual validation remains source-level until Task 5.

---

## Task 3 review fix pass 2

### What changed

- Added `ServerAddressValidator` in `EMSControlCore` so manual connection URLs are constrained to first-iteration local/private scope before a client is created.
- Covered the validator with new package tests for accepted local/private hosts and rejected obvious public internet hosts.
- Updated `ConnectionView` to use the validator, surface validation errors inline, and mark the URL field with the EMS error color when validation fails.
- Removed the forced dark-mode override from `AppShellView` and switched `AppShellView`, `ConnectionView`, and `DashboardView` to select `EMSTheme.dark` or `EMSTheme.light` from `colorScheme`.
- Expanded `docs/ios-validation/iteration-3-dashboard-notes.md` with the exact Connection and Dashboard source inspection performed for this pass.

### Tests run and exact result

- `cd ios/EMSControl && DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test`
  - Passed: 13 tests, 0 failures, 0 unexpected failures.
- `git diff --check -- ios/EMSControl docs/ios-validation/iteration-3-dashboard-notes.md`
  - Passed: no output.

### Files changed

- `ios/EMSControl/Sources/EMSControlCore/ServerAddressValidator.swift`
- `ios/EMSControl/Sources/EMSControlApp/AppShellView.swift`
- `ios/EMSControl/Sources/EMSControlApp/ConnectionView.swift`
- `ios/EMSControl/Sources/EMSControlApp/DashboardView.swift`
- `ios/EMSControl/Tests/EMSControlCoreTests/ServerAddressValidatorTests.swift`
- `docs/ios-validation/iteration-3-dashboard-notes.md`

### Self-review

- The local/VPN-only rule is enforced in reusable core code, not duplicated in the view, and the tests cover both allowed and disallowed host classes called out by review.
- The visible error state lives in `ConnectionView` so rejected public hosts fail before any live request is attempted.
- Theme selection is now source-correct for light and dark appearance without changing the current package-target limitation; Task 5 still needs the real app target for compiled UI validation and screenshots.

---

## Task 3 review fix pass 3

### What changed

- Restricted single-label server hosts to an explicit allowlist in `ServerAddressValidator`.
- Kept `localhost` plus the EMS-style local names `ems`, `ems-vpn`, and `home-ems` allowed.
- Added test coverage for the newly accepted friendly names and for rejected arbitrary single-label hosts such as `example` and `google`.

### Tests and results

- `cd ios/EMSControl && DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test`
  - Passed: 13 tests, 0 failures, 0 unexpected failures.
- `git diff --check -- ios/EMSControl docs/ios-validation/iteration-3-dashboard-notes.md`
  - Passed: no output.

### Files changed

- `ios/EMSControl/Sources/EMSControlCore/ServerAddressValidator.swift`
- `ios/EMSControl/Tests/EMSControlCoreTests/ServerAddressValidatorTests.swift`
