# Task 4 Report: iOS Chat Store and Chat View

## What I implemented

- Added `ChatStore` in `ios/EMSControl/Sources/EMSControlCore/ChatStore.swift` with the required public interface:
  - `loadFAQ()`
  - `send(question:)`
  - `clearSession()`
- Added chat-related API methods to `ios/EMSControl/Sources/EMSControlCore/APIClient.swift`:
  - `fetchExplainer()`
  - `fetchFAQ()`
  - `sendChat(question:)`
- Swapped the placeholder Chat tab in `ios/EMSControl/Sources/EMSControlApp/AppShellView.swift` to use `ChatView()`.
- Added `ios/EMSControl/Sources/EMSControlApp/ChatView.swift` with:
  - FAQ rendering under "Quick answers"
  - in-memory message list under "Messages"
  - input field and send button
  - `.task { await store.loadFAQ() }`
- Added validation notes in `docs/ios-validation/iteration-4-chat-notes.md`.

## TDD RED/GREEN evidence

### RED

After creating `ios/EMSControl/Tests/EMSControlCoreTests/ChatStoreTests.swift` and before any production code changes, I ran:

```bash
cd ios/EMSControl
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test
```

Result: failed as expected because `ChatStore` did not exist yet.

Key failure lines:

```text
error: cannot find 'ChatStore' in scope
error: type 'Equatable' has no member 'user'
error: type 'Equatable' has no member 'assistant'
```

### GREEN

After implementing the minimal production changes, I reran:

```bash
cd ios/EMSControl
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test
```

Result: passed.

Key passing lines:

```text
Test Suite 'All tests' passed
Executed 16 tests, with 0 failures (0 unexpected)
```

## Tests and results

- `cd ios/EMSControl && DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test`
  - Passed
- `git diff --check -- ios/EMSControl docs/ios-validation/iteration-4-chat-notes.md`
  - Passed

## Files changed

- Modified: `ios/EMSControl/Sources/EMSControlCore/APIClient.swift`
- Created: `ios/EMSControl/Sources/EMSControlCore/ChatStore.swift`
- Modified: `ios/EMSControl/Sources/EMSControlApp/AppShellView.swift`
- Created: `ios/EMSControl/Sources/EMSControlApp/ChatView.swift`
- Created: `ios/EMSControl/Tests/EMSControlCoreTests/ChatStoreTests.swift`
- Created: `docs/ios-validation/iteration-4-chat-notes.md`

## Self-review

- Verified the implementation matches the task brief exactly for the required interfaces and view wiring.
- Kept changes scoped to the Task 4 ownership boundaries only.
- Confirmed empty questions are ignored, demo chat appends a user and assistant message, and `clearSession()` clears both messages and FAQ state.
- Confirmed the privacy note is consistent with the implementation: chat state is memory-only in `ChatStore`.

## Concerns

- `fetchExplainer()` is added per brief but is not yet consumed by `ChatStore` or `ChatView` in Task 4.
- `ChatView.swift` and `AppShellView.swift` are edited as requested, but those app shell sources are still not attached to an app target until Task 5, so runtime UI validation is deferred to that task.

---

## 2026-06-29 Task 4 Review-Fix Report

### Scope handled

- Task 4 only in the isolated worktree.
- Kept changes inside `ios/EMSControl` chat client/store/views/tests plus `docs/ios-validation/iteration-4-chat-notes.md`.
- Did not expand into Task 5 shell/build/screenshot work beyond wiring the Chat tab to the existing shared dashboard session.

### Review findings fixed

1. Wired the Chat tab to the shared live EMS session:
   - `AppShellView` now owns one `ChatStore`.
   - `ChatView` reads the shared `DashboardStore` environment and synchronizes `ChatStore` from the same `APIClient` / demo snapshot path the dashboard uses.
   - Live sessions call `/api/explainer`, `/api/faq`, and `/api/chat`; demo sessions use bundled fixtures only.
2. Connected explainer state:
   - `ChatStore` now stores `ExplainerStatus`.
   - Free-form send is gated by `ExplainerStatus.active`.
   - `ChatView` surfaces AI active/FAQ-only/demo status, explainer mode, and language.
3. Added explicit Demo labeling on the Chat screen.
4. Replaced the default `List` / `.secondary` / `.roundedBorder` presentation with the EMS palette and panel styling.
5. Added session reset behavior:
   - chat messages, FAQ items, and explainer state clear on live-server switch, demo/live switch, and disconnect.
6. Added transport-level tests:
   - `/api/chat` JSON body encoding and auth header.
   - `/api/faq` and `/api/explainer` request paths and auth headers.

### TDD evidence

#### RED

After extending the chat/API tests for shared-session and explainer behavior, I ran:

```bash
cd ios/EMSControl
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test
```

The run failed as expected before production changes. Key failures were missing `ChatStore` session APIs and explainer state members:

```text
error: value of type 'ChatStore' has no member 'updateSession'
error: value of type 'ChatStore' has no member 'explainerStatus'
error: value of type 'ChatStore' has no member 'isDemoMode'
```

#### GREEN

After implementing the store/view/demo-data changes and transport assertions, the same command passed:

```text
Test Suite 'All tests' passed
Executed 22 tests, with 0 failures (0 unexpected)
```

### Verification commands run

- `cd ios/EMSControl && DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test`
- `git diff --check -- ios/EMSControl docs/ios-validation/iteration-4-chat-notes.md`

Both passed.

### Files changed

- Modified: `ios/EMSControl/Sources/EMSControlApp/AppShellView.swift`
- Modified: `ios/EMSControl/Sources/EMSControlApp/ChatView.swift`
- Modified: `ios/EMSControl/Sources/EMSControlCore/ChatStore.swift`
- Modified: `ios/EMSControl/Sources/EMSControlCore/DemoDataStore.swift`
- Added: `ios/EMSControl/Resources/demo-explainer.json`
- Modified: `ios/EMSControl/Tests/EMSControlCoreTests/APIClientTests.swift`
- Modified: `ios/EMSControl/Tests/EMSControlCoreTests/ChatStoreTests.swift`
- Modified: `docs/ios-validation/iteration-4-chat-notes.md`

### Remaining concern

- `swift test` covers the core package only. The SwiftUI app shell sources changed as required for Task 4, but full app-target build/integration verification remains part of Task 5.

Controller fix after review:
- Root cause: loadLiveSession used async let for explainer and FAQ while the test transport returned queued responses, so request ordering could swap and decode the wrong payload.
- Fix: load explainer then FAQ sequentially during chat session bootstrap.
- Verification: DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test in ios/EMSControl passed 22 tests, 0 failures; git diff --check -- ios/EMSControl docs/ios-validation/iteration-4-chat-notes.md passed with no output.
