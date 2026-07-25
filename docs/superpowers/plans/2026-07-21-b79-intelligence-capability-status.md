# B-79 — Runtime-proven intelligence capability status — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Replace the hard-coded `INTELLIGENCE_MODE = "not_active"` string with a runtime-derived status (state + last-evaluation time/result + reason) that cannot lie, exposed via `/api/battery-plan` and a new `/api/intelligence`, with UI copy for all four states.

**Architecture:** A single evaluation-record box (`app.state.intelligence_box = {"latest": None}`, mirroring the existing `validation_box`) is the one seam. A pure helper `_intelligence_status()` derives the reported status from that record: empty → `not_active` with `last_evaluated_at: null`; a recorded evaluation → its state + timestamp. Nothing writes a non-`None` record today (the layer stays unwired — E-08), but the value is now derived from runtime state, so it self-corrects and can't assert a false capability.

**Tech Stack:** Python 3.12 / FastAPI, React + Vite (TSX), pytest + Playwright.

## Global Constraints

- **Honesty is the deliverable.** With the box empty, the status MUST be `not_active` with `last_evaluated_at: null` / `last_result: null`; no response field may ever equal `shadow_evaluation` / `advisory` / `active` unless a runtime record set it. Never assert a capability the runtime didn't record.
- **Non-goal:** do NOT wire the scenario/ML layer to actually evaluate (no calls to `plan_risk_aware_adaptive` / `build_planning_scenarios` from the live loop). That is the E-08 epic. This item builds only the status mechanism.
- **One backend source of truth:** `_intelligence_status()`. `/api/battery-plan` `provenance.intelligence` and `/api/intelligence` both return it; `System.tsx` reads the runtime value (not a static constant).
- **`provenance.intelligence` changes from a string to an object** `{state, last_evaluated_at, last_result, reason}` — a deliberate internal contract change; update all consumers + tests in this change.
- Leave the unrelated dangling `PlannerMode` enum (`ems/domain.py`) untouched (retiring it is B-85).
- Frontend bundle stays ≤ 300 KB gz.
- Four states: `not_active`, `shadow_evaluation`, `advisory`, `active`.

---

## File Structure

- `ems/domain.py` — new `IntelligenceState(StrEnum)`.
- `ems/web/api.py` — `intelligence_box` on `app.state`, `_INTELLIGENCE_REASONS`, `_intelligence_status()`, `_plan_provenance` change, remove `INTELLIGENCE_MODE`, new `GET /api/intelligence`.
- `ems/web/frontend/src/labels.ts` — 4-state `INTELLIGENCE_COPY`, drop `CURRENT_INTELLIGENCE_MODE`.
- `ems/web/frontend/src/BatteryPlan.tsx` — object type + render.
- `ems/web/frontend/src/System.tsx` — fetch `/api/intelligence`, render from it.
- Tests: `ems/tests/test_battery_plan_api.py` (update), `ems/tests/test_intelligence_status.py` (new), `ems/web/frontend/e2e/ui.spec.ts` (update).

---

### Task 1: Backend — runtime-derived status + `/api/intelligence`

**Files:**
- Modify: `ems/domain.py`
- Modify: `ems/web/api.py`
- Modify: `ems/tests/test_battery_plan_api.py`
- Create: `ems/tests/test_intelligence_status.py`

**Interfaces:**
- Produces: `ems.domain.IntelligenceState` (StrEnum, values `not_active`/`shadow_evaluation`/`advisory`/`active`).
- Produces: `app.state.intelligence_box = {"latest": None}` — the evaluation-record seam. `latest` is either `None` or `{"state": <str>, "ts": <iso str>, "result": <str|None>}`.
- Produces: `_intelligence_status() -> dict` returning `{"state","last_evaluated_at","last_result","reason"}`.
- Produces: `GET /api/intelligence` returning that dict (VIEW-tier).
- Changes: `provenance.intelligence` in `/api/battery-plan` from a string to that dict.

- [ ] **Step 1: Write the failing tests**

Create `ems/tests/test_intelligence_status.py` (reuse the app/client pattern from `test_battery_plan_api.py` — check how that file builds its `TestClient`/app and mirror it; the client's app is reachable as `client.app`):

```python
# Mirror test_battery_plan_api.py's app/client construction (import its helper or replicate it).
from fastapi.testclient import TestClient

# ... build `app` the same way test_battery_plan_api.py does (e.g. a _client()/_app() helper) ...


def _prov_intelligence(client):
    body = client.get("/api/battery-plan").json()
    return body["plan"]["provenance"]["intelligence"] if "plan" in body else body["provenance"]["intelligence"]
    # NOTE: match the ACTUAL battery-plan response shape used by test_battery_plan_api.py
    #       (that file already reads provenance — copy its exact access path).


def test_intelligence_status_default_is_not_active_object(client_factory):
    with client_factory() as c:
        prov = _prov_intelligence(c)
        assert isinstance(prov, dict)
        assert prov["state"] == "not_active"
        assert prov["last_evaluated_at"] is None
        assert prov["last_result"] is None
        assert isinstance(prov["reason"], str) and prov["reason"]


def test_api_intelligence_endpoint_shape(client_factory):
    with client_factory() as c:
        body = c.get("/api/intelligence").json()
        assert set(body) == {"state", "last_evaluated_at", "last_result", "reason"}
        assert body["state"] == "not_active"


def test_status_is_runtime_derived_not_a_constant(client_factory):
    # The anti-constant proof: inject a recorded evaluation into the runtime seam and the reported
    # status must reflect it (state + timestamp + result), via BOTH surfaces.
    with client_factory() as c:
        c.app.state.intelligence_box["latest"] = {
            "state": "shadow_evaluation",
            "ts": "2026-07-21T12:00:00+00:00",
            "result": "pessimistic vs baseline: -0.3 kWh",
        }
        ep = c.get("/api/intelligence").json()
        assert ep["state"] == "shadow_evaluation"
        assert ep["last_evaluated_at"] == "2026-07-21T12:00:00+00:00"
        assert ep["last_result"] == "pessimistic vs baseline: -0.3 kWh"
        prov = _prov_intelligence(c)
        assert prov["state"] == "shadow_evaluation"


def test_no_false_capability_claim_when_unrecorded(client_factory):
    with client_factory() as c:
        ep = c.get("/api/intelligence").json()
        assert ep["state"] not in {"shadow_evaluation", "advisory", "active"}
```

> `client_factory` is a placeholder for whatever construction `test_battery_plan_api.py` already uses — use the same mechanism (a fixture or a `_client()` context manager). The point: each test gets a fresh app whose `app.state.intelligence_box` starts `{"latest": None}`.

Update the two existing assertions in `ems/tests/test_battery_plan_api.py`:
- `test_battery_plan_carries_a_provenance_block_with_the_expected_shape` (~line 208-219): the `intelligence` value is now a dict, not `"not_active"`. Change the shape assertion so the top-level provenance keys are still `{"forecast_source","solar_confidence_pct","planner","intelligence"}`, and assert `prov["intelligence"]["state"] == "not_active"` and `prov["intelligence"]["last_evaluated_at"] is None`.
- `test_battery_plan_provenance_is_present_even_when_paused_safely` (~line 263-274): change `prov["intelligence"] == "not_active"` to `prov["intelligence"]["state"] == "not_active"`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/jeroenniesen/Development/EnergyManagementSystem/.claude/worktrees/auth-slice1 && uv run pytest ems/tests/test_intelligence_status.py ems/tests/test_battery_plan_api.py -v`
Expected: FAIL — `/api/intelligence` 404; `provenance.intelligence` is still the string `"not_active"` (so `["state"]` indexing / dict assertions fail).

- [ ] **Step 3: Add the enum**

In `ems/domain.py`, add near the other enums (leave `PlannerMode` untouched):

```python
class IntelligenceState(StrEnum):
    """Runtime capability state of the scenario/ML intelligence layer (B-79). Derived from a
    real evaluation record, never asserted — see ems.web.api._intelligence_status."""
    NOT_ACTIVE = "not_active"                # not wired / never evaluated
    SHADOW_EVALUATION = "shadow_evaluation"  # evaluated alongside the plan, never steering
    ADVISORY = "advisory"                    # surfaced as advice, still not steering
    ACTIVE = "active"                        # actually steering the plan
```

- [ ] **Step 4: Add the box, reasons, helper; change provenance; remove the constant; add the endpoint**

In `ems/web/api.py`:

Import the enum (add to the existing `from ems.domain import ...`): `IntelligenceState`.

Remove the constant `INTELLIGENCE_MODE = "not_active"` (line ~638) and its comment block.

Add the reasons map at module scope (near where `INTELLIGENCE_MODE` was):

```python
_INTELLIGENCE_REASONS = {
    IntelligenceState.NOT_ACTIVE.value: (
        "The scenario/ML intelligence layer is built but not wired into the live path; "
        "the dependable deterministic planner produced this plan."
    ),
    IntelligenceState.SHADOW_EVALUATION.value: (
        "The intelligence layer evaluated this cycle for comparison only — it did not steer the plan."
    ),
    IntelligenceState.ADVISORY.value: (
        "The intelligence layer produced advice this cycle — the deterministic planner still steers."
    ),
    IntelligenceState.ACTIVE.value: (
        "The intelligence layer is steering the plan."
    ),
}
```

Inside `create_app`, next to `validation_box` (~line 970), add the seam and expose it for the runtime to record into (and for tests to inject):

```python
    intelligence_box: dict[str, Any] = {"latest": None}
    app.state.intelligence_box = intelligence_box
```

Add the derivation helper inside `create_app` (near `_plan_provenance`):

```python
    def _intelligence_status() -> dict:
        """Runtime-derived intelligence capability status (B-79). Empty record -> not_active with
        no evaluation; a recorded evaluation -> its state/timestamp/result. The value is DERIVED
        from `intelligence_box`, never a constant, so it cannot claim a capability the runtime did
        not record. Populating the record (shadow/advisory/active) is E-08 work."""
        latest = intelligence_box["latest"]
        if latest is None:
            state = IntelligenceState.NOT_ACTIVE.value
            return {
                "state": state,
                "last_evaluated_at": None,
                "last_result": None,
                "reason": _INTELLIGENCE_REASONS[state],
            }
        state = latest["state"]
        return {
            "state": state,
            "last_evaluated_at": latest.get("ts"),
            "last_result": latest.get("result"),
            "reason": _INTELLIGENCE_REASONS.get(state, ""),
        }
```

Change `_plan_provenance` (~line 2864-2875) so its `intelligence` field calls the helper:

```python
    def _plan_provenance(strategy: str) -> dict:
        return {
            "forecast_source": _forecast_source_label(),
            "solar_confidence_pct": settings_cache["planner.solar_confidence"],
            "planner": _resolved_planner_name(strategy),
            "intelligence": _intelligence_status(),
        }
```

Add the endpoint (mirror the sibling `/api/explainer` at ~line 3584 — same decorator style/tier; `/api/intelligence` matches neither `_ADMIN_PREFIXES` nor `OPERATE_PATHS`, so it resolves to VIEW automatically):

```python
    @app.get("/api/intelligence")
    async def intelligence_status_endpoint() -> dict:
        """B-79: runtime-proven capability state of the scenario/ML layer (state + last-eval
        time/result + reason). Never claims a capability the runtime hasn't recorded."""
        return _intelligence_status()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest ems/tests/test_intelligence_status.py ems/tests/test_battery_plan_api.py ems/tests/test_domain.py -v`
Expected: PASS. Then the full suite: `uv run pytest ems -q` (no extra `-q` beyond pyproject's — run `uv run pytest ems` if you need the summary line) — 0 failures. Also `uv run ruff check ems` clean.

- [ ] **Step 6: Commit**

```bash
git add ems/domain.py ems/web/api.py ems/tests/test_battery_plan_api.py ems/tests/test_intelligence_status.py
git commit -m "feat(b79): runtime-derived intelligence status + /api/intelligence (honesty mechanism)"
```

---

### Task 2: Frontend — four-state copy, object shape, System reads runtime

**Files:**
- Modify: `ems/web/frontend/src/labels.ts`
- Modify: `ems/web/frontend/src/BatteryPlan.tsx`
- Modify: `ems/web/frontend/src/System.tsx`
- Modify: `ems/web/frontend/e2e/ui.spec.ts`

**Interfaces:**
- Consumes: `/api/battery-plan` `provenance.intelligence` object; `GET /api/intelligence` object (Task 1).

- [ ] **Step 1: Write/adjust the failing e2e tests**

In `ems/web/frontend/e2e/ui.spec.ts`:

Update the `DEFAULT_PROVENANCE` fixture (~line 45-50) so `intelligence` is the object shape:

```ts
const DEFAULT_PROVENANCE = {
  forecast_source: "Solcast",
  solar_confidence_pct: 85,
  planner: "rule_based",
  intelligence: { state: "not_active", last_evaluated_at: null, last_result: null,
    reason: "The scenario/ML intelligence layer is built but not wired into the live path; the dependable deterministic planner produced this plan." },
};
```

The provenance-line test (~line 738-761) still asserts `toContainText("scenario intelligence: not active yet")` — keep that assertion (the short copy for `not_active` is unchanged), it now flows from `intelligence.state`.

The Model-health test (~line 1728-1744) must now mock `GET /api/intelligence` (System fetches it) returning `{state:"not_active", last_evaluated_at:null, last_result:null, reason:"...not wired..."}`, and still assert the `health-planning-intelligence` row contains the `not_active` label and the `planning-intelligence-note` contains its detail/reason, plus the muted `.dot-unknown` styling. Add the route mock alongside the test's other `page.route(...)` mocks.

- [ ] **Step 2: Run to verify failure**

Run: `cd ems/web/frontend && EMS_E2E_APP_PORT=8091 EMS_E2E_AUTH_PORT=8092 npx playwright test e2e/ui.spec.ts -g "provenance|Planning intelligence"`
Expected: FAIL (type/shape mismatch once fixtures are objects but code still indexes a string; System has no `/api/intelligence` fetch yet). If those ports are busy, pick another free high pair and note it.

- [ ] **Step 3: Expand the copy map (labels.ts)**

In `ems/web/frontend/src/labels.ts`, replace the single-entry `INTELLIGENCE_COPY` with all four states and delete `CURRENT_INTELLIGENCE_MODE` (System now reads runtime):

```ts
export const INTELLIGENCE_COPY: Record<string, { label: string; detail: string; short: string }> = {
  not_active: {
    label: "Planning intelligence",
    detail: "not active; the dependable baseline plans today",
    short: "not active yet",
  },
  shadow_evaluation: {
    label: "Planning intelligence",
    detail: "evaluating in shadow — comparing against the baseline, not steering",
    short: "shadow (not steering)",
  },
  advisory: {
    label: "Planning intelligence",
    detail: "advisory — surfacing suggestions; the baseline still steers",
    short: "advisory",
  },
  active: {
    label: "Planning intelligence",
    detail: "active — steering the plan",
    short: "active",
  },
};
```

(Remove the `CURRENT_INTELLIGENCE_MODE` export and update the doc-comment to say the current state is now runtime-derived from `/api/intelligence`, not a constant here.)

- [ ] **Step 4: Object shape + render (BatteryPlan.tsx)**

Change the `PlanProvenance` type (~line 22-27):

```tsx
export type PlanProvenance = {
  forecast_source: string;
  solar_confidence_pct: number;
  planner: "rule_based" | "adaptive" | "summer";
  intelligence: {
    state: string;
    last_evaluated_at: string | null;
    last_result: string | null;
    reason: string;
  };
};
```

Update the render (~line 357-363) to read `.state` and use the runtime `reason` as the tooltip:

```tsx
          <span title={plan.provenance.intelligence.reason}>
            scenario intelligence:{" "}
            {INTELLIGENCE_COPY[plan.provenance.intelligence.state]?.short ??
              plan.provenance.intelligence.state}
          </span>
```

- [ ] **Step 5: System reads the runtime status (System.tsx)**

Remove the `CURRENT_INTELLIGENCE_MODE` import (keep `INTELLIGENCE_COPY`). Add state + a fetch mirroring the existing `/api/diagnostics|incidents|accuracy` `useEffect` blocks (~line 298-352):

```tsx
  const [intelligence, setIntelligence] = useState<
    { state: string; last_evaluated_at: string | null; last_result: string | null; reason: string } | null
  >(null);

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const r = await apiFetch("/api/intelligence");
        if (!r.ok) return;
        const b = await r.json();
        if (alive) setIntelligence(b);
      } catch {
        /* health row degrades to muted/unknown if this read fails */
      }
    }
    load();
    return () => {
      alive = false;
    };
  }, []);
```

Render the row (~line 506-518) from the runtime value, keeping the testids. Muted `dot-unknown` for `not_active` (or when unknown/not-yet-loaded); the note shows the runtime `reason`:

```tsx
            <li
              className={`health-row ${intelligence && intelligence.state !== "not_active" ? "health-ok" : "health-unknown"}`}
              data-testid="health-planning-intelligence"
            >
              <span
                className={`check-dot ${intelligence && intelligence.state !== "not_active" ? "dot-ok" : "dot-unknown"}`}
                aria-hidden="true"
              />
              <span className="health-label">
                {INTELLIGENCE_COPY[intelligence?.state ?? "not_active"]?.label ?? "Planning intelligence"}
              </span>
              <span className="health-value">
                {INTELLIGENCE_COPY[intelligence?.state ?? "not_active"]?.short ?? "—"}
              </span>
              <span
                className="health-note planning-intelligence-note"
                data-testid="planning-intelligence-note"
              >
                {intelligence?.reason ??
                  INTELLIGENCE_COPY[intelligence?.state ?? "not_active"]?.detail}
              </span>
            </li>
```

> The existing test asserts the note contains "not active; the dependable baseline plans today" (the `not_active` **detail**). With the runtime `reason` now shown, either (a) keep the note bound to `INTELLIGENCE_COPY[state].detail` (so the existing copy assertion holds), or (b) update the e2e assertion to the runtime `reason` text. Pick ONE and make the test and render agree — prefer (a) (bind the note to `.detail`) to minimize copy churn, and drop the `?? reason` fallback if you choose (a). Keep it internally consistent.

- [ ] **Step 6: Verify build + e2e**

Run: `cd ems/web/frontend && npx tsc --noEmit && npm run build && EMS_E2E_APP_PORT=8091 EMS_E2E_AUTH_PORT=8092 npx playwright test e2e/ui.spec.ts -g "provenance|Planning intelligence"`
Expected: tsc clean; build under the 300 KB gz budget (report gz sizes); e2e PASS.

- [ ] **Step 7: Commit**

```bash
git add ems/web/frontend/src/labels.ts ems/web/frontend/src/BatteryPlan.tsx ems/web/frontend/src/System.tsx ems/web/frontend/e2e/ui.spec.ts
git commit -m "feat(b79-web): four-state intelligence copy + object shape; System reads /api/intelligence"
```

---

## Self-Review

**Spec coverage:** §3.1 enum → Task 1 Step 3. §3.2 box → Task 1 Step 4. §3.3 helper → Task 1 Step 4. §3.4 API (provenance object + `/api/intelligence`) → Task 1 Steps 4-5. §3.5 frontend (labels/BatteryPlan/System) → Task 2. §4 testing → Task 1 Step 1 (default, runtime-proven injection, no-false-claim, endpoint shape) + Task 2 Step 1 (fixtures, provenance line, System health). All covered.

**Placeholder scan:** the `client_factory` / `_prov_intelligence` access-path notes are deliberate deferrals to `test_battery_plan_api.py`'s existing harness (the implementer copies the real construction + provenance access path rather than my guessing them) — flagged explicitly, not silent TODOs. The System note (a) vs (b) is a bounded either/or with a stated preferred choice, not an open question.

**Type/name consistency:** `IntelligenceState` values, `intelligence_box["latest"] = {state,ts,result}`, `_intelligence_status()` → `{state,last_evaluated_at,last_result,reason}`, `provenance.intelligence` object, `INTELLIGENCE_COPY[state]`, `/api/intelligence` — used identically across both tasks. The record's `ts`/`result` keys map to the response's `last_evaluated_at`/`last_result` in the helper (Task 1 Step 4) and the injection test uses the same `ts`/`result` keys (Task 1 Step 1).
