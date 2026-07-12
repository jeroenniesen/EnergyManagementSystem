# The Apple of Energy Management Systems — design roadmap (2026-07-12)

What "Apple" actually means here is not aesthetics. It is a promise: **plug it in and the home
gets cheaper and cleaner, with one glance to trust it and zero manuals.** Every phase below is
judged against that promise with a measurable bar. The audit companion:
`2026-07-12-settings-ux-audit.md`.

## Design principles (the constitution)

1. **Defaults are the product.** Every setting must earn its visibility. If the app can measure,
   detect, or infer a value, it should — and then *suggest*, never silently apply (the
   solar-confidence advisor is the house style: evidence → suggestion → "you decide").
2. **One glance, one truth.** The dashboard answers "is my home doing the right thing?" in one
   sentence before it shows a single number. Numbers justify; they never lead.
3. **Progressive disclosure everywhere.** First sentence first. Detail on demand. Advanced behind
   a fold. This applies to help text, cards, exports, and explanations equally.
4. **Explain, never justify.** Plain language, including — especially — for *inaction* ("holding:
   the spread doesn't beat the wear"). No raw enum, no jargon leaks to a screen.
5. **Never make the human do the machine's work.** Re-typing meter IPs, hand-tuning confidence
   percentages, remembering to re-anchor the car — each is a defect to burn down over time.
6. **Quiet safety.** Fail-safe behaviour is invisible until needed, then unmissable. Never modal
   panic; always "here's what I did to keep you safe, here's the one thing you can do."
7. **One product on every surface.** Web, iOS, exports, and chat share one vocabulary, one set of
   scores, one tone. A screenshot of any surface should be recognisably the same product.
8. **Performance and accessibility are features.** ≤300KB gz, WCAG 2.1 AA, 60fps — regressions
   are bugs, not debts.

## Where we already meet the bar (keep, don't churn)

Score rings with plain-language reads · decision explainability incl. why-NOT · fail-safe AUTO ·
dry-run-before-live discipline · the landscape header (brand warmth) · demo mode · the advisor
voice · one-command install/upgrade · redacted one-click export.

## The phases

### P0 — Calm the controls *(this branch)*
The Settings two-pane menu: intent-grouped sidebar, search, one section at a time, first-sentence
help, sticky save. **Bar:** any setting in ≤2 interactions; configuring a section ≤1.5 screens;
save always visible when dirty.

### P1 — The first five minutes
The out-of-box experience decides whether this is an appliance or a project.
- **Guided onboarding**: discover HomeWizard meters on the LAN (mDNS) and *offer* them; pick your
  car from the database; drop the map pin; end on "watching your home now — come back tomorrow
  for your first insights."
- Demo mode becomes the empty state, with one "use my real home" action.
- First-run explainer of watch-mode vs. control, in one screen, no scroll.
**Bar:** unboxing → live dashboard in under 5 minutes, zero typed IP addresses on a normal LAN;
day-2 return shows a personalised insight, not an empty chart.

### P2 — Trust at a glance
- Dashboard hierarchy pass: one hero verdict sentence, then support. Nothing on the first screen
  a family member wouldn't understand.
- **Weekly digest** (the Sunday email/screen): what you saved, what the system did, one suggested
  tweak — the advisor voice, on a schedule.
- iOS: widgets (SoC + today's verdict), Live Activity during a planned car-charge window,
  push for the rare must-know (fail-safe engaged, charge window starting).
**Bar:** a non-operator household member can answer "are we doing well this week?" in 10 seconds.

### P3 — It configures itself
- One-tap adoption of advisor suggestions (solar confidence today; export model when 2027 nears).
- Auto-season, auto-calibrated load baseline, car-session auto-detection refining the SoC anchor.
- Anomaly whispers: "your solar underperformed similar days — panels dirty?"
**Bar:** a year of operation needs ≤4 manual setting changes.

### P4 — Earned delight
- A "Year in Energy" review (savings, sunniest day, best arbitrage catch — shareable).
- Seasonal landscape moments; micro-transitions on state changes (never gratuitous).
- Milestone moments: first €100 saved, first full solar night.
**Bar:** at least one screen someone *shows* to a friend unprompted.

### P5 — The ecosystem, held to the same bar
- Charger/car control (the v2 EV spec) — the planner's slots become commands, behind the same
  probe → dry-run → confirm discipline as the battery.
- HA entities, heating control (F5/F6) — each new device class enters through onboarding
  discovery, explains itself, and fails safe.
**Bar:** adding a device never requires documentation.

## Sequencing note

P0 ships now; P1 is the highest-leverage next step (it compounds: every future user passes through
it); P2 and P3 interleave by season (digest lands before winter, self-configuration before the
2027 pivot); P4 rides along continuously; P5 stays gated on its specs, never on enthusiasm.
