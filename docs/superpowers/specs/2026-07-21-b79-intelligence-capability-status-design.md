# B-79 — Truthful intelligence capability status (runtime-proven)

**Status:** design (approach approved 2026-07-21) · **Item:** B-79 (E-09, P1, Bug·S — second half; the `shadow`→`not_active` label fix already shipped in PR #38) · **Base:** `main` @ `f6791ae`.

## 1. Problem

`/api/battery-plan` reports the scenario/ML "intelligence" layer's status from a **hard-coded module constant** `INTELLIGENCE_MODE = "not_active"` (`ems/web/api.py:638`), and `System.tsx` renders a *second* static copy (`labels.ts` `CURRENT_INTELLIGENCE_MODE`). Neither is derived from what the runtime actually did, so:
- the label would keep saying `not_active` even if someone wired the layer (a latent false claim), and
- "truthfulness" depends on remembering to edit three constants in sync (the `labels.ts` doc-comment even documents this footgun).

The intelligence layer (`ems/intelligence/planning.py`) is **deliberately unwired** — the live planner is the deterministic `build_plan`; only tests call the scenario code. So the only truthful state today is `not_active`.

## 2. Goal (and non-goal)

Make the status **runtime-derived and impossible to falsify**: computed each request from a real runtime record of whether/how the intelligence layer evaluated, exposing the state + last-evaluation time/result, with UI copy that distinguishes all four states.

**Non-goal (explicit):** actually running shadow/advisory/active evaluation. Wiring the scenario layer into the live loop is the E-08 predictive-optimization epic. This item builds only the *honest status mechanism*; today it truthfully reports `not_active`, but self-corrects the instant an evaluation is ever recorded.

## 3. Approach

### 3.1 State vocabulary (`ems/domain.py`)
Add a `StrEnum`:
```python
class IntelligenceState(StrEnum):
    NOT_ACTIVE = "not_active"          # layer not wired / never evaluated
    SHADOW_EVALUATION = "shadow_evaluation"  # evaluated alongside, never steering
    ADVISORY = "advisory"             # surfaced as advice, still not steering
    ACTIVE = "active"                 # actually steering the plan
```
Leave the dangling `PlannerMode` (rule_based/ml/advisory) untouched — it's a different, unrelated taxonomy; retiring it is B-85's job, not this bug.

### 3.2 Runtime record (the "proof")
A single closure-local box in `create_app`, mirroring the existing `validation_box` pattern (`api.py:970`):
```python
intelligence_box: dict[str, Any] = {"latest": None}
```
`latest` is `None` until something records an evaluation. When the E-08 work wires the layer, it will set
`intelligence_box["latest"] = {"state": <IntelligenceState>, "ts": <iso>, "result": <short str>}` — and nothing else changes. **This box is the single seam**; there is no status string to edit.

### 3.3 Derivation helper (`ems/web/api.py`)
Replace the `INTELLIGENCE_MODE` constant and inline `_plan_provenance` field with a helper:
```python
def _intelligence_status() -> dict:
    latest = intelligence_box["latest"]
    if latest is None:
        return {
            "state": IntelligenceState.NOT_ACTIVE.value,
            "last_evaluated_at": None,
            "last_result": None,
            "reason": "The scenario/ML intelligence layer is built but not wired into the "
                      "live path; the dependable deterministic planner produced this plan.",
        }
    return {
        "state": latest["state"],
        "last_evaluated_at": latest["ts"],
        "last_result": latest.get("result"),
        "reason": _INTELLIGENCE_REASONS[latest["state"]],
    }
```
`_plan_provenance` sets `"intelligence": _intelligence_status()` (was the bare constant).

### 3.4 API surface
- **`/api/battery-plan`** — `provenance.intelligence` changes from a **string** to the **object** above (both the normal branch `api.py:3014` and the paused branch `api.py:2918`). This is a deliberate contract change; all consumers are ours and updated in this change.
- **`GET /api/intelligence`** — a small new endpoint (mirrors the sibling `/api/explainer` at `api.py:3584`) returning `_intelligence_status()`. This gives `System.tsx` a runtime source so it stops reading a static constant. VIEW-tier (read), no new gating logic.

### 3.5 Frontend
- **`labels.ts`** — expand `INTELLIGENCE_COPY` to all four states (`{label, detail, short}` each); delete the `CURRENT_INTELLIGENCE_MODE` static constant (System now reads runtime). Keep `labels.ts` as the copy source of truth, but it no longer asserts *which* state is current.
- **`BatteryPlan.tsx`** — `PlanProvenance.intelligence` type becomes `{ state: string; last_evaluated_at: string | null; last_result: string | null; reason: string }`; render `INTELLIGENCE_COPY[intelligence.state]?.short ?? intelligence.state`, tooltip from `intelligence.reason` (falling back to the copy `detail`). Keep the existing `data-testid="battery-plan-provenance"`.
- **`System.tsx`** — fetch `/api/intelligence` and render the state + reason in the existing `health-planning-intelligence` row (keep the muted "unknown" dot only for `not_active`; the row must reflect the runtime state, not the deleted constant). Keep `data-testid`s `health-planning-intelligence` / `planning-intelligence-note`.

## 4. Testing

Backend (`ems/tests/test_battery_plan_api.py` + a small `ems/tests/test_intelligence_status.py`):
- **Default (box empty):** `provenance.intelligence` is an object with `state=="not_active"`, `last_evaluated_at is None`, `last_result is None`, and a non-empty `reason`; present in both the normal and paused-safely branches. Update the two existing tests that assert the old string.
- **Runtime-proven (the anti-constant test):** inject `intelligence_box["latest"] = {"state":"shadow_evaluation","ts":"<iso>","result":"pessimistic vs baseline: -0.3 kWh"}` and assert `/api/battery-plan` provenance **and** `/api/intelligence` report `state=="shadow_evaluation"` with that `last_evaluated_at`/`last_result`. This proves the value is derived from the runtime record, not a renamed constant.
- **No-false-claim:** with the box empty, no response field ever equals `advisory`/`active`/`shadow_evaluation`.
- **`/api/intelligence` shape:** returns exactly `{state,last_evaluated_at,last_result,reason}`; VIEW-tier reachable.

Frontend (Playwright, `ui.spec.ts`):
- Update `DEFAULT_PROVENANCE` + the provenance-line test to the object shape; assert the rendered short copy for `not_active` ("not active yet") and — with a mocked `shadow_evaluation` provenance — the shadow short copy.
- Update the Model-health test to drive `System.tsx` from a mocked `/api/intelligence` (`not_active` → muted row + reason), replacing the static-constant assertion.

## 5. Files
- `ems/domain.py` — `IntelligenceState` enum.
- `ems/web/api.py` — `intelligence_box`, `_intelligence_status()`, `_INTELLIGENCE_REASONS`, `_plan_provenance` change, remove `INTELLIGENCE_MODE`, add `GET /api/intelligence`.
- `ems/web/frontend/src/labels.ts` — 4-state copy, drop `CURRENT_INTELLIGENCE_MODE`.
- `ems/web/frontend/src/BatteryPlan.tsx` — object type + render.
- `ems/web/frontend/src/System.tsx` — fetch `/api/intelligence`.
- Tests: `test_battery_plan_api.py`, `test_intelligence_status.py`, `e2e/ui.spec.ts`.

## 6. Rollout
Additive except the `provenance.intelligence` string→object change, which is internal (our own UI) and updated in-PR. No migration, no config. Ships on `feat/b79-capability-status` off `main`.
