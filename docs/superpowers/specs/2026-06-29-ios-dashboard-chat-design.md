# iOS Dashboard and Chat Design Spec

**Date:** 2026-06-29
**Owner:** Jeroen Niesen
**Status:** Approved direction; ready for implementation planning after review

## Goal

Build a maintainable native iOS app for the Energy Management System that lets a homeowner monitor the system dashboard and use grounded chat from an iPhone on the local network or over the user's own VPN. The first iteration is read-mostly: dashboard, server connection, and chat. It does not add remote internet access, account hosting, push notifications, widgets, or battery control changes.

## Non-Goals for First Iteration

- No cloud relay, hosted login, public internet access, or third-party tunneling.
- No battery write controls beyond viewing the current EMS state and existing advisory chat.
- No Home Assistant integration inside the iOS app.
- No Apple Watch, widgets, Live Activities, Siri, or push notifications.
- No independent planner logic in the iOS app. The server remains the source of truth.

## Current Application Context

The current repository contains a FastAPI backend, SQLite stores, and a React/Vite web dashboard. The backend already exposes granular endpoints for status, freshness, strategy, battery, charge need, energy story, alerts, savings, diagnostics, FAQ, explainer state, and chat. Chat is grounded by server-side context through `/api/chat` and is advisory only.

The backend also has active long-running reliability work: short in-memory live-read coalescing, persistent caches for external data and AI explanations, history retention, diagnostics, and shutdown restore. This matters for mobile because a phone app must not create a second dashboard polling fan-out against the battery, Tibber, Forecast.Solar, or the LLM.

## Product Shape

The app opens into the actual monitoring experience, not a marketing screen. First launch asks the user to connect to the EMS server by scanning the LAN or entering an IP address / base URL. Once connected, the app shows two primary tabs:

1. **Dashboard** - operational overview of the EMS: readiness, dry-run/live state, current intent, SoC, strategy, freshness, plan summary, prices, solar, battery tower summary, savings, alerts, and the latest AI validation state when available.
2. **Chat** - the existing grounded assistant experience: FAQ answers work without AI, and free-form questions use `/api/chat` when the backend says AI chat is active.

The app uses native iOS interaction patterns: tab navigation, pull-to-refresh, swipe-safe scrolling, native Dynamic Type, VoiceOver labels, system alert sheets for connection errors, and system text entry for chat.

## Architecture

Use a native SwiftUI app in `ios/EMSControl` backed by small, testable modules:

- `EMSControlApp` - app entry point and dependency composition.
- `AppShellView` - root navigation and tab structure.
- `ConnectionView` - server scan/manual URL setup and connection validation.
- `DashboardView` - dashboard rendering from one snapshot model.
- `ChatView` - grounded chat UI and FAQ rendering.
- `APIClient` - async HTTP client, token header handling, decoding, retry policy, and cancellation.
- `ServerDiscovery` - LAN scan/manual URL validation; optional Bonjour/mDNS if the backend advertises a service.
- `DashboardStore` - refresh state, TTL handling, offline stale snapshot display, and error state.
- `ChatStore` - message list, pending request state, and send/cancel handling.
- `Theme` - shared color tokens matching the web app palette.
- `Models` - Codable request/response types for dashboard, chat, auth/explainer, FAQ, and errors.

Keep server state authoritative. The iOS app may cache the last dashboard snapshot locally for display while offline, but cached mobile data is never used for control decisions.

## Backend API Contract

Add one consolidated dashboard snapshot endpoint before or alongside the iOS app:

`GET /api/dashboard`

The endpoint returns one coherent read model for dashboard clients:

```json
{
  "generated_at": "2026-06-29T12:00:00+00:00",
  "cache_ttl_seconds": 10,
  "readiness": {},
  "status": {},
  "freshness": {},
  "strategy": {},
  "decision": {},
  "alerts": [],
  "battery": {},
  "charge_need": {},
  "savings": {},
  "energy_story": {},
  "ai_validation": null
}
```

The exact nested schemas should reuse the existing endpoint response shapes where practical. The endpoint is additive: the existing granular web endpoints stay in place for the current web UI and drill-down views.

### Caching Requirements

The dashboard endpoint must be protected by a short server-side snapshot cache. Multiple clients and browser tabs calling `/api/dashboard` inside the TTL must reuse the same computed snapshot. The TTL should default to 10 seconds and be returned to clients as `cache_ttl_seconds`.

The endpoint must not bypass existing source protections:

- Live meter and battery reads stay in the existing short in-memory coalescing window.
- Tibber, Forecast.Solar, and AI explanation caches stay server-owned.
- No mobile client may call many granular dashboard endpoints on a polling loop.
- The iOS app schedules refreshes from `cache_ttl_seconds` and backs off when backgrounded or when the server is unreachable.
- Pull-to-refresh can force a client request, but the server may still return the cached snapshot inside the TTL.

This contract is the main defense against doubled calls from the iOS app or future monitoring devices.

## Existing Chat Contract

Use the existing backend chat endpoints:

- `GET /api/explainer` - whether AI chat is active and what language is configured.
- `GET /api/faq` - deterministic quick answers that work with AI off.
- `POST /api/chat` - free-form grounded question; body `{ "question": "..." }`.

The iOS app must not build its own LLM prompt or contact an LLM provider directly. All grounding, redaction, provider choice, and caching remain server-side.

## Connection and Discovery

First iteration supports:

- Manual server entry: `http://192.168.x.x:8080`, `http://ems.local:8080`, or another LAN/VPN URL.
- Connection validation through `/health/live`, `/health/ready`, and `/api/auth`.
- Secure storage of the base URL and optional web token in Keychain.
- Local Network permission copy that explains the app searches only for the user's EMS server.

LAN scan should be pragmatic:

- Try Bonjour/mDNS when the EMS backend advertises a service.
- Otherwise scan a small subnet range derived from the current Wi-Fi interface with strict timeouts.
- Always keep manual entry available.

VPN access is just a manually entered URL. The app does not create or manage the VPN.

## Authentication and Security

Reuse the backend's existing token model. If `/api/auth` says a token is required, the app prompts for it and stores it in Keychain. The token is sent with the same header contract as the web app's `authHeaders()`.

Security requirements:

- Do not log tokens, full chat questions, server responses, or device identifiers.
- Use App Transport Security defaults for HTTPS. Permit HTTP only for local network addresses and user-entered LAN/VPN hosts needed by this self-hosted app.
- Keep all write/control endpoints out of the first iteration UI.
- Make chat advisory copy clear through UI state, not through repeated instructional text.

## Visual Design

The app must share the web app's color scheme:

| Token | Dark | Light |
|---|---:|---:|
| Background | `#0b0e13` | `#eef1f6` |
| Panel | `#161b23` | `#ffffff` |
| Secondary Panel | `#1e242e` | `#f1f4f9` |
| Line | `#2a313c` | `#e2e7ef` |
| Text | `#e6e9ef` | `#1b2330` |
| Muted | `#8b95a5` | `#5c6675` |
| Accent | `#46c8a8` | `#1f9e84` |
| Amber | `#e0a23a` | `#b07410` |
| Error | `#f4b0b0` | `#c0392b` |
| Winter | `#5aa2e0` | `#2f7fc4` |

Follow Apple's Human Interface Guidelines and Liquid Glass direction for iOS 26+, with operational legibility as the higher priority. Use glass surfaces for navigation, section grouping, and lightweight overlays, but never make critical telemetry hard to read through excessive blur, transparency, or motion.

Design rules:

- Dashboard typography is dense but calm: large current SoC and status, compact supporting tiles.
- Cards use stable dimensions and avoid layout jumps while refreshing.
- Use SF Symbols for tab icons, status chips, connection actions, and chat send.
- Respect Dynamic Type, Reduce Motion, Increase Contrast, and Dark Mode.
- No decorative gradients, blobs, marketing hero, or explanation-heavy onboarding.

## App Store Readiness

The first build should be structured so it can later be submitted to the App Store:

- Native SwiftUI project with a clear bundle id and a simple checked-in app icon asset suitable for TestFlight builds.
- Local Network usage description.
- Camera usage description only if QR scanning is implemented; otherwise omit camera permission.
- Privacy posture: no tracking, no analytics SDK, no third-party data collection in the app.
- If HTTP LAN access is needed, provide a narrowly scoped ATS exception and document why.
- Review notes should explain that the app connects to a user-owned LAN/VPN EMS server and can be tested with a mock backend.

## Error Handling

Dashboard:

- Show the last successful snapshot with a stale indicator when refresh fails.
- Show connection errors as state, not as a blocking modal after initial setup.
- Keep pull-to-refresh available.
- If a specific nested section is missing, render a degraded tile rather than failing the whole dashboard.

Chat:

- Disable send while a message is pending.
- Allow retry after failure.
- If AI is off, show FAQ answers and a disabled free-form state.
- If the server returns a guarded/error response, display that response exactly enough to be useful without exposing debug internals.

Connection:

- Distinguish invalid URL, server unreachable, token required, token rejected, and incompatible API version.
- Manual entry always remains accessible.

## Testing Strategy

Backend tests:

- `/api/dashboard` returns all required top-level keys.
- Snapshot cache returns the same generated timestamp inside TTL across repeated calls.
- Concurrent `/api/dashboard` calls trigger at most one underlying dashboard snapshot build inside TTL.
- The endpoint does not increase live source reads beyond existing coalescing expectations.
- Chat endpoints remain unchanged and covered by existing tests.

iOS unit tests:

- `APIClient` decodes dashboard snapshots, chat responses, API errors, and auth state.
- `DashboardStore` uses `cache_ttl_seconds`, preserves stale snapshots after refresh failure, and cancels in-flight refreshes.
- `ChatStore` handles FAQ-only, AI-active, send success, send failure, and empty input.
- `ServerDiscovery` validates manual URLs and classifies connection failures.
- Theme tokens match the documented web palette.

iOS UI tests:

- First launch manual connection flow.
- Dashboard happy path with mocked `/api/dashboard`.
- Stale/offline dashboard state.
- Chat FAQ-only state.
- Chat active send/response state.
- Dynamic Type and dark mode screenshots.

## Five Iteration Loop

Each loop must end with implementation, tests, validation, and polish before moving on.

1. **Spec and API foundation**
   - Add `/api/dashboard` snapshot endpoint and tests.
   - Confirm caching prevents extra source calls.
   - Document response contract.

2. **iOS project foundation**
   - Create SwiftUI project, theme tokens, API client, models, and mock data.
   - Render connection shell and static dashboard from fixtures.
   - Add unit tests for decoding and stores.

3. **Live dashboard**
   - Connect to `/api/dashboard`.
   - Implement refresh scheduling from server TTL and stale offline state.
   - Add UI tests and accessibility pass.

4. **Chat**
   - Connect FAQ, explainer state, and `/api/chat`.
   - Add chat store tests and UI tests.
   - Verify chat does not bypass server grounding or caching.

5. **App Store polish**
   - Add discovery/manual setup polish, icon assets, permissions text, ATS review, screenshots, and mock backend instructions.
   - Validate on simulator sizes and at least one physical-device-ready build command.
   - Re-check Liquid Glass usage for legibility, motion, and Apple HIG alignment.

## Acceptance Criteria

- A spec, implementation plan, and native iOS app target exist in the repository.
- The iOS app can connect to a LAN/VPN EMS server by manual URL and can support scan/discovery if available.
- The dashboard uses a single consolidated cached backend endpoint.
- Multiple dashboard clients do not multiply live device, external API, or LLM calls.
- The app uses the same color scheme as the web app.
- Dashboard and chat are functional, tested, validated, and polished through five iterations.
- App Store constraints are documented and reflected in permissions, privacy posture, and project structure.
- The code is maintainable: small files, typed models, testable stores, no planner logic in the app, and no duplicated backend business rules.

## Implementation Choices for the Plan

These choices are fixed enough for the product spec and should be made concrete in the implementation plan:

- Whether the iOS target is created from Xcode templates manually or generated with Swift Package Manager plus an `.xcodeproj`.
- Exact backend nested response fields for `/api/dashboard`; the plan should reuse existing endpoint serializers to avoid drift.
- Whether Bonjour advertisement is added in iteration 1 or manual URL ships first with scan as a follow-up inside the same first product milestone.
- Minimum supported iOS version. Use iOS 26 visual APIs when available; keep the app functional on the oldest simulator/toolchain available in this repository environment.
