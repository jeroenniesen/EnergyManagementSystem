# Multi-level backlog + GitHub sync — design spec

*2026-07-03. User request: a multi-level backlog view (feature/epic → items spread over sprints),
edited locally, with a sync skill that pushes local changes to GitHub and pulls GitHub status back.
Decisions taken with recommended defaults (user AFK during the option prompt — all reversible):
Issues + Milestones representation; numbered date-less sprints; epics + sprint-assigned items sync,
the LATER/pool remainder stays markdown-only.*

## The model

Three levels, one file (`BACKLOG.md`), one sync direction rule.

```
EPIC (E-xx)            — a feature/outcome big enough to span sprints
  └─ Item (B-xx[a-z])  — one deliverable slice, sized to fit inside one sprint
       └─ Sprint N     — the bucket the slice is planned into (or "Pool")
```

- **Sprints are numbered, date-less buckets** (Sprint 1, Sprint 2, …): order without calendar
  pressure. "Pool" = groomed but unscheduled.
- **Multi-sprint features are expressed by slicing**: the epic spans sprints because its slices
  sit in different sprints — never by one item straddling two sprints.
- Existing B-numbers survive; a sliced item keeps its number with a letter suffix (B-04a, B-04b).

## BACKLOG.md layout (the "view")

1. **Sprint board** — a compact table at the top: rows = epics (+ pools), columns = Sprint 1..N /
   Pool; cells hold item ids with a status glyph (⬜ todo · 🔄 in progress/PR open · ✅ done).
   This is the multi-level at-a-glance view.
2. **Epic sections** — `## EPIC E-xx · name`: goal sentence, then its items as `### B-xx` blocks
   (unchanged item format) each carrying a `**Track:**` line:
   `**Track:** Sprint 1 · E-04 · GitHub #12 🔄 · PR #2`
   (sprint · parent epic · issue ref+state · PR ref, whichever exist).
3. **Pools** — standalone small items (`## Pool — unscheduled`) and refactors keep their current
   format; no epic required.

Conventions are documented at the top of the file so any future session (or the sync skill) can
parse them; the format stays human-first — no front-matter, no HTML comments.

## GitHub representation

| Local | GitHub |
|---|---|
| Sprint N | Milestone `Sprint N` (no due date) |
| Item in a sprint | Issue `B-xx · title`, label `backlog`, milestone set, body = item text + a "source: BACKLOG.md" footer |
| Epic (once ≥1 child is sprint-assigned) | Issue `E-xx · name`, labels `backlog`+`epic`, body = goal + task list `- [ ] #<child>` |
| Done | Issue closed (and/or linked PR merged) |

The LATER pool and unscheduled refactors deliberately do **not** become issues — GitHub carries
active work only (~5–10 issues), not the whole 30-item backlog.

## Sync contract (the one rule)

- **Content flows local → GitHub.** Titles, descriptions, slicing, epic membership: BACKLOG.md is
  the source of truth. The sync overwrites issue title/body from the local text.
- **Status + sprint assignment flow GitHub → local.** Issue closed / PR merged / milestone moved
  on GitHub: the sync updates the local `Track` line and board glyphs. (Close an issue on your
  phone; the next sync marks the item done locally.)
- Conflicts can't arise: each field has exactly one owner.
- An item deleted locally while its issue is open → the sync **reports** it and asks; it never
  closes or deletes on GitHub silently. Milestones/issues are never deleted, only closed.

**Edge rules (resolved during baseline testing, 2026-07-03):**
- An item already ✅ done but never synced gets **no** born-closed issue — keep its PR ref locally
  and list it as a checked plain bullet in its epic's body.
- Pool-only children appear in epic bodies as plain bullets, never empty `- [ ]` checkboxes —
  they have no issues and dangling boxes would misstate epic progress.
- Epic issue numbers are written back onto the epic's goal line, so future syncs match by ref,
  not by title.
- Later steps always use the actual issue numbers returned by `gh issue create`, never predicted
  ones (issues and PRs share the numbering sequence).

## The `/backlog-sync` skill

Project skill at `.claude/skills/backlog-sync/SKILL.md` (checked in). When invoked it:

1. Reads `BACKLOG.md`; collects epics/items/sprints/refs.
2. Reads GitHub: `gh api` milestones, `gh issue list --label backlog`, PR states for referenced PRs.
3. Prints a **dry-run plan** (creates / updates / closes / local write-backs) and **asks for
   confirmation before any GitHub mutation** (outward-facing writes need explicit approval).
4. Pushes: missing milestones → create; sprint items without refs → create issues; changed local
   content → update issue bodies; epics → create/refresh task lists.
5. Pulls: closed issues / merged PRs / moved milestones → update `Track` lines + board glyphs
   (✅/🔄/⬜) in `BACKLOG.md`.
6. Ends with a sync report (what changed in each direction) and leaves `BACKLOG.md` staged but
   uncommitted (the operator reviews the diff).

Model-driven, no parser code: the skill is instructions over `gh` + file edits, same as every
other skill. Requires `gh` authenticated with repo scope (already true in this repo).

## Initial epic/sprint cut (first grooming, adjustable by editing the file)

- **E-01 Honest CO₂ picture** — B-02 gas, B-10 NED.nl
- **E-02 Measured money** — B-03a history (PR #3) ✅-when-merged, B-03b dashboard tile swap, B-13 energy rollups
- **E-03 Family reach: iOS** — B-04a rebase+API catch-up, B-04b emotional-design parity, B-04c family rollout
- **E-04 2027-ready planner** — B-30 valley fix (PR #2), B-05 post-2027 economics, B-15 hysteresis, B-16 recovery, B-22 SoC gating
- **E-05 Quiet motivation** — B-06 trends, B-07 weekly recap, B-08 success markers
- **E-06 Trust & guidance** — B-09 failure states, B-12 setup split, B-31 marker fix, B-21 Dutch
- Pools: big levers (B-17/18/19/20/23), refactors (B-24..29), B-11/B-14 unscheduled.
- **Sprint 1 (current):** B-30, B-03a, B-02, B-06 · **Sprint 2:** B-04a, B-04b, B-03b, B-31 ·
  **Sprint 3:** B-05, B-04c, B-13, B-08 · rest Pool.

## Non-goals

No GitHub Projects v2 board, no dates/velocity/burndown, no automation triggers (sync runs only
when invoked), no two-way content merge (single-owner fields instead).
