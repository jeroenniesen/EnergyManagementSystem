# Settings — visual experience audit (2026-07-12)

Method: live captures of the running app (mock mode, seeded data) at 1100px and 390px, collapsed
and fully-expanded states, plus full-resolution crops for micro-typography. Numbers below are
measured, not estimated.

## The headline finding

Collapsed, the page looks tidy — 13 accordion groups on ~1.5 screens. But the moment someone
actually configures (expand + advanced), it becomes **one 6,365px column — 7.1 screens** of
uninterrupted form. Density is not a field-count problem; it is a **structure** problem: every
group, every field, and all of its documentation compete in a single scroll.

## Findings

### Structure & navigation
1. **One giant column.** Editing a field mid-page leaves the Save button ~6,000px away, off-screen.
   No sticky save; the dirty state is invisible while scrolled. Risk of abandoned edits.
2. **13 flat groups with no intent hierarchy.** Setup-once (Connection, Meters, Battery, Prices,
   Solar & location) is interleaved with tune-often (Strategy, Planner economics, Car) and
   almost-never (Appearance, Access). The user's mental model — *"am I setting up, tuning, or
   personalising?"* — is not reflected anywhere.
3. **No search.** 60+ settings, no way to find "efficiency" without opening groups one by one.
4. **The global "Show advanced settings (24)" toggle** sits at the top of the page and mutates
   fields thousands of pixels below — cause and effect are never on screen together.
5. **Group badges show field counts** (2, 6, 7…) — engineering metadata. They orient nobody.

### Visual rhythm & micro-typography (full-res crops)
6. **Help text IS the density.** Every field carries 3–8 lines of (excellent) explanation as
   always-visible body text. The explainability is a strength — the presentation costs a screen
   per group. Pattern needed: first sentence visible, rest behind a per-field disclosure.
7. **Masonry raggedness.** The 3-column grid with wildly varying help lengths produces floating
   orphan fields and misaligned rows (e.g. "Battery/meter read interval" alone under a 9-line
   neighbour).
8. **Native checkboxes** (system green ✔) clash with the app's otherwise cohesive visual language;
   toggles should be styled switches.
9. **Raw enum tokens leak into selects** ("net_metering") where labels.ts humanisation isn't
   applied.
10. **RESTART chips** are valuable but float inconsistently; restart-pending state is invisible
    from the group level.

### What is already good (keep)
Collapsed-by-default groups; genuinely plain-language help; per-field RESTART flags; the inline
solar-confidence advisor (evidence + suggestion + "you decide" — exactly the right voice); theme
coherence with the dashboard landscape.

## The redesign (implemented on this branch)

A **two-pane settings shell** — the "menu system":

- **Left: a sidebar menu**, sections grouped under three intent headers — **Your setup** /
  **How it runs** / **App** — with an icon and a one-line purpose per item, an unsaved-edits dot,
  a restart-pending badge, and a **search field** that filters sections to matching fields.
- **Right: one section at a time**, single column (max ~640px — kills the masonry), section title
  + hint, fields with **first-sentence help + "More…" disclosure**, and the section's advanced
  fields under an in-place "Advanced" divider (replacing the global toggle).
- **Sticky save bar** slides in whenever anything is dirty: "N unsaved changes · Save · Discard" —
  always visible, never 6,000px away.
- **Mobile**: the sidebar becomes an iOS-style drill-in list (sections → section), back button.
- No backend changes: the schema-driven rendering, validation, and special-case editors
  (car picker, weekly schedule) are preserved.

Fixes findings 1–7 structurally; 8–10 are addressed as part of the same pass.

## Measures of success
- Configuring any single section never exceeds ~1.5 screens.
- Save is always visible when dirty.
- Any setting reachable in ≤2 interactions (search → section) from anywhere.
- Zero raw enum tokens visible.
