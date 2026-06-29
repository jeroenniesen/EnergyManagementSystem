# Iteration 4 Chat Validation

- Command: `cd ios/EMSControl && DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test`
- Result: passing after the chat flow was rewired to the shared EMS session and the transport tests were extended.
- Live wiring validation: `ChatView` now derives session state from the shared `DashboardStore` client/snapshot path, and `ChatStore.updateSession(client:mode:)` reloads `/api/explainer` and `/api/faq` for live sessions instead of staying on bundled demo data.
- Explainer-state validation: free-form chat is now gated by `ExplainerStatus.active`, and the Chat header surfaces mode plus language so the screen reflects whether AI chat is active, FAQ-only, or demo-backed.
- Session reset validation: `ChatStore` clears messages and FAQ state when switching between live servers, demo mode, or disconnected state so stale conversation does not bleed across sessions.
- Demo validation: the Chat screen now shows an explicit `Demo` badge and uses bundled explainer/FAQ/chat fixtures only for demo mode.
- Styling validation: the Chat screen now uses the EMS panel/background/line/text palette rather than the default `List` and `.roundedBorder` styling.
- Privacy validation: chat messages remain memory-only in `ChatStore`, and `clearSession()` removes message, FAQ, and explainer state.
