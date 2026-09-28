# Tibber priceInfo fixtures (#137)

Recorded GraphQL `data` shapes for `viewer.homes[].currentSubscription.priceInfo`.
No live Tibber calls — tests load these files.

## Established format (what EMS queries today)

The shipped query in `ems/sources/tibber.py` omits `priceInfo(resolution: …)`.
Per [Tibber’s changelog (2025-09-30)](https://developer.tibber.com/docs/changelog),
that **defaults to `HOURLY`** for back-compat after the European quarter-hour MTU change.

| File | Shape | Notes |
|------|--------|--------|
| `price_info_hourly.json` | Trimmed sample (2h today + 1h tomorrow) | **Established live path** — same shape verified on the Mac Mini (`docs/live-integration.md`: 24×4 → 96 planner slots). |
| `price_info_hourly_{normal_96,spring_forward_92,fall_back_100}.json` | Full local days | Europe/Amsterdam DST: 24 / 23 / 25 hourly entries → 96 / 92 / 100 quarter-hours after expand. |

## Defensive format (quarter-hourly `today`/`tomorrow`)

If a client passes `resolution: QUARTER_HOURLY` (or Tibber ever changes the default),
`today`/`tomorrow` arrive already as ~15-min points. The parser must **not** re-expand them.

| File | Shape |
|------|--------|
| `price_info_quarter_hourly.json` | Trimmed sample with distinct per-quarter totals |
| `price_info_quarter_hourly_{normal_96,spring_forward_92,fall_back_100}.json` | Full local days (96 / 92 / 100 entries) |

True 15-min NL prices for a longer horizon also come from `priceInfoRange(resolution: QUARTER_HOURLY)`
(SPEC §6.2 / `docs/api-reference.md`) — that query path is separate; these fixtures cover the
`priceInfo.today`/`tomorrow` arrays the current adapter uses.
