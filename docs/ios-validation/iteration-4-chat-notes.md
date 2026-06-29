# Iteration 4 Chat Validation

- Command: `cd ios/EMSControl && swift test`
- Result: passing after `ChatStore`, chat API methods, and `ChatView` were added.
- Privacy validation: chat messages are memory-only in `ChatStore` and `clearSession()` removes all message/FAQ state.
