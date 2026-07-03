---
name: backlog-sync
description: Use when the user asks to sync the backlog, push backlog items or epics to GitHub, pull issue/PR status into BACKLOG.md, or after grooming/editing BACKLOG.md locally.
---

# Backlog sync — BACKLOG.md ↔ GitHub

Contract (see `docs/superpowers/specs/2026-07-03-backlog-sync-design.md`):
**content flows local → GitHub; status + sprint assignment flow GitHub → local.** Each field has
one owner, so conflicts cannot arise.

Mapping: sprint = milestone `Sprint N` (no due date) · item = issue `B-xx · <title>` (label
`backlog`, milestone set) · epic = issue `E-xx · <name>` (labels `backlog` + `epic`, task list of
children, **no** milestone). Only epics and sprint-assigned items sync; Pool/LATER items stay
markdown-only.

## Procedure

1. **Read both sides.** Parse `BACKLOG.md` (board, epics, items, `**Track:**` lines). Then:
   `gh issue list --label backlog --state all --json number,title,state,milestone,body`,
   `gh api repos/{owner}/{repo}/milestones --paginate`, and `gh pr view <n> --json state,mergedAt`
   for every PR referenced in a Track line.
2. **Print the dry-run plan** — a table of: milestones/labels to create, issues to create, bodies
   to update, issues to close, and local Track/board edits (each with a one-line reason).
   **Ask the user to confirm before ANY GitHub mutation.** Not confirmed → stop after the plan.
3. **Push (after confirmation).** Create missing `backlog`/`epic` labels and `Sprint N` milestones.
   Create an issue for every sprint-assigned item without a `GitHub #` ref; update issue
   title/body where local text changed. Issue body = the item's BACKLOG.md text + footer
   `_Source: BACKLOG.md (B-xx) — content is owned locally; edit there, not here._`
   Create/refresh epic issues: task list `- [ ] #n` per issue-linked child (`- [x]` when closed);
   pool-only children as plain bullets — never empty checkboxes for items that have no issue.
4. **Pull.** Referenced PR merged or issue closed → glyph ✅. Issue reopened → 🔄/⬜. Issue moved
   to another milestone on GitHub → update the item's Sprint in its Track line.
5. **Write back refs.** Each newly created issue number goes into its item's Track line; each epic
   issue number goes onto the epic's goal line. Use the ACTUAL numbers `gh issue create` returns —
   never predicted ones.
6. **Finish.** Update the Sprint-board glyphs to match the Track lines; `git add BACKLOG.md` but
   do NOT commit (the operator reviews the diff); print a report of what moved in each direction.

## Edge rules

- Item already ✅ done but never synced → do **not** create a born-closed issue; keep its PR ref
  and list it as a checked plain bullet in the epic body.
- Item deleted locally while its issue is open → report and ask; never close or delete on GitHub
  silently. Milestones and issues are never deleted, only closed.
- Moving a finished item into the Shipped section is the owner's grooming, not the sync's job —
  the sync only touches Track lines, glyphs, and the board.

## Red flags — stop if you're about to

- Create issues for Pool/LATER items (out of scope by design).
- Edit local item text to match GitHub (content is locally owned).
- Mutate GitHub before the user confirmed the printed plan.
- Reference an issue number you predicted rather than read back.
