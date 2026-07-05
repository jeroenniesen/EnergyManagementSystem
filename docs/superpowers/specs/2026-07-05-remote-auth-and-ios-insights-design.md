# Remote-access auth + iOS Insights correctness — design

Date: 2026-07-05
Status: approved (via `/goal` + three architecture decisions below)
Scope: the 10 findings from the iOS/remote-access review.

## Decisions (owner: Jeroen)

1. **Remote transport = existing UniFi/Ubiquiti VPN.** No new public transport (no
   Cloudflare Tunnel/Access, no Tailscale, no hosted proxy). The EMS port is **never**
   publicly exposed (SPEC §12). The iOS app reaches the LAN URL over the VPN with a
   bearer token. Consequence: **no reverse-proxy / forwarded-header trust code** — a
   simpler, safer surface.
2. **Read auth = new `web.require_auth` flag, default OFF.** OFF preserves today's LAN
   guest read-only dashboard (backward-compatible with the running Mac Mini). ON requires
   a valid token for **all** `/api/*` reads and writes. Operator sets ON before VPN use.
3. **Single token** for read + control (no separate read-only tier). Control endpoints
   are **always** gated regardless of the flag, and **every control action is audited**.

## Findings → workstreams

### Backend (`ems/`)

- **F1 + F9 — centralize + gate auth.** Replace the four scattered
  `if not _authorized(...)` write-guards with **one auth middleware** over `/api/*`:
  - Write endpoints (POST `override`, `settings`, `ai/validate`, `chat`) → **always**
    require the token (unchanged).
  - Read endpoints → require the token **only when `web.require_auth` is ON**.
  - Always open: `GET /api/auth` (so the app can discover auth state) and `/health/*`.
  - Add `web.require_auth` to `settings.py` (bool, default `false`, group `access`,
    UI-editable). `GET /api/auth` also returns `require_auth` so the UI/app can explain it.
  - `POST /api/plan-preview` stays open (read-only what-if; no persistence, no writes).
  - **F9 audit:** at the API layer, append an `AuditStore` record for `POST /api/override`
    and for control-affecting settings writes (operational mode, `control.dry_run`,
    connection/source changes) so the *action* is logged, not just its downstream cycle
    effect. Tag with source (e.g. `"manual override via API"`).

- **F10 — cadence-aware row caps.** Replace the hardcoded `limit=3000` (finance) and the
  `÷60` "one row/min" ceiling (report) with caps derived from the **recorder cadence**
  (`recorder.cycle_seconds`, prod 300 s ⇒ ~288 rows/day). Formula:
  `rows_per_day = ceil(86400 / max(cycle_seconds, 1))`, cap = `rows_for_window × margin`
  (margin ≥ 2) clamped to `[min_floor, 200_000]`. Correct if cadence changes
  (e.g. `EMS_CYCLE_SECONDS=5` in dev). A pure helper `history_row_cap(...)` in the query
  path, unit-tested.

### iOS (`ios/EMSControl`)

- **F2 — fix the flaky test.** `InsightsStoreTests`: the concurrent `async let` fetch is
  correct; the *test* asserts a fixed request order. Change the exact-ordered
  `requestedQueries` array-equality to order-independent (Set / `.contains` both paths),
  and the `.first == report` assertion to `.contains`.

- **F3 + F8 — missing/future data, aligned to the web.** Honor
  `ReportSeriesBucket.samples` exactly as `ems/web/frontend/src/EnergyBehavior.tsx` /
  `FinanceSection.tsx` / `Insights.tsx` do:
  - `BehaviorChart`: break the polyline where `samples == 0` (new subpath), no
    point/segment for unsampled or future buckets, isolated dot for a lone sampled bucket
    between gaps, domain computed from **sampled** values only.
  - `SavedBars`: `savedEur == nil` ⇒ **no bar** + "no price data" accessibility, never a
    €0 bar. (Web draws no bar for `saved_eur == null`.)
  - Labels: keep "(so far)" on partial windows; keep the empty-state and partial-price
    caveats already present.
  - Extract the gap logic into a **pure, testable helper** (e.g.
    `SeriesGeometry.segments(values:samples:)`) so it is unit-tested without SwiftUI.

- **F4 — stale-data warning.** Add `lastUpdatedAt: Date?` to `InsightsStore` and
  `ActivityStore` (set on success). When a refresh fails but data remains, surface a
  banner: **"Showing data from HH:MM · couldn't refresh"** (mirrors `DashboardStore`'s
  `isStale` badge). Today the error is swallowed because it is gated behind
  `report == nil` / `entries.isEmpty`.

- **F5 — clear on server switch.** Make `InsightsStore.setClient` and
  `ActivityStore.setClient` **server-identity-aware**: clear cached data whenever the
  base URL (or token) changes, not only on `nil` — mirroring `ChatStore`'s `sessionKey`.
  Closes the live-A → live-B leak where server-A data could show under server B.

### Tests + docs

- **F7 — auth e2e.** Extend `ems/tests/test_auth.py` (+ new cases): unauth read
  (flag OFF ⇒ 200, ON ⇒ 401), authed read (ON ⇒ 200), unauth write always ⇒ 401,
  wrong-token ⇒ 401 (read + write), control-denial paths (`override`/`settings`),
  `/api/auth` + `/health/*` always open, non-ASCII token ⇒ 401 (kept), and a test that
  `X-Forwarded-*` headers **cannot** bypass auth (we trust no forwarded headers). iOS:
  server-switch-clears (Insights + Activity), stale-banner state, and the gap-geometry
  helper.

- **F6 — remote-access doc.** New `docs/remote-access.md`: supported model = **UniFi VPN,
  port never publicly exposed**; require `web.require_auth: true` + a token before any
  VPN/remote access; trust boundaries (VPN peer is trusted transport but the token is
  still required); token rotation (link `docs/operator-runbook.md`); logging (audit log
  for control + size-rotated app log); control permissions (single token, control always
  gated + audited). Cloudflare/Tailscale/public proxy explicitly **out of scope for v1**.
  Update SPEC §12 to reference it.

## Implementation order (recommended sprint)

1. F2 (fix test) → 2. F3/F8 (missing data) → 3. F4 (stale) → 4. F5 (server switch)
→ 5. F1/F9 (backend auth + audit) → 6. F10 (cadence limits) → 7. F7 (auth e2e)
→ 8. F6 (docs + SPEC §12).

## Non-goals

- No public internet exposure, no reverse proxy, no forwarded-header trust.
- No read-only token tier.
- No EV/control changes; no planner changes.
- No change to the write-gating default (writes stay gated whenever a token is set).

## Verification

- Backend: `uv run pytest ems/tests` (esp. `test_auth.py`, new limit tests) green;
  `uv run ruff check ems` clean.
- iOS: `DEVELOPER_DIR=… swift test` green (fixed + new tests).
- Manual: with `web.require_auth: true`, an unauthenticated `GET /api/status` returns 401;
  with it OFF, 200. iOS Insights shows gaps (not zeros) on a partial/empty window and a
  stale banner when a refresh fails.
