# Dashboard navigation

The web UI has one rule for where things live, so a homeowner always knows where to look and a
developer always knows where to add something.

| Surface | Answers | Examples |
| --- | --- | --- |
| **Dashboard** | The immediate question — *what is happening right now, and do I need to do anything?* | The hero verdict, the act line, the plan story, the score pills. |
| **Contextual drawers** | *Why* — the context behind what the Dashboard shows, without leaving it. | "What is EMS doing?", "Your savings", "How much to trust this", "What is the battery doing?", "Why did it act?" |
| **Manage** | Durable **configuration** you change rarely. | Settings, System, Audit. |
| **Insights** | **History** and trends over time. | Daily/weekly finance, forecast accuracy, energy distribution. |

Dashboard is the homeowner-first surface; technical measurements are always one tap deeper (a
drawer or a "Show technical details" disclosure), never the first thing you read.

## Contextual drawers

A drawer opens **over** the Dashboard — a right-side panel on desktop, a full-screen sheet on
mobile (the same state, presented differently by CSS; no separate mobile code path). Every drawer
opens with **What happened**, **Why**, and an explicit **Do I need to act?** answer, and states
plainly when the safe baseline is active (low confidence, stale data, or a fallback).

Each drawer is a **hash route**, so it is deep-linkable, survives a reload, and works with browser
Back:

| Drawer | Deep link | Opened from |
| --- | --- | --- |
| Now / Next / Why | `#dashboard/now` | the hero "What is EMS doing?" link |
| Savings | `#dashboard/savings` | the hero "See your savings" link |
| Confidence | `#dashboard/confidence` | the confidence chip |
| Battery detail | `#dashboard/battery` | the Battery tile / power tile |
| A specific decision | `#dashboard/decision/<id>` | a row in the recent-decisions timeline |

Unknown dashboard subroutes (e.g. a mistyped `#dashboard/foo`) fall back to the plain Dashboard —
a bad hash never blanks the app.

### Back / close behaviour

- **Opening** a drawer pushes a history entry. **Browser Back**, the **close button**, **Escape**,
  and (on mobile) the **Back affordance** all return to the plain Dashboard.
- A **deep-linked** drawer (opened as the first entry, e.g. from a bookmark) rewrites the hash to
  `#dashboard` in place on close, so you never get bounced off the app.
- **Refresh** keeps the drawer open — the state lives in the URL.
- Focus moves to the close button on open and is **restored to the trigger** on close; Tab is
  trapped within the dialog. The Dashboard stays mounted underneath, so its scroll position is
  preserved.

## Savings honesty

The savings drawer never fabricates a number:

- **Estimated** — today's plan-based figure, shown with a clearly-labelled band (never presented
  as measured).
- **Realized** — measured from recorded meters + the prices that were actually active, and only
  once a day is **complete**. The drawer states how many complete days back the realized figure.
- If there is no complete day yet, it says so, rather than inventing a measured amount.

## Where to add new UI

- A new "why" explanation → a **drawer** (add a `DrawerRoute` kind + a `#dashboard/<kind>` route).
- A new setting → **Manage → Settings**.
- A new trend/report → **Insights**.
- A new always-visible verdict → the **Dashboard** hero (keep it to one read).
