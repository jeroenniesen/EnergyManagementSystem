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

The app opens into the actual monitoring experience, not a marketing screen. First launch asks the user to connect to the EMS server by entering a URL, scanning a pairing QR code, or selecting a discovered server. If the user has no server available, a read-only **Demo** mode lets them inspect the Dashboard and Chat experience from bundled fixture data. Once connected, the app shows two primary tabs:

1. **Dashboard** - operational overview of the EMS: readiness, dry-run/live state, current intent, SoC, strategy, freshness, plan summary, prices, solar, battery tower summary, savings, alerts, and the latest AI validation state when available.
2. **Chat** - the existing grounded assistant experience: FAQ answers work without AI, and free-form questions use `/api/chat` when the backend says AI chat is active. Chat history is session-only in v1 and is cleared when the user disconnects, switches server, signs out, or force-quits the app.

The app uses native iOS interaction patterns: tab navigation, pull-to-refresh, swipe-safe scrolling, native Dynamic Type, VoiceOver labels, system alert sheets for connection errors, and system text entry for chat.

## Architecture

Use a native SwiftUI app in `ios/EMSControl` backed by small, testable modules:

- `EMSControlApp` - app entry point and dependency composition.
- `AppShellView` - root navigation and tab structure.
- `ConnectionView` - server scan/manual URL setup and connection validation.
- `DashboardView` - dashboard rendering from one snapshot model.
- `ChatView` - grounded chat UI and FAQ rendering.
- `APIClient` - async HTTP client, token header handling, decoding, retry policy, and cancellation.
- `ServerDiscovery` - manual URL validation, QR pairing payload parsing, Bonjour/mDNS discovery when advertised, and optional subnet probing as a last resort.
- `DashboardStore` - refresh state, TTL handling, offline stale snapshot display, and error state.
- `ChatStore` - message list, pending request state, and send/cancel handling.
- `DemoDataStore` - bundled reviewer/demo fixtures for dashboard, FAQ, and chat responses.
- `Theme` - shared color tokens matching the web app palette.
- `Models` - Codable request/response types for dashboard, chat, auth/explainer, FAQ, and errors.

Keep server state authoritative. The iOS app may cache the last dashboard snapshot locally for display while offline, but cached mobile data is never used for control decisions. Local snapshot caching is limited to the last successful `/api/dashboard` response per saved server, stored outside iCloud backup when possible, and deleted when the user forgets the server.

Demo mode is visually labeled as demo data, is read-only, does not store tokens, and never attempts to call control endpoints. It exists for App Store review, onboarding, and offline design validation; it is not a simulator for real battery behavior. Every Demo screen must keep a visible `Demo` badge in the navigation area, and the connection screen must offer `Connect to my EMS` so a reviewer or user can leave Demo mode without clearing app data.

## Backend API Contract

Add one consolidated dashboard snapshot endpoint before or alongside the iOS app:

`GET /api/dashboard`

The endpoint returns one coherent, versioned read model for dashboard clients. This is a mobile/public read model, not a dump of internal Python objects or web-component state. The backend may build it by calling existing serializers, but the JSON contract below is stable for native clients.

```json
{
  "api_version": 1,
  "generated_at": "2026-06-29T12:00:00+00:00",
  "server_time": "2026-06-29T12:00:00+00:00",
  "server_name": "Home EMS",
  "cache_ttl_seconds": 10,
  "degraded_sections": [],
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

Required top-level fields:

- `api_version` - integer contract version; v1 is the first native iOS contract.
- `generated_at` - when the snapshot was computed.
- `server_time` - server clock at response time, useful for skew and stale-state display.
- `server_name` - user-visible server label from config/settings, with a stable fallback such as `Home EMS`.
- `cache_ttl_seconds` - client refresh hint and server snapshot TTL.
- `degraded_sections` - list of top-level section names that failed to build or are stale enough to render with warning treatment.

Nested sections must be JSON objects that remain backward-compatible within `api_version: 1`: adding optional fields is allowed; renaming or removing fields requires `api_version: 2`. The endpoint is additive: the existing granular web endpoints stay in place for the current web UI and drill-down views.

Each top-level section must be safe to decode independently. A section that cannot be built must return a small degraded object rather than disappear:

```json
{
  "state": "degraded",
  "message": "Battery details are temporarily unavailable.",
  "updated_at": "2026-06-29T12:00:00+00:00"
}
```

Normal sections may use their existing response shape, but if they include a `state` field it must use one of `ok`, `stale`, `degraded`, or `unavailable`. The iOS app renders `degraded_sections` and section-level states as warnings, not crashes.

### Caching Requirements

The dashboard endpoint must be protected by a short server-side snapshot cache. Multiple clients and browser tabs calling `/api/dashboard` inside the TTL must reuse the same computed snapshot. The TTL should default to 10 seconds and be returned to clients as `cache_ttl_seconds`.

The endpoint must not bypass existing source protections:

- Live meter and battery reads stay in the existing short in-memory coalescing window.
- Tibber, Forecast.Solar, and AI explanation caches stay server-owned.
- No mobile client may call many granular dashboard endpoints on a polling loop.
- The iOS app schedules refreshes from `cache_ttl_seconds` and backs off when backgrounded or when the server is unreachable.
- Pull-to-refresh can force a client request, but the server may still return the cached snapshot inside the TTL.
- The iOS app must treat `api_version` values above its supported range as an incompatible-server state with a clear upgrade message.

This contract is the main defense against doubled calls from the iOS app or future monitoring devices.

## Existing Chat Contract

Use the existing backend chat endpoints:

- `GET /api/explainer` - whether AI chat is active and what language is configured.
- `GET /api/faq` - deterministic quick answers that work with AI off.
- `POST /api/chat` - free-form grounded question; body `{ "question": "..." }`.

The iOS app must not build its own LLM prompt or contact an LLM provider directly. All grounding, redaction, provider choice, and caching remain server-side.

Chat privacy rules:

- Do not persist chat history to disk in v1.
- Keep message history in memory only for the active server session.
- Clear messages when the server changes, token changes, the user disconnects, or the app cold-starts.
- Never include chat text in analytics, logs, crash metadata, or reviewer screenshots unless the text comes from bundled demo fixtures.

## Connection and Discovery

First iteration supports:

- Manual server entry: `http://192.168.x.x:8080`, `http://ems.local:8080`, or another LAN/VPN URL.
- QR pairing payloads containing the base URL and optional server label. Tokens must not be embedded in QR payloads for v1.
- Connection validation through `/health/live`, `/health/ready`, and `/api/auth`.
- Secure storage of the base URL and optional web token in Keychain.
- Local Network permission copy that explains the app searches only for the user's EMS server.
- Demo mode using bundled fixtures when no server is reachable or when App Store review needs a standalone path.

Discovery priority:

1. Manual URL entry, always visible.
2. QR pairing, if the user chooses to scan.
3. Bonjour/mDNS when the EMS backend advertises a service.
4. Subnet probing only as an optional fallback, limited to a small derived range, strict timeouts, and no repeated background scanning.

Manual entry and Demo mode must always remain accessible.

VPN access is just a manually entered URL. The app does not create or manage the VPN.

## Authentication and Security

Reuse the backend's existing token model. If `/api/auth` says a token is required, the app prompts for it and stores it in Keychain. The native client sends the token as `Authorization: Bearer <token>` unless the backend contract is deliberately changed before implementation; if changed, both the web and iOS clients must share the same documented header.

Security requirements:

- Do not log tokens, full chat questions, server responses, or device identifiers.
- Prefer HTTPS whenever the server provides it.
- Permit HTTP only for user-entered local network, `.local`, or VPN hosts needed by this self-hosted app.
- When a token is sent over HTTP, show a one-time plain-language warning during setup: local HTTP is acceptable only on a trusted LAN/VPN and can expose the token to devices on that network.
- Support revoking the saved token by forgetting the server, which deletes Keychain credentials and local cached snapshots.
- Do not sync server URLs, tokens, snapshots, or chat text through iCloud in v1.
- Expire local dashboard snapshots after 24 hours; after that, show the connection shell rather than presenting stale home telemetry.
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

Liquid Glass guardrails:

- Use glass treatment for the tab bar, navigation bars, connection sheets, and transient overlays.
- Keep core telemetry cards high-contrast and mostly opaque.
- Never place critical values such as SoC, live/dry-run state, fallback state, or alerts on heavily blurred image/content backgrounds.
- Gate iOS 26-only glass APIs behind availability checks; older iOS versions use the same colors with standard materials.
- Reduce motion disables shimmer, animated glass transitions, and nonessential chart animation.

Design rules:

- The first dashboard viewport starts with a compact status header: server name, stale/demo/live indicator, current intent, SoC, and dry-run/live/fallback badges.
- Below the header, show a two-column adaptive grid for the most important monitoring cards: battery, current price, solar/current flow, savings, and next plan action.
- Alerts and fallback states appear directly under the header and above secondary charts.
- Detailed energy story, tower detail, and diagnostics live lower in the scroll view or behind drill-in sheets.
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
- A built-in Demo mode must let App Store reviewers exercise Dashboard and Chat without a private EMS server.
- Review notes should explain that live mode connects to a user-owned LAN/VPN EMS server and that Demo mode is available from first launch.
- Demo mode must not imply that it is connected to real hardware. Status labels, timestamps, and chat answers must use fixture wording that is clearly synthetic.

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
- `/api/dashboard` returns `api_version: 1` and stable section objects, including degraded section objects when a nested builder fails.
- Snapshot cache returns the same generated timestamp inside TTL across repeated calls.
- Concurrent `/api/dashboard` calls trigger at most one underlying dashboard snapshot build inside TTL.
- The endpoint does not increase live source reads beyond existing coalescing expectations.
- Chat endpoints remain unchanged and covered by existing tests.

iOS unit tests:

- `APIClient` decodes dashboard snapshots, chat responses, API errors, and auth state.
- `APIClient` rejects unsupported future `api_version` values with an incompatible-server error.
- `DashboardStore` uses `cache_ttl_seconds`, preserves stale snapshots after refresh failure, and cancels in-flight refreshes.
- `ChatStore` handles FAQ-only, AI-active, send success, send failure, and empty input.
- `ChatStore` clears messages on disconnect, token change, server change, and cold start.
- `ServerDiscovery` validates manual URLs and classifies connection failures.
- `DemoDataStore` loads bundled fixtures and never creates credentials.
- Theme tokens match the documented web palette.

iOS UI tests:

- First launch manual connection flow.
- Dashboard happy path with mocked `/api/dashboard`.
- Stale/offline dashboard state.
- Chat FAQ-only state.
- Chat active send/response state.
- Demo mode dashboard and chat states.
- HTTP setup warning when a token-protected server is configured over local HTTP.
- Forget-server flow clears token, local snapshot, and in-memory chat.
- Dynamic Type and dark mode screenshots.

Visual validation artifacts:

- Save simulator screenshots for Dashboard and Chat in light and dark mode.
- Save Dynamic Type screenshots at default and accessibility-large sizes.
- Save stale/offline, invalid-token, and Demo mode screenshots.
- Inspect screenshots for overlapping text, clipped values, insufficient contrast, and excessive glass blur behind critical values.
- Store validation screenshots under `docs/ios-validation/` with filenames that include the iteration number, device, theme, and state.

## Five Iteration Loop

Each loop must end with implementation, tests, validation, and polish before moving on.

1. **Spec and API foundation**
   - Add `/api/dashboard` snapshot endpoint and tests.
   - Confirm caching prevents extra source calls.
   - Document response contract.

2. **iOS project foundation**
   - Create SwiftUI project, theme tokens, API client, models, demo fixtures, and mock data.
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
   - Add discovery/manual setup polish, Demo mode, icon assets, permissions text, ATS review, screenshots, and reviewer instructions.
   - Validate on simulator sizes and at least one physical-device-ready build command.
   - Re-check Liquid Glass usage for legibility, motion, and Apple HIG alignment.

## Acceptance Criteria

- A spec, implementation plan, and native iOS app target exist in the repository.
- The iOS app can connect to a LAN/VPN EMS server by manual URL, supports QR pairing payloads, and can show Bonjour-discovered servers when the backend advertises them.
- Demo mode works without a live server and is clearly labeled as demo data.
- The dashboard uses a single consolidated cached backend endpoint.
- Multiple dashboard clients do not multiply live device, external API, or LLM calls.
- The app uses the same color scheme as the web app.
- Chat history is session-only and is not persisted locally in v1.
- Dashboard and chat are functional, tested, validated, and polished through five iterations.
- Each iteration leaves explicit evidence: passing backend/iOS test output plus validation screenshots or notes for the screens touched in that iteration.
- App Store constraints are documented and reflected in permissions, privacy posture, and project structure.
- The code is maintainable: small files, typed models, testable stores, no planner logic in the app, and no duplicated backend business rules.

## Implementation Choices for the Plan

These choices are fixed enough for the product spec and should be made concrete in the implementation plan:

- Whether the iOS target is created from Xcode templates manually or generated with Swift Package Manager plus an `.xcodeproj`.
- Exact backend nested response DTOs for `/api/dashboard` v1; the plan should define each required field and reuse existing endpoint serializers internally where that avoids drift.
- Whether Bonjour advertisement ships before subnet probing; manual URL, QR pairing, and Demo mode are required.
- Minimum supported iOS version. Use iOS 26 visual APIs when available; keep the app functional on the oldest simulator/toolchain available in this repository environment.
