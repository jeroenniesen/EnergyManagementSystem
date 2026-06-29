# Task 2 Report: iOS Core Package, Models, Theme, and Demo Fixtures

## What I implemented

Implemented the Swift package and core demo surface under `ios/EMSControl`:

- Added `Package.swift` for `EMSControlCore` with processed resources.
- Added model support in `Sources/EMSControlCore/Models.swift`:
  - `DashboardSnapshot`
  - `SectionState`
  - `FlexibleSection`
  - `FAQResponse`
  - `FAQItem`
  - `ChatRequest`
  - `ChatResponse`
  - `ExplainerStatus`
  - `JSONValue`
  - `DynamicCodingKey`
  - `JSONDecoder.ems` / `JSONEncoder.ems`
- Added `Sources/EMSControlCore/Theme.swift` with `HexColor` and `EMSTheme.dark` / `EMSTheme.light`.
- Added `Sources/EMSControlCore/DemoDataStore.swift` to load demo JSON fixtures from the package bundle.
- Added demo fixtures:
  - `Resources/demo-dashboard.json`
  - `Resources/demo-faq.json`
  - `Resources/demo-chat.json`
- Added tests:
  - `Tests/EMSControlCoreTests/ModelsTests.swift`
  - `Tests/EMSControlCoreTests/ThemeTests.swift`
  - `Tests/EMSControlCoreTests/DemoDataStoreTests.swift`

## TDD RED/GREEN evidence

### RED

I wrote the tests before the production source files.

First red run:

```bash
cd ios/EMSControl
swift test
```

Result:

- Failed before test execution because the target had no Swift sources yet.
- Error observed: `public headers ("include") directory path for 'EMSControlCore' is invalid or not contained in the target`

This confirmed the package was not implemented yet.

### GREEN progression

After adding the implementation, I ran the package tests again.

Plain `swift test` on this machine uses `/Library/Developer/CommandLineTools/usr/bin/swift`, which does not expose `XCTest` for this package-test flow, so it failed with:

- `no such module 'XCTest'`

I then verified the package with the full Xcode toolchain:

```bash
cd ios/EMSControl
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test
```

Intermediate failures caught by tests:

1. `DemoDataStore(bundle: .module)` could not compile as a public default argument.
2. `EMSTheme.dark` and `EMSTheme.light` needed `Sendable`-safe value types under Swift 6.
3. `DashboardSnapshot.cacheTTLSeconds` did not decode from `cache_ttl_seconds` with acronym casing.

Final green run result:

- Build succeeded.
- `ModelsTests` passed.
- `ThemeTests` passed.
- `DemoDataStoreTests` passed.
- Total: 3 tests passed, 0 failures.

## Tests and results

Commands run:

1. `cd ios/EMSControl && swift test`
   - RED, expected package-not-implemented failure.
2. `cd ios/EMSControl && swift test`
   - Environment/toolchain failure on this machine: `no such module 'XCTest'`.
3. `cd ios/EMSControl && DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test`
   - Exposed real code/test failures.
4. `cd ios/EMSControl && DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test`
   - GREEN, all 3 tests passed.
5. `git diff --check -- ios/EMSControl`
   - Passed.

## Files changed

- `ios/EMSControl/Package.swift`
- `ios/EMSControl/Sources/EMSControlCore/Models.swift`
- `ios/EMSControl/Sources/EMSControlCore/Theme.swift`
- `ios/EMSControl/Sources/EMSControlCore/DemoDataStore.swift`
- `ios/EMSControl/Resources/demo-dashboard.json`
- `ios/EMSControl/Resources/demo-faq.json`
- `ios/EMSControl/Resources/demo-chat.json`
- `ios/EMSControl/Tests/EMSControlCoreTests/ModelsTests.swift`
- `ios/EMSControl/Tests/EMSControlCoreTests/ThemeTests.swift`
- `ios/EMSControl/Tests/EMSControlCoreTests/DemoDataStoreTests.swift`

## Self-review

- Scope stayed within `ios/EMSControl` as required.
- The public API matches the brief.
- The implementation is intentionally small and fixture-focused, appropriate for downstream iOS tasks.
- The only non-brief adjustment in code was to make the package compile cleanly under Swift 6:
  - `DemoDataStore` now uses `init(bundle: Bundle? = nil)` and falls back to `.module` internally.
  - `HexColor` and `EMSTheme` conform to `Sendable`.
  - `DashboardSnapshot` uses explicit `Codable` handling so `cache_ttl_seconds` maps to the required `cacheTTLSeconds` property.

## Concerns

- On this machine, the exact `swift test` command resolves to the Command Line Tools Swift toolchain and fails to import `XCTest`.
- The package itself is green when run with the Xcode developer dir:
  - `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test`

---

## Review Fix Follow-up

### What changed

- Added explicit `public init(...)` methods to `ChatRequest`, `ChatResponse`, `FAQResponse`, `FAQItem`, and `ExplainerStatus` so app targets outside `EMSControlCore` can construct them.
- Switched the package tests to `import EMSControlCore` where appropriate and added `testPublicModelsCanBeConstructedByExternalConsumers()` to prove the public model surface is constructible without `@testable`.
- Broadened `ThemeTests` to assert all 10 dark tokens and all 10 light tokens from the spec.
- Kept the change scoped to `ios/EMSControl`.

### Tests run and exact result

1. `cd ios/EMSControl && DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test`
   - RED before the model fix: compile failed in `ModelsTests` because external consumers could not call memberwise initializers on `FAQItem`, `FAQResponse`, `ChatRequest`, `ChatResponse`, and `ExplainerStatus`.
2. `cd ios/EMSControl && DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test`
   - GREEN after the model fix: `5 tests, 0 failures`.
3. `git diff --check -- ios/EMSControl`
   - Passed with no output.

### Files changed

- `ios/EMSControl/Sources/EMSControlCore/Models.swift`
- `ios/EMSControl/Tests/EMSControlCoreTests/ModelsTests.swift`
- `ios/EMSControl/Tests/EMSControlCoreTests/ThemeTests.swift`
- `ios/EMSControl/Tests/EMSControlCoreTests/DemoDataStoreTests.swift`

### Self-review

- The reviewer’s visibility finding was valid; the explicit public initializers now make the public model types usable from external targets.
- The new constructability test exercises the public import boundary directly, which is the gap the earlier tests missed.
- Theme coverage now matches the full light and dark token set without changing theme implementation.
- No unrelated model or package code was rewritten.
