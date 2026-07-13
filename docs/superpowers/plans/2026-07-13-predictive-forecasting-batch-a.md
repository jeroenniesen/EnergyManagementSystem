# Predictive Forecasting Batch A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start the forecast-evidence clock immediately by diagnosing the current zero-match state, migrating/backfilling durable forecast evidence, unifying every quality consumer, and exposing trustworthy model status plus a manual update action in web and iOS.

**Architecture:** A transactional SQLite migration adds a compact observation store, exact-provenance prediction ledger, and durable update-job table. A CPU-only `forecasting` package owns contracts, baseline forecasts, canonical 18:00 capture, evidence scoring, and a baseline-only model service; existing confidence, health, API, advisor, and export paths consume its single `ForecastEvidenceReport`.

**Tech Stack:** Python 3.12, `sqlite3`/`aiosqlite`, FastAPI, pytest, React 18 + TypeScript + Playwright, Swift 6/SwiftUI, uv, Docker.

## Global Constraints

- Gate 0 must capture and classify the live production `n_slots=0` state before Task 2 changes schema.
- Use TDD for every behavior change: failing test, observed failure, minimal implementation, passing focused tests, commit.
- Keep all battery control behavior unchanged; Batch A records and reports evidence only.
- The prediction ledger is the only authority for user-visible forecast quality.
- Canonical evidence is captured at 18:00 site-local for the next local calendar day; fallback capture closes at 20:00.
- Store compact observations for 400 days; keep raw-history retention unchanged.
- Use `Low`, `Medium`, and `High` with plain-language reasons in household UI; technical metrics stay in diagnostics/export.
- Default Docker and `uv sync --frozen --no-dev` remain free of scikit-learn.
- Do not touch the unrelated deleted `.playwright-mcp/*` files in the working tree.
- Update `SPEC.md` deliberately in the same batch; it remains the source of truth.

---

## File Structure

### New backend files

- `ems/forecasting/__init__.py` — public Batch A forecast contracts.
- `ems/forecasting/contracts.py` — immutable forecast, quality, status, and job domain types.
- `ems/forecasting/diagnostic.py` — read-only live history diagnostic CLI and classifier.
- `ems/forecasting/baseline.py` — leakage-safe weekday/weekend baseline with low/expected/high bands.
- `ems/forecasting/canonical.py` — 18:00–20:00 canonical-capture eligibility and next-local-day slot selection.
- `ems/forecasting/quality.py` — the sole pure evidence scorer and versioned quality rules.
- `ems/forecasting/service.py` — baseline-only service, job single-flight, status, scheduling, and audit.
- `ems/storage/migrations.py` — ordered `PRAGMA user_version` runner, pre-migration backup, and v1 schema/backfill.
- `ems/storage/forecast_evidence.py` — focused persistence API for observations, predictions, and model jobs.
- `ems/web/routes/models.py` — model status and manual-update routes.

### New tests and fixtures

- `ems/tests/test_forecast_diagnostic.py`
- `ems/tests/test_migrations.py`
- `ems/tests/test_forecast_evidence_store.py`
- `ems/tests/test_forecast_contracts.py`
- `ems/tests/test_baseline_forecaster.py`
- `ems/tests/test_canonical_forecast.py`
- `ems/tests/test_forecast_quality.py`
- `ems/tests/test_forecast_evidence_reconciliation.py`
- `ems/tests/test_model_service.py`
- `ems/tests/test_models_api.py`
- `ems/tests/fixtures/forecast_history_nslots0.json` — privacy-safe live diagnostic frozen by Gate 0.
- `ems/web/frontend/src/modelHealth.ts`
- `ios/EMSControl/Sources/EMSControlCore/ModelHealthModels.swift`
- `ios/EMSControl/Sources/EMSControlCore/ModelHealthStore.swift`
- `ios/EMSControl/Sources/EMSControlApp/ModelHealthView.swift`
- `ios/EMSControl/Tests/EMSControlCoreTests/ModelHealthStoreTests.swift`

### Existing files modified

- `ems/storage/history.py`, `ems/sense.py`, `ems/main.py`
- `ems/analysis.py`, `ems/confidence.py`, `ems/web/context.py`, `ems/web/api.py`
- `ems/web/routes/accuracy.py`, `ems/web/routes/export.py`, `ems/export_package.py`
- `ems/tests/test_history.py`, `ems/tests/test_analysis.py`, `ems/tests/test_confidence.py`
- `ems/tests/test_accuracy_api.py`, `ems/tests/test_export_package.py`, `ems/tests/test_auth.py`
- `ems/web/frontend/src/System.tsx`, `ems/web/frontend/src/labels.ts`, `ems/web/frontend/src/styles.css`
- `ems/web/frontend/e2e/ui.spec.ts`
- `ios/EMSControl/Sources/EMSControlCore/APIClient.swift`
- `ios/EMSControl/Sources/EMSControlApp/AppShellView.swift`
- `ios/EMSControl/Tests/EMSControlCoreTests/APIClientTests.swift`
- `ios/EMSControl/EMSControl.xcodeproj/project.pbxproj`
- `pyproject.toml`, `uv.lock`, `Dockerfile`, `Makefile`, `scripts/install.sh`, `.github/workflows/ci.yml`
- `SPEC.md`, `CLAUDE.md`, `README.md`, `docs/ml-layer.md`, `docs/config-reference.md`, `docs/api-reference.md`, `docs/operator-runbook.md`

---

### Task 1: Gate 0 — Diagnose and Freeze the Live `n_slots=0` Cause

**Files:**
- Create: `ems/forecasting/__init__.py`
- Create: `ems/forecasting/diagnostic.py`
- Create: `ems/tests/test_forecast_diagnostic.py`
- Create after the live run: `ems/tests/fixtures/forecast_history_nslots0.json`

**Interfaces:**
- Produces: `diagnose_forecast_history(db_path: str) -> ForecastHistoryDiagnostic`
- Produces: `ForecastHistoryDiagnostic.to_public_dict() -> dict`
- CLI: `python -m ems.forecasting.diagnostic --db <path> --output <json-path>`

- [ ] **Step 1: Write failing classification tests**

Create tests that build tiny SQLite databases and assert these exact classifications:

```python
def test_diagnostic_classifies_no_elapsed_overlap(tmp_path):
    db = seeded_db(
        tmp_path,
        raw=["2026-07-13T10:00:00+00:00"],
        forecasts=[("2026-07-14", "2026-07-14T10:00:00+00:00")],
    )
    out = diagnose_forecast_history(str(db))
    assert out.classification == "no_elapsed_overlap"
    assert out.matched_buckets == 0
    assert out.parse_failures == 0


def test_diagnostic_classifies_bucket_match(tmp_path):
    db = seeded_db(
        tmp_path,
        raw=["2026-07-13T10:04:00+00:00"],
        forecasts=[("2026-07-13", "2026-07-13T10:00:00+00:00")],
    )
    out = diagnose_forecast_history(str(db))
    assert out.classification == "matched"
    assert out.matched_buckets == 1
```

The classifier enum is exactly: `missing_table`, `no_raw`, `no_forecasts`, `no_elapsed_overlap`, `parse_failure`, `bucket_mismatch`, `query_window_exclusion`, `matched`.

- [ ] **Step 2: Run the tests and observe the missing module failure**

Run: `uv run pytest ems/tests/test_forecast_diagnostic.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'ems.forecasting'`.

- [ ] **Step 3: Implement the read-only diagnostic**

`ForecastHistoryDiagnostic` is a frozen dataclass containing table presence, counts/date ranges,
parse failures, distinct raw/forecast buckets, matched buckets, matched daytime buckets, duplicate
forecast targets, grouped issue-date summaries, and `classification`. Open SQLite with
`file:<absolute-path>?mode=ro`; never execute DDL/DML. `to_public_dict()` excludes power readings and
contains only counts, dates, offsets, and classification.

The CLI must write atomically through `<output>.tmp` then `Path.replace()` and print the same JSON.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest ems/tests/test_forecast_diagnostic.py -q`

Expected: all diagnostic tests PASS.

- [ ] **Step 5: Run against the live database or support export and freeze the evidence**

Run:

```bash
uv run python -m ems.forecasting.diagnostic \
  --db "$LIVE_EMS_DB_PATH" \
  --output ems/tests/fixtures/forecast_history_nslots0.json
```

Expected: exit `0`; JSON `classification` is one of the declared values and contains no meter power,
tokens, IPs, or location. If the live database is unavailable, STOP Batch A here and request either
the database path or an export package. If the result is a classifier not covered by the tests, add
one failing regression test for that demonstrated condition before continuing.

- [ ] **Step 6: Add the frozen live regression test**

```python
def test_live_nslots0_fixture_remains_classified():
    fixture = json.loads(
        Path("ems/tests/fixtures/forecast_history_nslots0.json").read_text()
    )
    assert fixture["classification"] in {
        "missing_table", "no_raw", "no_forecasts", "no_elapsed_overlap",
        "parse_failure", "bucket_mismatch", "query_window_exclusion", "matched",
    }
    assert fixture["matched_buckets"] == 0
```

If the captured live state is `matched`, replace the final assertion with the demonstrated positive
count and add a regression test around the API query window that still produced `n_slots=0`.

- [ ] **Step 7: Commit the diagnostic gate**

```bash
git add ems/forecasting ems/tests/test_forecast_diagnostic.py \
  ems/tests/fixtures/forecast_history_nslots0.json
git commit -m "diagnose: freeze forecast history mismatch"
```

---

### Task 2: Transactional Migration, 400-Day Observations, and Ledger Backfill

**Files:**
- Create: `ems/storage/migrations.py`
- Create: `ems/storage/forecast_evidence.py`
- Create: `ems/tests/test_migrations.py`
- Create: `ems/tests/test_forecast_evidence_store.py`
- Modify: `ems/storage/history.py`
- Modify: `ems/tests/test_history.py`

**Interfaces:**
- Produces: `run_migrations(db_path: str, sample_cadence_seconds: float, now: datetime) -> MigrationResult`
- Produces: `ForecastEvidenceStore` methods `observations_between`, `upsert_observations`, `insert_predictions`, `canonical_predictions_between`, `create_job`, `update_job`, `active_job`, `get_job`, `purge_before`
- Produces: `ForecastEvidenceStore.add_quality_flag(slot_start, flag)` for demonstrated clamping,
  manual-override, and calibration/setup exclusions
- Consumes: Gate 0 classification and frozen fixture from Task 1

- [ ] **Step 1: Write failing migration tests**

Cover a fresh DB, a previous-schema DB with `user_version=0`, interrupted/idempotent rerun, verified
pre-migration backup, raw/derived 15-minute aggregation, and legacy forecast classification:

```python
def test_v1_migration_backfills_observations_and_eligible_legacy(tmp_path):
    db = previous_schema_db(tmp_path, recorder_active_at_midnight=True)
    result = run_migrations(str(db), 300.0, datetime(2026, 7, 13, tzinfo=UTC))
    con = sqlite3.connect(db)
    assert con.execute("PRAGMA user_version").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM forecast_observations").fetchone()[0] == 1
    row = con.execute(
        "SELECT provenance, canonical FROM forecast_predictions"
    ).fetchone()
    assert row == ("legacy_date_keyed", 0)
    assert result.backup_path is not None
    assert sqlite3.connect(result.backup_path).execute("PRAGMA integrity_check").fetchone()[0] == "ok"
```

- [ ] **Step 2: Run migration tests and observe failure**

Run: `uv run pytest ems/tests/test_migrations.py ems/tests/test_forecast_evidence_store.py -q`

Expected: FAIL with missing `ems.storage.migrations` and `ems.storage.forecast_evidence`.

- [ ] **Step 3: Add the v1 schema and migration runner**

Create these tables and indexes in one `BEGIN IMMEDIATE` transaction:

```sql
CREATE TABLE forecast_observations (
  slot_start TEXT PRIMARY KEY,
  non_ev_load_w REAL,
  solar_actual_w REAL,
  sample_count INTEGER NOT NULL,
  coverage REAL NOT NULL,
  quality_flags TEXT NOT NULL
);
CREATE TABLE forecast_predictions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  issued_at TEXT NOT NULL,
  target_start TEXT NOT NULL,
  forecast_type TEXT NOT NULL CHECK(forecast_type IN ('load','solar')),
  low_w REAL NOT NULL,
  expected_w REAL NOT NULL,
  high_w REAL NOT NULL,
  baseline_low_w REAL NOT NULL,
  baseline_expected_w REAL NOT NULL,
  baseline_high_w REAL NOT NULL,
  source TEXT NOT NULL,
  model_version TEXT NOT NULL,
  feature_schema_version TEXT NOT NULL,
  quality_level TEXT NOT NULL,
  quality_reasons TEXT NOT NULL,
  provenance TEXT NOT NULL,
  canonical INTEGER NOT NULL CHECK(canonical IN (0,1)),
  target_local_day TEXT NOT NULL,
  UNIQUE(issued_at,target_start,forecast_type,source,model_version)
);
CREATE INDEX idx_predictions_target ON forecast_predictions(target_start,forecast_type);
CREATE INDEX idx_predictions_day ON forecast_predictions(target_local_day,forecast_type,canonical);
CREATE TABLE model_update_jobs (
  id TEXT PRIMARY KEY,
  requested_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  stage TEXT NOT NULL,
  outcome TEXT,
  detail TEXT NOT NULL
);
```

Before changing a non-empty v0 database, create
`backups/pre-migration-v0-to-v1-<UTC timestamp>.sqlite` with `sqlite3.Connection.backup`, run
`PRAGMA integrity_check`, and abort before `BEGIN IMMEDIATE` if backup verification fails.

- [ ] **Step 4: Add idempotent backfill rules**

Aggregate paired `raw_samples`/`derived_samples` by UTC epoch quarter-hour. Coverage is
`min(1.0, sample_count / max(1, round(900 / sample_cadence_seconds)))`; flags are JSON `[]` for
ordinary legacy samples. Copy old forecast rows as `legacy_date_keyed` only when that issue date has
a raw sample in `[00:00Z,00:15Z)` and target lead is at least 18 hours; all others become
`legacy_unknown`. Set approximate `issued_at` to `<issued_date>T00:00:00+00:00`, `canonical=0`, and
copy P10/P50/P90 into both active and baseline columns. Use `INSERT OR IGNORE` so reruns are stable.
`purge_before(cutoff)` deletes only compact observations older than the supplied UTC instant; it
never shortens raw-history retention or removes prediction-ledger rows.

- [ ] **Step 5: Wire migration into `HistoryStore.init()`**

Change `HistoryStore.__init__` to accept `sample_cadence_seconds: float = 300.0`. After the existing
base-table transaction commits, call:

```python
await asyncio.to_thread(
    run_migrations,
    self.db_path,
    self.sample_cadence_seconds,
    datetime.now(UTC),
)
```

In `ems/main.py`, construct `HistoryStore(str(db_path), sample_cadence_seconds=cfg.cycle_seconds)`.

- [ ] **Step 6: Run migration/storage tests**

Run: `uv run pytest ems/tests/test_migrations.py ems/tests/test_forecast_evidence_store.py ems/tests/test_history.py -q`

Expected: all PASS; rerun the same command to prove idempotence.

- [ ] **Step 7: Commit migration and storage**

```bash
git add ems/storage/migrations.py ems/storage/forecast_evidence.py \
  ems/storage/history.py ems/main.py ems/tests/test_migrations.py \
  ems/tests/test_forecast_evidence_store.py ems/tests/test_history.py
git commit -m "feat(storage): migrate and backfill forecast evidence"
```

---

### Task 3: Forecast Contracts, Leakage-Safe Baseline, and Canonical Capture

**Files:**
- Create: `ems/forecasting/contracts.py`
- Create: `ems/forecasting/baseline.py`
- Create: `ems/forecasting/canonical.py`
- Create: `ems/tests/test_forecast_contracts.py`
- Create: `ems/tests/test_baseline_forecaster.py`
- Create: `ems/tests/test_canonical_forecast.py`
- Modify: `ems/forecasting/__init__.py`

**Interfaces:**
- Produces: `ForecastPoint`, `ForecastBundle`, `ForecastQualityComponent`, `ForecastEvidenceReport`, `ModelJob`, `ModelStatus`
- Produces: `BaselineLoadForecaster.forecast(origin: datetime, starts: tuple[datetime, ...], observations: list[dict]) -> ForecastBundle`
- Produces: `canonical_target_day(now: datetime, tz: ZoneInfo) -> date | None`
- Produces: `next_local_day_starts(now: datetime, tz: ZoneInfo) -> tuple[datetime, ...]`

- [ ] **Step 1: Write contract and DST tests**

```python
def test_bundle_rejects_crossed_quantiles():
    with pytest.raises(ValueError, match="low <= expected <= high"):
        ForecastPoint(T0, low_w=900, expected_w=500, high_w=700)


@pytest.mark.parametrize((day, count), [(date(2026, 3, 29), 92), (date(2026, 10, 25), 100)])
def test_next_local_day_uses_real_dst_slot_count(day, count):
    now = datetime.combine(day - timedelta(days=1), time(18), AMS)
    assert len(next_local_day_starts(now, AMS)) == count
```

- [ ] **Step 2: Run tests and observe missing types**

Run: `uv run pytest ems/tests/test_forecast_contracts.py ems/tests/test_baseline_forecaster.py ems/tests/test_canonical_forecast.py -q`

Expected: FAIL on missing modules/types.

- [ ] **Step 3: Define immutable contracts**

Use frozen dataclasses and `StrEnum` for `QualityLevel(low, medium, high)`. `ForecastPoint.__post_init__`
rejects naive/misaligned starts, non-finite or negative watts, and crossed quantiles. Every report
contains `evidence_version`, `quality_rules_version`, `eligible_days`, component metrics/reasons, and
an aggregate worst-component-wins level/reason.

- [ ] **Step 4: Build the baseline without future leakage**

Bucket only observations with `slot_start < origin` by `(is_weekend, local_hour, local_minute)`.
Require three observations for a personalized bucket; otherwise reuse the existing shaped typical
profile. Apply a capped recent correction from the last three complete local days:

```python
ratio = observed_recent_kwh / max(profile_recent_kwh, 0.1)
correction = min(1.25, max(0.75, ratio))
expected = bucket_mean * correction
low = max(0.0, expected * 0.65)
high = expected * 1.45
```

Add a test proving an extreme observation at or after `origin` does not change the bundle.

- [ ] **Step 5: Implement exact canonical eligibility**

`canonical_target_day()` returns tomorrow only from 18:00:00 through 19:59:59 site-local. It returns
`None` outside that window. `next_local_day_starts()` walks UTC quarter-hours from local midnight to
the following local midnight, producing 92/96/100 slots across DST.

- [ ] **Step 6: Run focused tests**

Run: `uv run pytest ems/tests/test_forecast_contracts.py ems/tests/test_baseline_forecaster.py ems/tests/test_canonical_forecast.py -q`

Expected: all PASS.

- [ ] **Step 7: Commit contracts and baseline**

```bash
git add ems/forecasting ems/tests/test_forecast_contracts.py \
  ems/tests/test_baseline_forecaster.py ems/tests/test_canonical_forecast.py
git commit -m "feat(forecast): add baseline contract and canonical window"
```

---

### Task 4: Single Forecast Evidence Scorer and Existing-Surface Reconciliation

**Files:**
- Create: `ems/forecasting/quality.py`
- Create: `ems/tests/test_forecast_quality.py`
- Create: `ems/tests/test_forecast_evidence_reconciliation.py`
- Modify: `ems/analysis.py`, `ems/confidence.py`, `ems/export_package.py`
- Modify: `ems/web/context.py`, `ems/web/api.py`
- Modify: `ems/web/routes/accuracy.py`, `ems/web/routes/export.py`
- Modify: `ems/tests/test_analysis.py`, `ems/tests/test_confidence.py`
- Modify: `ems/tests/test_accuracy_api.py`, `ems/tests/test_export_package.py`

**Interfaces:**
- Consumes: `ForecastEvidenceStore` from Task 2 and contracts from Task 3
- Produces: `score_forecast_evidence(predictions, observations, now) -> ForecastEvidenceReport`
- Produces: `ForecastEvidenceReport.to_accuracy_api(plan_execution: dict | None) -> dict`

- [ ] **Step 1: Write failing quality-rule tests**

Use generated complete-day inputs to assert: six days is Low; seven representative days can be
Medium; an 80% interval wider than 200% normalized stays Low; legacy evidence caps aggregate at
Medium; stale evidence older than 30 days is Low; and worst-component-wins.

```python
def test_worst_component_sets_aggregate_reason():
    report = score_report(solar=component("high"), load=component("low", "Load still learning"))
    assert report.level is QualityLevel.LOW
    assert report.reason == "Load still learning"
```

- [ ] **Step 2: Run tests and observe missing scorer**

Run: `uv run pytest ems/tests/test_forecast_quality.py ems/tests/test_forecast_evidence_reconciliation.py -q`

Expected: FAIL with missing `ems.forecasting.quality`.

- [ ] **Step 3: Implement the versioned scorer**

Set `EVIDENCE_VERSION = 1`, `QUALITY_RULES_VERSION = 1`. Match predictions to observations by
`target_start`; group by local target day; compute MAE, signed daily-energy bias, pinball loss,
80%-band coverage, normalized mean interval width, and weighted interval score over the most recent
56 eligible days. Exclude slots flagged `clamped`, `incomplete`, `manual_override`, or `calibration`.
Normalize MAE and interval width by `max(mean_observed_load_w, 100.0)`.

Apply these version-1 gates exactly:

- Medium: 7 complete days with at least 5 weekdays and 2 weekend days; at least 80% usable slot
  coverage; absolute daily-energy bias at most 20%; normalized MAE at most 45%; nominal 80% interval
  coverage from 60% through 95%; normalized mean interval width at most 200%; weighted interval
  score no more than 5% worse than `BaselineLoadForecaster`.
- High: 28 complete days with at least 20 weekdays and 8 weekend days; at least 90% usable slot
  coverage; absolute daily-energy bias at most 10%; normalized MAE at most 30%; nominal 80% interval
  coverage from 72% through 88%; normalized mean interval width at most 125%; the bias, normalized
  MAE, coverage, and width gates must also hold independently in each of the last four complete
  weekly windows.
- Search backward only within the 56-day cap to satisfy calendar mix. Evidence older than 7 days
  caps quality at Medium; older than 30 days is Low. `legacy_date_keyed` evidence can contribute to
  Medium but never High. Any failed Medium gate is Low; the aggregate is worst-component-wins.

- [ ] **Step 4: Replace parallel evidence consumers**

Change `AppContext` to expose:

```python
forecast_evidence: Callable[[datetime], Awaitable[ForecastEvidenceReport | None]]
```

Replace `_solar_forecast_skill` with `_forecast_evidence`. Make `plan_confidence` accept
`forecast_evidence` and use its aggregate level/reason after data freshness/device checks.
`model_health` maps the report's solar/load component levels to `ok/warn/unknown`; plan execution
remains its own component. `/api/accuracy` returns the report-derived `solar`, `load`, `quality`, and
existing `plan_execution`/`health`. The advisor reads canonical solar residuals from the same report
window. Export writes the same evidence/version and labels legacy matched-slot output explicitly.

- [ ] **Step 5: Add a cross-surface equality test**

Seed one evidence database, then assert `/api/accuracy`, `/api/battery-plan` confidence, advisor, and
export validation summary all contain the same `evidence_version`, `quality_rules_version`, level,
eligible-day count, and deciding reason. Do not mock four separate dictionaries; all paths must call
the same store-backed scorer.

- [ ] **Step 6: Run focused reconciliation tests**

Run:

```bash
uv run pytest ems/tests/test_forecast_quality.py \
  ems/tests/test_forecast_evidence_reconciliation.py \
  ems/tests/test_analysis.py ems/tests/test_confidence.py \
  ems/tests/test_accuracy_api.py ems/tests/test_export_package.py -q
```

Expected: all PASS and no test imports `_MIN_SKILL_SLOTS` as the household quality authority.

- [ ] **Step 7: Commit the single evidence authority**

```bash
git add ems/forecasting/quality.py ems/analysis.py ems/confidence.py \
  ems/export_package.py ems/web/context.py ems/web/api.py ems/web/routes/accuracy.py \
  ems/web/routes/export.py ems/tests/test_forecast_quality.py \
  ems/tests/test_forecast_evidence_reconciliation.py ems/tests/test_analysis.py \
  ems/tests/test_confidence.py ems/tests/test_accuracy_api.py ems/tests/test_export_package.py
git commit -m "refactor(forecast): unify quality evidence across surfaces"
```

---

### Task 5: Baseline Model Service, Canonical Persistence, and API Job Lifecycle

**Files:**
- Create: `ems/forecasting/service.py`
- Create: `ems/web/routes/models.py`
- Create: `ems/tests/test_model_service.py`
- Create: `ems/tests/test_models_api.py`
- Modify: `ems/sense.py`, `ems/main.py`, `ems/web/context.py`, `ems/web/api.py`
- Modify: `ems/tests/test_auth.py`
- Modify: `docs/api-reference.md`

**Interfaces:**
- Produces: `ModelService.init()`, `record_cycle(now, solar_slots, quality_flags)`, `status(now)`,
  `trigger_update(now)`, `job(job_id)`, `wait_for_job(job_id)`, `flag_slot(now, flag)`,
  `run_schedule(stop)`
- API: `GET /api/models/status`, `POST /api/models/train`, `GET /api/models/jobs/{id}`

- [ ] **Step 1: Write failing service tests**

Cover canonical dedupe, 18:00 failure/19:00 retry, no capture after 20:00, one active manual job,
durable job outcome, audit append, no-new-evidence outcome, and scheduled 03:00 eligibility.

```python
async def test_manual_update_is_single_flight(service):
    first = await service.trigger_update(T0)
    second = await service.trigger_update(T0 + timedelta(seconds=1))
    assert second.id == first.id
    await service.wait_for_job(first.id)
    assert (await service.job(first.id)).outcome in {
        "current_model_remains_better", "more_history_needed", "no_new_evidence"
    }
```

- [ ] **Step 2: Run tests and observe missing service/routes**

Run: `uv run pytest ems/tests/test_model_service.py ems/tests/test_models_api.py -q`

Expected: FAIL with missing `ModelService` and models router.

- [ ] **Step 3: Implement the baseline-only service**

`record_cycle()` materializes the completed prior observation slot and, during 18:00–20:00, stores
canonical baseline load plus solar-source predictions for the next local day. Unique constraints
make every retry idempotent. `trigger_update()` creates one UUID job, advances
`preparing -> evaluating -> finished`, refreshes materialized observations/evidence, and returns
`more_history_needed`, `no_new_evidence`, or `current_model_remains_better`; it never trains a tree
or changes a plan in Batch A. Both scheduled maintenance and a manual update call
`purge_before(now - timedelta(days=400))`; prediction evidence is retained. `wait_for_job()` awaits
only the service-owned task for that UUID and returns the durable final job row.

- [ ] **Step 4: Wire lifecycle and recorder**

Construct `ForecastEvidenceStore` and `ModelService` in `main.build_app()`. Pass
`forecast_observer=model_service.record_cycle` into `Recorder`; call it after raw/derived and source
forecast persistence. `Recorder` supplies `clamped`, `incomplete`, and source-freshness flags for
the completed slot. The existing manual-override and calibration/setup write paths call
`model_service.flag_slot(now, "manual_override")` or `flag_slot(now, "calibration")`; these flags
exclude the affected slot from model evidence without deleting it. In FastAPI lifespan, call
`await model_service.init()`, start
`model_service.run_schedule(stop)`, and await it during shutdown. Add service to `AppContext`.

- [ ] **Step 5: Add authenticated API routes**

The status response is:

```json
{
  "active_source": "baseline",
  "capability": "baseline_only",
  "quality": {"level":"low","reason":"Still collecting complete forecast days.","eligible_days":0},
  "last_successful_update": null,
  "fallback_state": null,
  "current_job": null,
  "reserve_advice": null
}
```

`POST /api/models/train` returns `202` with the job. Add `/api/models/train` to the centrally gated
write paths; GETs follow existing read-auth behavior. Unknown jobs return JSON 404.

- [ ] **Step 6: Run service/API/auth tests**

Run: `uv run pytest ems/tests/test_model_service.py ems/tests/test_models_api.py ems/tests/test_auth.py -q`

Expected: all PASS, including 401 for a token-protected manual update without a bearer token.

- [ ] **Step 7: Commit model service and API**

```bash
git add ems/forecasting/service.py ems/web/routes/models.py ems/sense.py ems/main.py \
  ems/web/context.py ems/web/api.py ems/tests/test_model_service.py \
  ems/tests/test_models_api.py ems/tests/test_auth.py docs/api-reference.md
git commit -m "feat(models): expose baseline evidence update lifecycle"
```

---

### Task 6: Extend the Web Model Health Surface

**Files:**
- Create: `ems/web/frontend/src/modelHealth.ts`
- Modify: `ems/web/frontend/src/System.tsx`
- Modify: `ems/web/frontend/src/labels.ts`
- Modify: `ems/web/frontend/src/styles.css`
- Modify: `ems/web/frontend/e2e/ui.spec.ts`

**Interfaces:**
- Consumes: Task 5 model API
- Produces: `fetchModelStatus()`, `startModelUpdate()`, `fetchModelJob(id)`

- [ ] **Step 1: Add failing Playwright assertions**

Mock `/api/models/status` with Low quality, then assert the System page shows `Forecast quality`,
`Low`, the deciding reason, `Baseline`, and `Update models now`. Click the button, assert one POST,
show `Evaluating`, poll the job, and render `More history needed`. Add a 401 case that shows the
existing access-token guidance rather than a generic failure.

- [ ] **Step 2: Run the focused e2e test and observe failure**

Run: `cd ems/web/frontend && npx playwright test e2e/ui.spec.ts -g "model update"`

Expected: FAIL because the button/status fields do not exist.

- [ ] **Step 3: Add typed fetch helpers and UI state**

`modelHealth.ts` exports exact response unions for quality, job stages, outcomes, and the three fetch
functions. `SystemView` performs a best-effort 30-second status poll, disables the button while a job
is active, polls an active job every second, stops after `finished`, and refreshes status once.

Household copy maps outcomes exactly:

```ts
export const MODEL_OUTCOME: Record<string, string> = {
  current_model_remains_better: "Current forecast remains better",
  more_history_needed: "More history needed",
  no_new_evidence: "No new evidence to learn from",
  could_not_update: "Could not update",
};
```

- [ ] **Step 4: Add accessible styling**

Use existing panel, badge, button, focus-visible, and reduced-motion conventions. The quality label
must be text, not color-only. Add `aria-live="polite"` around job progress and outcome.

- [ ] **Step 5: Run frontend verification**

Run:

```bash
cd ems/web/frontend
npm run build
npx playwright test e2e/ui.spec.ts -g "model health|model update"
cd ../../..
python3 scripts/check_bundle_size.py
```

Expected: build PASS, focused Playwright tests PASS, bundle remains at or below 300 KB gzipped.

- [ ] **Step 6: Commit the web surface**

```bash
git add ems/web/frontend/src/modelHealth.ts ems/web/frontend/src/System.tsx \
  ems/web/frontend/src/labels.ts ems/web/frontend/src/styles.css \
  ems/web/frontend/e2e/ui.spec.ts
git commit -m "feat(web): add forecast quality and model update status"
```

---

### Task 7: Add the iOS Model Health View and Manual Update Action

**Files:**
- Create: `ios/EMSControl/Sources/EMSControlCore/ModelHealthModels.swift`
- Create: `ios/EMSControl/Sources/EMSControlCore/ModelHealthStore.swift`
- Create: `ios/EMSControl/Sources/EMSControlApp/ModelHealthView.swift`
- Create: `ios/EMSControl/Tests/EMSControlCoreTests/ModelHealthStoreTests.swift`
- Modify: `ios/EMSControl/Sources/EMSControlCore/APIClient.swift`
- Modify: `ios/EMSControl/Sources/EMSControlApp/AppShellView.swift`
- Modify: `ios/EMSControl/Tests/EMSControlCoreTests/APIClientTests.swift`
- Modify: `ios/EMSControl/EMSControl.xcodeproj/project.pbxproj`

**Interfaces:**
- Produces: `APIClient.fetchModelStatus()`, `startModelUpdate()`, `fetchModelJob(id:)`
- Produces: `@Observable @MainActor ModelHealthStore`

- [ ] **Step 1: Write failing Swift client/store tests**

Assert exact paths, bearer header on POST, decoding of Low/Medium/High, single active refresh, stale
data preservation on error, server-switch clearing, and manual-update progress/outcome.

```swift
func testStartModelUpdateUsesAuthenticatedPost() async throws {
    let transport = ModelHealthTransport()
    let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, token: "abc", transport: transport)
    let job = try await client.startModelUpdate()
    XCTAssertEqual(job.stage, "preparing")
    XCTAssertEqual(transport.lastRequest?.url?.path, "/api/models/train")
    XCTAssertEqual(transport.lastRequest?.httpMethod, "POST")
    XCTAssertEqual(transport.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer abc")
}
```

- [ ] **Step 2: Run Swift tests and observe failure**

Run: `cd ios/EMSControl && swift test --filter 'APIClientTests|ModelHealthStoreTests'`

Expected: compile FAIL because model-health types/methods do not exist.

- [ ] **Step 3: Add models, client methods, and store**

Define `ModelStatus`, `ForecastQualitySummary`, and `ModelUpdateJob` as public
`Codable, Equatable, Sendable` structs with snake-case decoding through existing decoder settings.
The store follows `InsightsStore` conventions: client identity, `isLoading`, `isStale`,
`lastUpdatedAt`, `errorMessage`, preserved last-good data, and immediate clearing on server switch.

- [ ] **Step 4: Build `ModelHealthView` and navigation**

Add a fifth `System` tab with `wrench.and.screwdriver`. The view uses `NavigationStack`, shows quality
word/reason/source/update time, a disabled progress button, `ContentUnavailableView` for no evidence,
and an error banner that preserves last-good state. It does not expose battery controls.

- [ ] **Step 5: Register files in Xcode and run verification**

Update PBX file references, groups, and Sources build phases for app/core files. Run:

```bash
cd ios/EMSControl
swift test
cd ../..
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
  xcodebuild -project ios/EMSControl/EMSControl.xcodeproj \
  -scheme EMSControl -sdk iphonesimulator -configuration Debug build CODE_SIGNING_ALLOWED=NO
```

Expected: Swift tests PASS and app target reports `BUILD SUCCEEDED`.

- [ ] **Step 6: Commit iOS surface**

```bash
git add ios/EMSControl/Sources/EMSControlCore/ModelHealthModels.swift \
  ios/EMSControl/Sources/EMSControlCore/ModelHealthStore.swift \
  ios/EMSControl/Sources/EMSControlCore/APIClient.swift \
  ios/EMSControl/Sources/EMSControlApp/ModelHealthView.swift \
  ios/EMSControl/Sources/EMSControlApp/AppShellView.swift \
  ios/EMSControl/Tests/EMSControlCoreTests/ModelHealthStoreTests.swift \
  ios/EMSControl/Tests/EMSControlCoreTests/APIClientTests.swift \
  ios/EMSControl/EMSControl.xcodeproj/project.pbxproj
git commit -m "feat(ios): add model health and update action"
```

---

### Task 8: Optional Dependency Target, Source-of-Truth Reconciliation, and Batch Acceptance

**Files:**
- Modify: `pyproject.toml`, `uv.lock`, `Dockerfile`, `Makefile`, `scripts/install.sh`
- Modify: `.github/workflows/ci.yml`
- Modify: `SPEC.md`, `CLAUDE.md`, `README.md`
- Modify: `docs/ml-layer.md`, `docs/config-reference.md`, `docs/operator-runbook.md`

**Interfaces:**
- Produces: optional dependency group `forecasting`
- Produces: `./scripts/install.sh --forecasting`, `make build-forecasting-image`

- [ ] **Step 1: Add the optional dependency and lock it**

Add:

```toml
[project.optional-dependencies]
dev = ["pytest>=8.0", "ruff>=0.4"]
forecasting = ["scikit-learn>=1.5,<2"]
```

Run: `uv lock`

Expected: `uv.lock` changes and resolves Python 3.12 wheels for macOS plus Linux arm64/amd64.

- [ ] **Step 2: Add explicit lean/forecasting build paths**

Add Docker `ARG INSTALL_FORECASTING=0` and a conditional dependency layer that runs the exact lean
command for `0` and `uv sync --frozen --no-dev --extra forecasting` for `1`. Add installer
`--forecasting`, Make target `build-forecasting-image`, and CI checks:

```yaml
- name: Verify lean dependency set
  run: uv sync --frozen --no-dev && ! uv run python -c 'import sklearn'
- name: Verify forecasting dependency set
  run: uv sync --frozen --no-dev --extra forecasting && uv run python -c 'import sklearn'
```

Build both `docker build .` and `docker build --build-arg INSTALL_FORECASTING=1 .` in CI or a
dedicated cached matrix; the default image must remain the lean variant.

- [ ] **Step 3: Reconcile source-of-truth documentation**

Update documents with these exact boundaries:

- CPU baseline/quantile forecasting is optional but not accelerator-gated.
- Batch A ships baseline evidence only; the tree challenger is Batch B.
- `forecast.use_developing_model` belongs to Batch B.
- `planner.risk_preference` and the B-47 risk-aware planner belong to Batch C.
- `planner.mode=rule_based|ml|advisory` remains reserved for a future learned planner.
- Template/external/local explainer behavior is unchanged.
- New tables, canonical 18:00 semantics, 400-day compact retention, migration/backup behavior, APIs,
  web/iOS status surfaces, and manual-update audit behavior are documented.

Remove any claim that `LoadForecaster` always requires CUDA/Metal/CoreML/MLX. Do not describe Batch B
or C behavior as shipped.

- [ ] **Step 4: Run full Batch A verification**

Run:

```bash
git diff --check
uv run ruff check ems
uv run pytest ems/tests
cd ems/web/frontend && npm run build && npx playwright test && cd ../../..
python3 scripts/check_bundle_size.py
cd ios/EMSControl && swift test && cd ../..
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
  xcodebuild -project ios/EMSControl/EMSControl.xcodeproj \
  -scheme EMSControl -sdk iphonesimulator -configuration Debug build CODE_SIGNING_ALLOWED=NO
docker build .
docker build --build-arg INSTALL_FORECASTING=1 .
```

Expected: no diff whitespace errors; ruff PASS; all pytest/Playwright/Swift tests PASS; bundle
`<=300 KB` gzipped; Xcode `BUILD SUCCEEDED`; both images build; lean runtime cannot import sklearn;
forecasting runtime can.

- [ ] **Step 5: Run migration acceptance on a disposable production backup**

Copy the live database backup to `/tmp/ems-batch-a-acceptance.sqlite`, start the app against it with
`EMS_DB_PATH=/tmp/ems-batch-a-acceptance.sqlite` and mock sources, then verify:

```bash
sqlite3 /tmp/ems-batch-a-acceptance.sqlite 'PRAGMA integrity_check; PRAGMA user_version;'
curl -fsS http://127.0.0.1:8099/api/models/status
curl -fsS http://127.0.0.1:8099/api/accuracy
```

Expected: `ok`, user version `1`, HTTP 200 JSON from both endpoints, migrated legacy evidence labeled
as legacy, no real device writes, and the original live database unchanged.

- [ ] **Step 6: Commit packaging and documentation**

```bash
git add pyproject.toml uv.lock Dockerfile Makefile scripts/install.sh .github/workflows/ci.yml \
  SPEC.md CLAUDE.md README.md docs/ml-layer.md docs/config-reference.md \
  docs/operator-runbook.md
git commit -m "docs: reconcile CPU forecasting evidence architecture"
```

---

## Batch A Completion Gate

Batch A is complete only when:

- The live `n_slots=0` cause is frozen in a privacy-safe regression fixture.
- Previous-schema migration, backup verification, idempotent backfill, and restore acceptance pass.
- Canonical evidence begins accumulating at 18:00 local without duplicated target rows.
- Plan confidence, Model Health, accuracy, advisor, and export agree on one evidence version/result.
- Manual update is authenticated, single-flight, audit-logged, and explicitly baseline-only.
- Web and iOS show word-based quality, reason, source, progress, and outcome.
- Default install/image remain sklearn-free; forecasting extra resolves and imports successfully.
- Source-of-truth docs describe Batch A as shipped and B/C as future.
- No battery-control behavior or plan selection changes.
