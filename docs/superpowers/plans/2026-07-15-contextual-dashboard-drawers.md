# Contextual Dashboard Drawers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Redesign the EMS dashboard so homeowners can quickly understand what the system is doing, why it acted or did not act, and what financial benefit it created, while keeping technical detail available through a consistent contextual drawer.

**Architecture:** Keep the existing top-level views (`Dashboard`, `Insights`, `Car`, `Chat`, `Manage`). Add one reusable `DetailDrawer` controlled by `App.tsx` route state. Desktop renders a right-side drawer over the dashboard; mobile renders the same state as a full-screen subview with a back button. Hash routes make drawers refreshable and browser-back compatible.

**Tech Stack:** React 18, TypeScript, Vite, CSS, existing hash routing, Playwright, existing EMS JSON endpoints.

## Global Constraints

- Dashboard remains the homeowner-first surface; technical measurements use progressive disclosure.
- Every drawer starts with `What happened`, `Why`, and `Do I need to act?`.
- Low confidence, stale data, and fallback mode explicitly say the safe baseline remains active.
- Escape, close, browser Back, mobile Back, and refresh behave predictably.
- No new backend dependency or external analytics service.
- Existing top-level navigation and legacy hashes remain compatible.
- Do not modify unrelated dirty planner, validator, replay, or what-if changes.

## Drawer contract

| Trigger | Route | User question | First-screen content |
| --- | --- | --- | --- |
| Now/Next/Why story | `#dashboard/now` | What is EMS doing? | Current action, next action, reason, action-needed answer |
| Savings card | `#dashboard/savings` | Is it saving money? | Estimate, range, realized/estimated state, calculation link |
| Decision event | `#dashboard/decision/<id>` | Why did it act or not act? | Event, reason, consequence, action/no-action answer |
| Confidence badge | `#dashboard/confidence` | How much should I trust this? | Plain meaning, evidence reason, safety statement |
| Battery detail | `#dashboard/battery` | What is the battery doing? | State, power flow, reserve, per-tower details |

---

### Task 1: Reusable drawer shell

**Files:** Create `ems/web/frontend/src/DetailDrawer.tsx`; modify `ems/web/frontend/src/App.tsx` and `ems/web/frontend/src/styles.css`; test `ems/web/frontend/e2e/ui.spec.ts`.

**Interfaces:** `DetailDrawerProps` is `{open, title, eyebrow?, onClose, children, testId}`. `App.tsx` owns `drawer: DrawerRoute | null`; `DetailDrawer` owns dialog presentation and focus behavior.

- [ ] Add a failing Playwright test that opens `[data-testid=detail-drawer]`, focuses `[data-testid=detail-drawer-close]`, and closes on Escape.
- [ ] Run `npx playwright test e2e/ui.spec.ts -g "drawer opens" --reporter=line`; verify it fails because the component and trigger do not exist.
- [ ] Implement a semantic `role="dialog"`, `aria-modal="true"`, labelled heading, close button, backdrop, focus restoration, Escape handling, and body-scroll lock. Use a right-side desktop panel and full-width mobile panel.
- [ ] Run the focused test and `npm run build`; verify both pass.
- [ ] Commit `feat(web): add contextual dashboard drawer shell`.

### Task 2: Drawer routing and deep links

**Files:** Modify `ems/web/frontend/src/App.tsx`; test `ems/web/frontend/e2e/ui.spec.ts`.

**Interfaces:** Extend the existing hash route parser with `#dashboard/now`, `#dashboard/savings`, `#dashboard/confidence`, `#dashboard/battery`, and `#dashboard/decision/<id>`. Unknown drawer routes resolve to Dashboard.

- [ ] Add a failing test that opens `#dashboard/now`, reloads, confirms the drawer remains open, then uses browser Back to close it.
- [ ] Run `npx playwright test e2e/ui.spec.ts -g "drawer route" --reporter=line`; verify RED.
- [ ] Implement route serialization through the existing hash helper. Preserve dashboard scroll position and restore focus to the opening trigger. Keep legacy `#settings`, `#system`, and `#audit` behavior.
- [ ] Run route and legacy navigation tests; verify GREEN.
- [ ] Commit `feat(web): deep-link contextual dashboard details`.

### Task 3: Now / Next / Why drawer

**Files:** Modify `ems/web/frontend/src/App.tsx` and `ems/web/frontend/src/labels.ts`; test `ems/web/frontend/e2e/ui.spec.ts`.

**Interfaces:** Add pure `nowDrawerCopy(...)` returning `{happened, why, next, action, calm}` from existing `home`, `decision`, `batteryPlan`, `confidence`, `alertsData`, and `status` values.

- [ ] Add failing fixtures for normal operation, low confidence, and safe fallback. Assert `drawer-happened`, `drawer-why`, and `drawer-action` exist.
- [ ] Run `npx playwright test e2e/ui.spec.ts -g "Now / Next / Why" --reporter=line`; verify RED.
- [ ] Make the hero story open `#dashboard/now` via keyboard-accessible trigger `[data-testid=dashboard-now-trigger]`. Use homeowner copy: “Battery is powering your home”, “Charging is planned for the lower-cost period”, and “No action needed”. Put raw intent and mode in a technical disclosure.
- [ ] Run `npm run build` and the focused tests; verify GREEN.
- [ ] Commit `feat(web): explain current and next energy actions`.

### Task 4: Savings and decision data contract

**Files:** Inspect and, if required, modify `ems/web/api.py`, `ems/web/routes/accuracy.py`, `ems/web/routes/export.py`, and the existing finance/audit storage readers; create or modify `ems/web/frontend/src/DecisionTimeline.tsx`, `ems/web/frontend/src/App.tsx`, and `ems/web/frontend/src/styles.css`; tests `ems/tests/test_accuracy_api.py`, relevant finance/audit tests, and `ems/web/frontend/e2e/ui.spec.ts`.

**Interfaces:** The backend must expose enough data for `DecisionEvent[]` with `{id, time, title, reason, consequence, action, severity}` and a savings object with `{today, month, estimate, realized, lower_bound, upper_bound, complete_days}`. If an existing endpoint cannot provide a field, add it to the existing authenticated response rather than fabricating it in the browser. `DecisionTimeline` consumes the event contract and savings distinguishes `estimated`, `realized`, and `unknown`.

- [ ] Add failing backend tests proving the response distinguishes estimated savings from realized savings, reports the number of complete days, and returns structured decision events for an executed plan, an economic skip, a safety fallback, and a no-action event.
- [ ] Add failing frontend tests for estimated savings, realized savings, missing complete-day evidence, economic skip, safety fallback, and “No action needed”.
- [ ] Run `npx playwright test e2e/ui.spec.ts -g "savings drawer|decision timeline" --reporter=line`; verify RED.
- [ ] Implement or extend the authenticated backend response using existing finance and audit storage; preserve the existing safety and privacy boundaries. Add a Dashboard savings trigger and a recent-decisions timeline. Drawer order is `What happened`, `Why`, consequence, and action/no-action. Put formulas behind “How this was calculated”.
- [ ] Include explicit examples for skipped actions: “Prices were above the break-even point” and “The safe baseline remains active”.
- [ ] Run `npm run build` and focused tests; verify GREEN.
- [ ] Commit `feat(web): explain savings and planner decisions`.

### Task 5: Confidence and battery drawers

**Files:** Modify `ems/web/frontend/src/App.tsx`, `ems/web/frontend/src/System.tsx`, `ems/web/frontend/src/BatteryChips.tsx`, and `ems/web/frontend/src/labels.ts`; test `ems/web/frontend/e2e/ui.spec.ts`.

**Interfaces:** Confidence consumes existing `PlanConfidence`, `ModelHealth`, and advisor data. Battery detail wraps `BatteryChips` without changing its API.

- [ ] Add failing tests for Low/Medium/High explanations, the safe-baseline statement, per-tower battery detail, close behavior, and technical disclosure.
- [ ] Run `npx playwright test e2e/ui.spec.ts -g "confidence drawer|battery drawer" --reporter=line`; verify RED.
- [ ] Move the existing battery modal and confidence entry point into the common drawer pattern. Use “Recent forecasts are tracking well”, “Useful for planning, but cautious”, and “The safe baseline is active”. Keep bias, MAPE, hit rate, and per-tower values below “Show technical details”.
- [ ] Run `npm run build` and the focused Model Health/battery tests; verify GREEN.
- [ ] Commit `feat(web): unify confidence and battery detail drawers`.

### Task 6: Mobile, accessibility, and visual polish

**Files:** Modify `ems/web/frontend/src/DetailDrawer.tsx` and `ems/web/frontend/src/styles.css`; test `ems/web/frontend/e2e/drawer.spec.ts` (create if needed).

- [ ] Add failing tests for desktop right alignment, mobile full-screen presentation, focus trapping, Escape, reduced motion, accessible labels, and focus restoration.
- [ ] Run `npx playwright test e2e/drawer.spec.ts --reporter=line`; verify RED.
- [ ] Implement CSS media-query presentation, `prefers-reduced-motion: reduce`, focus trap, close/back affordance, and dashboard scroll preservation. Do not branch UI logic on viewport width.
- [ ] Run `npm run build` and `npx playwright test e2e/drawer.spec.ts e2e/ui.spec.ts --reporter=line`; verify GREEN.
- [ ] Commit `feat(web): polish dashboard detail subviews`.

### Task 7: Documentation and acceptance

**Files:** Create `docs/dashboard-navigation.md`; modify `README.md` only if a user-facing link is needed.

- [ ] Document the rule: Dashboard answers the immediate question, drawers explain context, Manage changes durable configuration, and Insights provides history.
- [ ] Document deep-link examples and desktop/mobile back behavior.
- [ ] Run the full frontend suite: `npm run build && npx playwright test --reporter=line`.
- [ ] Run relevant backend contract checks: `UV_CACHE_DIR=.uv-cache uv run pytest ems/tests/test_accuracy_api.py ems/tests/test_settings.py ems/tests/test_replay.py -q`.
- [ ] Run `git diff --check`, inspect `git status --short`, and confirm unrelated planner/validator/replay/what-if edits are excluded.
- [ ] Commit `docs: describe contextual dashboard navigation`.

## Acceptance criteria

- A homeowner can answer “what is happening?” within five seconds of opening Dashboard.
- One tap explains the current action without losing dashboard context.
- Every drawer states whether action is needed.
- Savings are labelled estimated or realized and never fabricated.
- Technical evidence is available but is not the first emotional signal.
- Browser Back, Escape, mobile Back, refresh, deep links, and focus behavior are consistent.
- Existing navigation, legacy hashes, and current tests remain intact.
