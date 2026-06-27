# GOAL — Smart Energy Manager (HEMS)

> **North star:** build a home energy manager that is **trustworthy, beautiful, and self-explanatory** — a system you can hand to a curious person who has never seen it, and within a minute they understand *what it is doing with their battery and why.* It runs entirely on hardware you already own, never does anything reckless with the battery, and is a pleasure to look at and to maintain.
>
> This document is the **why and the bar** — the intent we hold every decision against. [`SPEC.md`](./SPEC.md) is the **how** (the detailed, source-of-truth specification); the `docs/` folder holds the supporting references. The spec has been **reconciled to this goal** (iteration 6): the React+Vite UI (`SPEC §9.1`), the optional ML layer ([`docs/ml-layer.md`](./docs/ml-layer.md)), and the Jetson target ([`docs/jetson-deployment.md`](./docs/jetson-deployment.md)) are now specified. Where goal and spec ever diverge again, this goal states the *intent* and the spec must be updated to match.

---

## 1. What we are building, and the commitment

We are going to build the application described in `SPEC.md`: a **mode-switching** home energy management system that smart-charges an Indevolt battery from a solar forecast and dynamic Tibber prices — running the house on stored solar overnight in summer, and arbitraging cheap/expensive price windows in winter. It decides *which mode the battery should be in*, a few times an hour, and only acts when the right mode changes.

This is a commitment to **finish it well**, not just to make it work. "Works" is the floor. The goal is an application that is **safe by construction, explains itself completely, looks intentional, and stays easy to change.**

---

## 2. The experience: a UI that is beautiful — and explains itself

The interface is not a dashboard of raw numbers. It is the system *teaching you what it is doing.*

- **Beautiful and intentional.** A **React + Vite** SPA, **built and bundled locally, no runtime CDN** — it still runs on the LAN with HA down. (A deliberate upgrade from the spec's original vanilla/vendored UI, now reflected in `SPEC §9.1`.) Beauty here means **considered** typography, spacing, color, hierarchy, and motion — never templated defaults. Every screen should feel like someone *chose* how it looks.
- **Respect the user.** Their home, their energy, their money, their data. The UI is honest about uncertainty, never hides a fallback or a stale reading, and never asks them to trust a number they can't interrogate.
- **Everything displayed is explained.** Every metric, graph, badge, and control carries an accessible explanation — what it is, where it came from, how fresh it is, and why it matters. No mystery numbers, no unlabelled jargon.
- **The application explains itself.** It states not only *what* it is doing but *why* — and, crucially, **why it is *not* acting** ("holding: the spread doesn't beat battery wear today"). First-run onboarding, a setup wizard with pass/fail checks, and a "here's what I would have done today" dry-run summary mean a new user is never lost.
- **Always visible: trust signals.** Dry-run vs. live, fallback-active, per-signal data freshness, plan data-quality, and the current ownership state are never more than a glance away.

The test of the UI: **a person who has never read the spec can look at the screen and correctly explain what the battery is doing and why.**

---

## 3. Architecture: robust, and built for stability

The system touches a real battery in a real home. Stability is not a feature — it is the foundation.

- **Fail-safe by construction.** If anything is stale, implausible, or uncertain, fall back to the battery's own self-consumption mode. The system is **never worse than having no EMS at all.** (`SPEC.md` §8.8, §16.)
- **Observe before acting; validate before applying.** Boot into observation, validate sensors and the device surface, load a plan, and only then — after a grace period — act. Every plan is versioned and must pass a validator before it can move the battery. (`SPEC.md` §8.11, §13.)
- **Don't fight the hardware.** The battery owns its own fast power control (P1 zeroing); we set *intent*, not instantaneous power. We switch modes rarely and deliberately. (`SPEC.md` §2, §7.)
- **Local-first.** It runs entirely on a **Raspberry Pi or an Nvidia Jetson** on the local network. No cloud dependency for the core control loop; the data and the decisions stay on the device.
- **Designed to recover.** Survives reboots, power loss, HA outages, and missed windows — each with a defined, tested behaviour. (`docs/failure-modes.md`.)

---

## 4. Code that stays maintainable and self-explained — KISS & SOLID

The codebase should read like an explanation of the system, the same way the UI does.

- **KISS.** The simplest design that satisfies the spec. No speculative abstraction, no model-predictive control loop, no cleverness for its own sake. YAGNI is enforced: v1 stays v1.
- **SOLID.**
  - *Single responsibility:* each module does one job (the spec's module tree, `SPEC.md` §13).
  - *Open/closed & dependency inversion:* the planner depends on **ports** (interfaces) — a forecast provider, a price source, a battery driver, an explainer — not concrete vendors. This is why the battery is "one writer behind one interface," why Solcast/Forecast.Solar are interchangeable, and why ML (§5) can slot in without touching the core.
  - *Liskov & interface segregation:* a statistical baseline and an ML model satisfy the *same* small interface; nothing downstream knows or cares which is running.
- **Self-explained.** Names, structure, and tests document intent. Tests are first-class: planners are deterministic and unit-tested with canned inputs; a fake battery adapter means no hardware in tests. (`SPEC.md` §14.) Code reads like the surrounding code — consistent and unsurprising.

---

## 5. Optional intelligence — helpful, never required

We want to use whatever silicon is on hand to make the system *better* — without ever making the Pi version *worse*.

ML is an **optional enhancement layer behind the same ports as everything else (§4)** — now specified in [`docs/ml-layer.md`](./docs/ml-layer.md), deployed per [`docs/jetson-deployment.md`](./docs/jetson-deployment.md). On a plain Pi it simply isn't loaded; the proven CPU methods run instead. It is **accelerator-agnostic**: **CUDA** on a Jetson, **Metal / Core ML / MLX** on Apple Silicon (which makes a Mac a first-class ML dev box — `SPEC.md` §11.6). A runtime **planner-mode switch** (`rule_based` · `ml` · `advisory`) lets you compare the ML plan against the rule-based one in the UI before trusting it. Where an accelerator is present it powers:

- **Load forecasting** — a learned household-consumption model that beats the rolling weekday+hour average.
- **Schedule optimization** — a learned planner that can run *instead of* the rule-based one (or, in `advisory` mode, alongside it for comparison), anticipating price shapes and smoothing the SoC path.
- **Self-explanation** — an LLM that turns decisions and data into the natural-language "why" the UI shows: either a **local** LLM (on an accelerator) or, so **even a plain Pi can be a bit smarter**, an **external** LLM API (e.g. MiniMax) — no GPU needed.

Non-negotiables keep this safe:
1. **ML proposes, the safety layer disposes.** Every ML output still passes the **plan validator and all guardrails** (`SPEC.md` §8.11). ML can never bypass fail-safe, reserve floors, write caps, or the "don't fight vendor control" contract.
2. **Graceful degradation.** If a model/accelerator/LLM is absent, slow, or low-confidence, the system falls back to the statistical/rule-based/template path automatically and says so in the UI.
3. **Still explainable.** An LLM may *phrase* the reason, but the reason itself is derived from the deterministic plan — the explanation can always be traced back to real numbers, never invented.
4. **Local-first, with one bounded, opt-in exception.** The **external** LLM explainer is the *only* feature that sends data off the device. It is **off by default**, sends only a **minimal redacted payload** (the reason + the few numbers it cites — never raw history, location, or secrets), never touches control, and falls back to the offline template. Everything else — telemetry, training, decisions — stays on the device (principle 4 below).

---

## 6. How we reach "perfect": the loops

We polish to "perfect" with two complementary cycles, each gated by **technical inspection (reviews)** and **visual experience (tests)**:

- **Per-area iterative dev loops (up to 3× each):** for every area we build, iterate
  **build/refine → technical code review → visual experience test → refine** — until that area passes *both* gates. We don't move on while either gate is red.
- **Whole-app global polish passes (3×):** once the app is assembled, run **3 full passes over the entire application**, each one a **complete technical review** *and* a **complete visual/UX test**. These catch the cross-cutting things per-area loops miss: consistency, coherence, and whether the *whole* product explains itself.

What each gate means:
- **Technical inspection (reviews):** correctness and edge cases, fail-safe behaviour, security, and adherence to KISS/SOLID and the spec. Driven by review agents, with human checkpoints.
- **Visual experience (tests):** run the real app and *look* — visual polish, responsiveness, accessibility, and the core test from §2: **can someone understand what's happening and why, just by looking?**

The work is agent-driven where it helps (fan-out reviews, scripted visual checks) but the human stays in the loop and signs off the gates.

---

## 7. Definition of "perfect"

We are done when all of these are true:

- **Safe:** every failure mode in `docs/failure-modes.md` has a tested, fail-safe behaviour; the system runs unattended for weeks without doing anything reckless to the battery.
- **Self-explanatory:** every displayed element is explained, every decision (and non-decision) carries a traceable reason, and a first-time user can narrate what the system is doing.
- **Beautiful:** the UI survives all 3 global visual-polish passes — intentional, consistent, and a pleasure to use.
- **Maintainable:** KISS/SOLID hold up under review; the test suite is green and reads as documentation; adding a new battery, price source, or forecast provider touches one adapter.
- **Portable:** the same codebase runs on a Raspberry Pi (CPU-only, fully featured core), an Nvidia Jetson (CUDA ML), and Apple Silicon (Metal/CoreML/MLX — the dev/test box); the accelerator is detected, not assumed.
- **Honest:** it is never worse than "no EMS," and it always tells the truth about what it knows.

---

## Guiding principles (the short list)

1. **Never worse than no EMS.** Fail safe, always.
2. **Explain everything** — what, why, and why-not.
3. **Set intent, don't fight the hardware.**
4. **Local-first; the user owns their data** — one bounded, opt-in exception: the external LLM explainer (§5).
5. **KISS & SOLID** — simple, swappable, self-explained.
6. **ML helps, never rules** — and never bypasses the safety layer.
7. **Beautiful is part of correct** — an intentional UI is a feature, not a finish.
