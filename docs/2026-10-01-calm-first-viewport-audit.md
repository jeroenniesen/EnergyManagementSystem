# Calm first-viewport audit pointer (2026-10-01)

**Backlog:** E-10 follow-up — B-94 … B-102 (after shipped B-86 / B-87).  
**Status:** findings only; implementation tracked in `BACKLOG.md`.

## What this is

A senior UX/UI audit of the **live** HEMS web dashboard (Mac Mini tip ≈ `45c7629`) against the calm-dashboard intent. The first viewport had grown dense again: DeviceHealth wallpaper, duplicate evening-peak / SoC / savings surfaces, planner jargon in the hero, and BatteryActionWhy / evening-peak sitting **between** outcome tiles and the PlanStory chart.

## Durable specs (in-repo — do not duplicate)

- Calm hierarchy + PlanStory chart: [`docs/superpowers/specs/2026-07-18-calm-dashboard-design.md`](superpowers/specs/2026-07-18-calm-dashboard-design.md)
- Implementation plan: [`docs/superpowers/plans/2026-07-18-calm-dashboard.md`](superpowers/plans/2026-07-18-calm-dashboard.md)
- Readability follow-up: [`docs/superpowers/plans/2026-07-19-calm-dashboard-readability.md`](superpowers/plans/2026-07-19-calm-dashboard-readability.md)
- Emotional register: [`docs/2026-06-28-emotional-design-review.md`](2026-06-28-emotional-design-review.md)

## Audit + mockup (agent store; not committed)

Full write-up and screenshots live in the Cursor agent store for the 2026-10-01 UX pass:

| Artifact | Name |
| --- | --- |
| Audit | `ux-audit-dashboard-2026-10-01.md` |
| Audit prompt | `ux-audit-prompt-dashboard.md` |
| Target mockup (**PlanStory chart kept**) | `dashboard-mockup-calm-with-planstory.jpg` |
| Earlier calm fold mockup | `dashboard-mockup-calm-first-viewport.jpg` |
| Evidence screenshots | `media/ux-audit-dashboard/01-…09-…` |

Store path (operators): Agent Store → `docs/` + `media/ux-audit-dashboard/`.

## Do not change (implementation constraint)

Preserve: **PlanStory / combined 24h chart**, **Waarom? disclosure pattern**, **override confirmation**, **SkyBackdrop + existing theme tokens**. Hierarchy and duplication only — not a parallel design system.

## Target first-viewport wireframe

`[Topbar: title | 1–2 status chips | nav]` → `[Hero: verdict · 1 plain sentence · act-line]` → `[4 outcome tiles]` → `[PlanStory chart]` → `[details: Waarom / health / More]`.
