# Remote access

How to reach the EMS web UI and the iOS app from **outside** your home network, safely.

> **TL;DR** — the EMS port is **never** exposed to the public internet. Remote access is your LAN
> reached over a **VPN**. Before you connect from outside, set a **web access token** and turn on
> **`web.require_auth`** so every read (not just control) needs that token.

## Supported model: VPN to the LAN (v1)

The one supported remote-access mode is a **VPN into your home network** — in this deployment the
**UniFi / Ubiquiti VPN** (WireGuard). Your phone joins the home LAN over the tunnel and then talks
to the EMS exactly as it would at home: the same `http://<lan-ip>:8080` URL, plus a bearer token.

Why this and not a public tunnel/proxy:

- **No public attack surface.** The EMS port stays bound to the LAN; nothing is published to the
  internet. This matches SPEC §12 ("do not expose the EMS port to the internet").
- **No proxy-header trust.** Because there is no reverse proxy in front of the app, the EMS trusts
  **no** forwarded headers (`X-Forwarded-For/Proto/Host`, `X-Real-IP`). Authentication is the
  bearer token only — a spoofed forwarded header cannot authorise anything (tested in
  `ems/tests/test_remote_auth.py::test_forwarded_headers_cannot_bypass_auth`).
- **Fits the app as built.** The iOS app already connects to a "LAN or VPN URL" with a separately
  entered token (`ios/EMSControl/README.md`), and sends `Authorization: Bearer <token>` on every
  request — reads included.

### Explicitly out of scope for v1

Cloudflare Tunnel/Access, Tailscale, and any public reverse proxy / hosted proxy are **not**
supported paths in v1. They add a public hostname, third-party trust, and forwarded-header handling
that this design deliberately avoids. If one is ever adopted, it needs its own review: proxy-header
trust list, the proxy's own auth (SSO / service token), and TLS termination.

## Trust boundaries

| Boundary | Trusted? | Control |
|---|---|---|
| Home LAN (physical / Wi-Fi) | Yes (transport) | Reads open by default; writes always need the token |
| VPN peer (phone over WireGuard) | Same as LAN | It joins the LAN; still needs the token once `require_auth` is on |
| Public internet | No | EMS port not reachable; nothing published |
| Anything presenting the **token** | Authorised | Full read **and** control (single-token model) |

The VPN authenticates the *device onto the network*; the **token** authenticates the *request to the
EMS*. Both layers are required for safe remote use — the VPN is not a substitute for the token.

## Turn this on before connecting remotely

1. **Set a token.** Settings → Access → *Web access token* (`web.auth_token`). Use a long random
   value. Enter the same token in the app and in the browser's Access box.
2. **Require it for reads too.** Settings → Access → *Require the token to view too*
   (`web.require_auth` = ON). With this on, **every** `/api/*` read returns `401` without the token,
   not just control actions. Leave it **off** only for an open, LAN-only dashboard.
3. **Connect over the VPN**, using the LAN URL. That's it.

Auth posture summary:

| `web.require_auth` | Reads (`GET /api/*`) | Control/writes (`POST …`) |
|---|---|---|
| **off** (default, LAN) | open | require the token |
| **on** (for VPN/remote) | require the token | require the token |

Always open regardless: `GET /api/auth` (so the app can discover a token is needed) and
`/health/live` · `/health/ready` (liveness probes). The SPA shell and static assets load without a
token so the browser can show its Access box — but every datum it renders comes from a gated
`/api/*` read.

> With **no** token configured, `require_auth` cannot lock anything — you can't require a credential
> that doesn't exist. Set a token first.

## Control permissions

- **Single token** for both reading and control (manual override, settings, mode changes). There is
  no separate read-only credential in v1.
- **Control is always gated** — the four write endpoints (`/api/override`, `/api/settings`,
  `/api/ai/validate`, `/api/chat`) require the token whether or not `require_auth` is on.
- **Every control action is audited.** Manual overrides are written to the audit log
  (`manual_override`), and settings changes record which keys changed (`config_change`, keys only —
  never secret values). View them in the app's **Activity** tab or `GET /api/audit`.

## Token rotation, logging, secrets

- **Rotation:** see [`operator-runbook.md`](operator-runbook.md) → *Rotate a token*. In short: set a
  new token in the secret source / UI, re-enter it in the app and browser, then retire the old one.
- **Secrets never leak:** the token is a secret setting — masked in `GET /api/settings`, never
  echoed back, never written to the audit log or app log (SPEC §12).
- **Logging:** control actions → audit log; app logs are size-rotated with tokens redacted
  (`operator-runbook.md`).

## Enforcement (implementation)

Auth is a single pure-ASGI middleware (`_AccessMiddleware` in `ems/web/api.py`) — one choke point in
front of the whole JSON API, rather than a per-endpoint guard. It is deliberately **not** a Starlette
`BaseHTTPMiddleware`, because that wraps each request in an anyio task group that starves the
override endpoint's background control cycle. The behavior matrix above is covered by
`ems/tests/test_remote_auth.py`.
