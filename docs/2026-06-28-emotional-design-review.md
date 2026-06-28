# Emotional Design Review - 2026-06-28

## Scope

Reviewed the current application from a user-interaction and emotional-design perspective. The goal is not to make the EMS feel playful or decorative. This is a control system for home energy, money, and a physical battery, so emotional design should create:

- calm,
- trust,
- agency,
- safety,
- comprehension,
- a sense that the system is quietly taking care of the home.

I reviewed the React UI, labels, CSS, tests, related docs, and attempted a live visual pass. The app server started but shut down after an Indevolt read timeout in this environment, so the visual validation is primarily source-driven plus existing behavior from tests and documentation.

## Loop 1 - Research: Current User Experience

### Existing Strengths

1. **Plain-language labels already reduce fear.**
   `labels.ts` turns internal states like `dry_run`, `unsafe`, and `auto` into homeowner-facing language such as "Watching only", "Paused - safe mode", and "Self-use (auto)".

2. **The dashboard leads with meaningful home outcomes.**
   The app now shows saved money, battery level, house load, grid flow, solar, battery power, home use, strategy, energy story, charge target, manual control, controller decision, data status, and AI validation. That is a good operational base.

3. **Energy Story is the strongest emotional feature.**
   `EnergyStory.tsx` tells the user "what happened" and "what will happen" in one visual model. The headline, stats, self-sufficiency, reserve line, target line, and peak insight create confidence.

4. **The app has progressive disclosure.**
   Settings are grouped and collapsible, advanced settings are hidden, the battery tile opens a per-tower modal, and the strategy card keeps advanced tuning behind a link.

5. **Loading is less abrupt than before.**
   Skeleton cards are better than a bare "Loading..." for live sensor reads.

6. **Light/dark themes and accessibility work are already present.**
   The CSS includes light theme variables, focus-visible states, reduced-motion handling, and chart aria labels in several places.

### Existing Emotional Friction

1. **The app still asks the user to understand too many raw system states.**
   "Data status", "System status", "Audit", and "Manual control" are useful, but they do not yet form a simple emotional hierarchy: safe, needs attention, paused, controlling, or saving money.

2. **Manual control feels too easy for a risky action.**
   The override card says "tell the battery exactly what to do" and exposes charge/hold/discharge in a compact form. For a real battery, this needs more ceremony, confirmation, and reassurance.

3. **Settings can feel like a technical configuration panel.**
   Groups are clear, but users still see many device IPs, economics, tokens, and operational controls. The page needs an onboarding/commissioning layer that separates "set up your home" from "tune advanced behavior."

4. **Errors are factual but not emotionally complete.**
   "Cannot reach EMS API" is clear, but the user also needs to know: "Is my battery safe?", "Is the house still running normally?", and "What should I do next?"

5. **System status has checks but not recovery guidance.**
   Diagnostics explain pass/warn/fail, but emotional design needs next actions: "Check the battery IP", "Leave it alone; EMS is paused safely", "Restart needed", etc.

6. **Success moments are underdeveloped.**
   The app shows savings and self-sufficiency, but it could better celebrate quiet wins without becoming noisy: "Covered the evening peak from your battery", "No grid top-up needed tonight", "Stayed above reserve."

## Loop 2 - Processing: Emotional Design Model For This App

### Emotional Design Goal

The ideal feeling is:

> "The system understands my home, explains itself clearly, asks before risky actions, and quietly keeps the battery useful without surprising me."

### The Emotional States The UI Should Support

1. **Calm observation**
   User opens the app and sees: current state, safe mode, what is happening now, and whether action is needed.

2. **Trust through explanation**
   User asks "why is it charging/not charging?" and receives a grounded, non-technical answer with the few numbers that matter.

3. **Agency without danger**
   User can override or tune behavior, but the UI makes risk visible and prevents accidental harmful choices.

4. **Relief during failure**
   If something is stale or disconnected, the UI should say the battery is safe and what part is degraded.

5. **Satisfaction after good behavior**
   The UI should show small proof that the system helped: peak covered, solar stored, reserve respected, grid import avoided.

### Screen-By-Screen Recommendations

#### Dashboard

Current state: strong information density, good labels, good metrics, but not enough emotional hierarchy.

Implement:

- Add a **home state headline** above the metric grid:
  - "All good - watching only"
  - "Running on your battery"
  - "Saving the battery for tonight"
  - "Paused safely - battery is managing itself"
  - "Needs attention - battery data unavailable"
- Add a **one-sentence plan summary** near the top:
  - "Tonight: target 72% by sunset; expected to top up 2.1 kWh from solar."
  - "No grid charge planned; tomorrow's sun should cover the night."
- Make the money tile less dominant when savings are zero or estimated from simulated data. Emotional trust is damaged if "Saved today €0.00" is the first thing the user sees every time.
- Add "confidence language" near the data-quality badge:
  - "High confidence"
  - "Using fallback forecast"
  - "Plan paused until battery data returns"
- Keep the grid compact, but group metrics by mental model:
  - Home now: house load, grid, solar, battery
  - Battery: level, mode, charge target
  - Outcome: savings, self-sufficiency

#### Energy Story

Current state: best part of the app emotionally.

Implement:

- Add a small "Why this matters" line for the current story:
  - "This is the period where electricity is most expensive."
  - "This is when the battery should carry the home through the evening."
- Add explicit **trust markers**:
  - "Reserve respected"
  - "Target reached"
  - "No unnecessary grid charge"
  - "Solar shortfall covered"
- Add hover/focus details for story segments that explain cause and effect:
  - "Charged here because price was €0.12 and sunset target was not met."
  - "Held here to keep energy for the evening peak."
- Add gentle positive reinforcement when true:
  - "Good day: your solar covered the home and filled the battery."
  - "Nice: the battery covered the highest-price slot."
- Avoid over-celebration. This is not a fitness app; use quiet confirmation, not confetti.

#### Strategy Card

Current state: simple and friendly, but "Summer/Winter" may become misleading.

Implement:

- Rename or supplement seasonal labels with intent labels:
  - Auto: "Let EMS choose"
  - Solar-first: "Use my solar tonight"
  - Price-smart: "Avoid expensive grid power"
- Explain what changing strategy will do before it takes effect:
  - "This changes the next plan; it will not switch the battery immediately unless the plan calls for it."
- For Auto, show why it chose the current strategy:
  - "Running solar-first because expected solar surplus is 8.4 kWh."
  - "Running price-smart because tomorrow's solar is low and price spread is high."
- Add a "recommended" marker only when you can justify it deterministically.

#### Manual Control / Override

Current state: useful but emotionally risky.

Implement:

- Split override into safe presets first:
  - "Return to battery default"
  - "Pause EMS for 6 hours"
  - "Hold battery for later"
  - "Charge now"
- Add a confirmation step for charge/discharge, but not for clearing override or returning to AUTO.
- Make the consequence explicit:
  - "This may buy grid power now."
  - "This may stop the battery powering the house."
  - "This ends automatically at 22:00."
- Show the recovery promise:
  - "After this expires, EMS follows the plan again."
- Disable or heavily warn on risky override modes when data quality is unsafe.
- Replace "tell the battery exactly what to do" with calmer language:
  - "Temporarily take over from the automatic plan."

#### Settings

Current state: capable but technical.

Implement:

- Add a **Setup** mode separate from Settings:
  - connect devices,
  - validate meters,
  - set solar array,
  - test battery read,
  - choose safe defaults.
- Keep Settings for later tuning.
- Add "Beginner / Advanced" rather than just "Show advanced settings".
- Add a guided **operational readiness panel** before the operational toggle:
  - live devices connected,
  - meter roles validated,
  - dry-run period completed,
  - battery target writes verified,
  - safe restore tested.
- For each setting group, show emotional framing:
  - Strategy: "How hands-off should EMS be?"
  - Battery: "How much comfort buffer do you want?"
  - Planner economics: "How picky should EMS be before cycling the battery?"
- Show impact previews for more than planner economics:
  - reserve changes,
  - summer grid top-up changes,
  - max top-up price,
  - usable capacity.

#### System Status

Current state: factual checklist.

Implement:

- Add an overall safety sentence:
  - "The battery is safe; EMS is only watching."
  - "Control is blocked until battery data returns."
  - "Live control is ready."
- Group checks by what the user cares about:
  - Home data,
  - Forecast and prices,
  - Battery control,
  - App storage/security.
- For warnings/failures, include the next action:
  - "Battery read failed. Check Indevolt IP or wait; EMS is in safe mode."
  - "Prices stale. EMS will avoid price-based charging."
- Distinguish "warning but still useful" from "control blocked".

#### Audit

Current state: transparent but log-like.

Implement:

- Add human outcomes to audit rows:
  - "Avoided grid import"
  - "Held battery for car charging"
  - "Paused because data was stale"
- Add filters by emotional question:
  - "Why did it change?"
  - "What did I change?"
  - "What did it save?"
  - "Problems"
- Add a plan replay link from audit entries to the exact plan/story around that decision.

#### Chat / AI

Current state: scoped and grounded, which is good.

Implement:

- Make AI boundaries even more emotionally reassuring:
  - "AI explains; it never controls."
  - "Answers use the current dashboard facts only."
- Add non-AI fallback question buttons that work with template explanations:
  - "Why not charging?"
  - "What happens tonight?"
  - "Is my battery safe?"
- In Dutch households, consider Dutch as a first-class tone option, not just a language toggle. The user may trust safety explanations more in their native language.

#### Visual Design

Current state: polished enough, but a little card-heavy and status-heavy.

Implement:

- Use color emotionally and consistently:
  - green = safe/doing well,
  - amber = attention/degraded,
  - red = blocked/problem,
  - blue = forecast/plan,
  - warm amber = solar.
- Reduce the number of competing badges in the top bar. Convert them into one coherent "state strip" when possible.
- Add subtle microcopy for empty/loading states:
  - "Reading your meters..."
  - "Building the next plan..."
  - "No history yet; this fills in after the app has watched for a day."
- Keep motion minimal and purposeful. Shimmer and chat typing are fine; avoid decorative animation.

## Loop 3 - Validation Against App Constraints

### What Fits The Current Architecture

These recommendations can be implemented without changing backend control logic:

- home state headline,
- better dashboard hierarchy,
- safer override copy and confirmation,
- system status grouping,
- richer empty/loading states,
- audit filters/copy,
- grounded template FAQ buttons,
- top-bar state strip,
- setup-vs-settings navigation split,
- more emotionally useful labels.

### What Requires Backend Support

These need API/model changes:

- deterministic "why Auto chose solar-first/price-smart",
- confidence score behind plan,
- "control-ready" versus "dashboard-ready",
- plan replay links,
- hard commissioning checklist,
- richer plan target fields,
- impact preview for reserve and top-up settings,
- safe override gating based on data quality.

### Emotional Design Risks To Avoid

1. **Do not make unsafe states sound cute.**
   "Paused - safe mode" is good. "Oops!" is not.

2. **Do not overpromise savings.**
   If savings are estimated, say estimated. If simulated, say demo/simulation.

3. **Do not hide risk behind friendly language.**
   Friendly copy should make risk understandable, not smaller.

4. **Do not let AI become the emotional authority.**
   The system's deterministic reason should be primary. AI can phrase, not decide.

5. **Do not celebrate cycling the battery unless it genuinely helped.**
   Positive reinforcement should be tied to outcomes: reserve respected, peak avoided, solar used.

## Prioritized Implementation Backlog

### P0 - Trust And Safety

1. Add a state headline that says whether the home is safe, watching, controlling, paused, or needs attention.
2. Rewrite manual override copy and add confirmation for charge/discharge actions.
3. Add control-readiness language to System status.
4. Ensure unsafe/degraded states always include "what happens now" and "what to do next".
5. Make simulation/live state impossible to miss.

### P1 - Agency And Understanding

1. Add plan summary at the top of the dashboard.
2. Add deterministic "why this strategy" explanation.
3. Add setup flow separate from settings.
4. Add impact previews for major energy settings.
5. Add template FAQ buttons that work without AI.

### P2 - Delight And Long-Term Trust

1. Add quiet success markers in Energy Story.
2. Add audit filters around human questions.
3. Add plan replay from audit/history.
4. Add calmer loading and empty states across all tabs.
5. Add Dutch tone/language review for all critical safety copy.

## Top 10 Emotional Design Recommendations

1. **Add a top-level home state headline.**
   The first read should answer: safe, watching, controlling, paused, or needs attention.

2. **Turn Manual Control into a guided override flow.**
   Use safe presets, consequence copy, expiry reassurance, and confirmations for risky actions.

3. **Split Setup from Settings.**
   Setup should guide confidence; Settings should tune an already-working system.

4. **Add "why this plan" and "why this strategy" explanations.**
   Make cause and effect visible: solar shortfall, price spread, reserve, car charging, stale data.

5. **Make failure states emotionally complete.**
   Every warning should say what is wrong, whether the battery is safe, what EMS will do now, and what the user can do.

6. **Use Energy Story as the emotional center of the app.**
   Add quiet trust markers: target reached, reserve respected, peak covered, no grid top-up needed.

7. **Make operational readiness explicit.**
   Do not let "control battery" feel like an ordinary toggle. It should feel like commissioning.

8. **Add grounded non-AI help prompts.**
   "Why not charging?", "What happens tonight?", "Is my battery safe?" should work even with AI off.

9. **Reframe metrics around homeowner outcomes.**
   Group raw watts under stories like "home now", "battery for tonight", and "grid avoided".

10. **Keep the tone calm, precise, and honest.**
    No hype, no cute error states, no exaggerated savings. The emotional promise is quiet competence.

## Bottom Line

The current UI already has the start of good emotional design: plain labels, energy story, skeleton loading, light/dark theme, audit, and explanations. The next step is to make the app feel less like a technical dashboard and more like a trustworthy home energy caretaker: clear state, guided setup, safe overrides, emotionally complete failure handling, and proof that the system helped.
