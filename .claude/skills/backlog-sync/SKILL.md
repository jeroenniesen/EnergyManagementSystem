---
name: backlog-sync
description: Retired. The product backlog lives in GitHub Issues, not BACKLOG.md. Use when the user asks to sync the backlog, groom items, or track new work — do not edit BACKLOG.md or mirror markdown to GitHub.
---

# Backlog — GitHub Issues (`/backlog-sync` is retired)

Do not maintain `BACKLOG.md`. That file is only a pointer. Do not parse it, recreate its board,
or push its old item text to GitHub.

The backlog lives in GitHub Issues:

<https://github.com/jeroenniesen/EnergyManagementSystem/issues?q=is%3Aissue+is%3Aopen+label%3Abacklog>

The last full `BACKLOG.md` is git commit `00ed087aa7a06ee3e9a83164e63563a7eb7418fc`.

## How work is tracked

New work is a GitHub issue. Give it these labels:

- `backlog`
- `type:*` — kind of work
- `prio:*` — priority; together with the body line below, this sets the order
- `area:*` — part of the system
- `size:*` — effort. `size:S` is at most 3 hours, `size:M` is 3–6 hours, `size:L` is more than 6 hours and means split the issue before starting it

Each issue body carries the order line `_Volgorde in backlog: #N_`. Read open `backlog` issues in
`prio:*` order, then by that line.

Issue titles keep the old B-number so history stays traceable, for example `[B-09] ...`. Do not
invent a new B-number unless the user assigns one.

## When this skill is invoked

1. Stop. Do not edit `BACKLOG.md`, and do not run the old local-to-GitHub sync.
2. Read or file the work as GitHub issues with the labels above.
3. Tell the user the markdown sync is retired and link the issues filter.

The old contract in `docs/superpowers/specs/2026-07-03-backlog-sync-design.md` is historical.
Do not follow it.
