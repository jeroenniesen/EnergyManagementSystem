# Predictive Forecasting and Risk-Aware Planning Design

**Date:** 2026-07-13
**Scope:** BACKLOG B-63, B-64, B-65, and B-67; B-78 is a later follow-up
**Status:** Revised after integration review, awaiting reviewer approval and implementation plan

## 1. Purpose

Improve the EMS's household-load and solar forecasts, plan explicitly under uncertainty, and
recommend a daily reserve that reflects expected conditions. Deliver value early without requiring
a GPU, Jetson, or a large history window, while labeling forecast quality honestly.

This work preserves the core control contract:

- Forecasting and optimization never write to the battery.
- The deterministic planner remains responsible for producing `Plan` objects.
- Every selected plan passes the existing projection checks, validator, data-quality gate, mode
  dwell limit, write cap, and fail-safe behavior.
- The minimum battery reserve is inviolable for every risk preference.
- Missing, stale, slow, corrupt, or low-quality model output falls back to the existing baseline.

## 2. Product decisions

### 2.1 CPU-first, model-agnostic forecasting

The first personalized model is a small CPU-native quantile gradient-boosted model. The current
learned household profile remains the baseline and fallback. Accelerator support is not required
for load forecasting or risk-aware planning.

A future neural forecaster may replace or compete with the tree model through the same public
forecast contract. Planning, APIs, UI, quality labels, safety validation, and reserve advice must
not depend on the model family.

### 2.2 Separate forecast quality from plan confidence

The UI exposes two distinct concepts:

- **Forecast quality** reports how reliably the forecasting system has performed on unseen data.
- **Plan confidence** remains the existing assessment of today's particular plan, including input
  freshness, device health, forecast quality, data quality, and validation evidence.

Both use `Low`, `Medium`, or `High` plus a plain-language deciding reason. The normal household UI
does not require users to interpret probabilities or statistical metrics. Detailed numbers remain
available in diagnostics and exports.

### 2.3 Early personalization is explicitly optional

Add `forecast.use_developing_model`, default `false`.

- When off, a low-quality personalized model is scored and shown but does not influence live
  planning. The proven baseline remains active.
- When on, a low-quality personalized model may influence the forecast through a conservative,
  capped shrinkage toward the baseline, with personalized weight at most 25%. It never fully
  replaces the baseline.
- Medium-quality evidence permits personalized weight at most 60%, regardless of the toggle; a
  high-quality model may become the primary forecast with weight up to 100%.
- The active source and fallback state are always visible.

The toggle opts into earlier personalization. It does not weaken the plan validator, minimum
reserve, input freshness requirements, or other safety rules.

### 2.4 Risk preferences use household language

Add `planner.risk_preference`, default `balanced`, with three values:

- **Cautious:** prioritizes having enough stored energy across nearly all credible futures.
- **Balanced:** accepts ordinary forecast variation while retaining protection against a bad
  evening.
- **Savings-focused:** avoids precautionary charging unless evidence that it is needed is strong.

The application explains consequences in words. Internally, versioned calibrated thresholds make
the preferences reproducible and testable; those numbers may be shown in diagnostics.

The initial protected-window success thresholds are 95% for `cautious`, 85% for `balanced`, and
70% for `savings_focused`, measured over the common trajectory ensemble. These govern the additional
planning buffer; no preference permits crossing the deterministic hard reserve. Threshold changes
require replay evidence and an explicit specification/configuration change, not silent model tuning.

**Protected expensive window** means the contiguous interval from the first through the last
forecast-deficit slot in the planning horizon whose import price exceeds the shared degradation-
and-efficiency-aware breakeven threshold. Success means the projected battery can serve every such
deficit slot without reaching the hard reserve and forcing peak-price import. When no slot qualifies,
the protected-window constraint is vacuously satisfied and economics—not an invented evening
window—decides among candidates.

### 2.5 Reserve changes remain suggest-first

B-67 recommends a daily planning reserve and explains why it differs from the user's default. It
cannot apply the recommendation automatically.

B-78 is a separate follow-up for explicit opt-in automatic adoption after B-67 has accumulated
production evidence. Automatic adoption must retain a user-configured hard floor, bound day-to-day
changes, audit every change, be reversible, and fall back to the last manual reserve when evidence
is insufficient or stale.

## 3. Architecture

### 3.1 Stable forecast contract

Define a model-independent `LoadForecaster` boundary:

```text
forecast(origin, horizon) -> ForecastBundle

ForecastBundle:
  issued_at
  horizon
  slots[]:
    start
    low_w
    expected_w
    high_w
  evidence:
    source
    model_version
    feature_schema_version
    quality_level
    quality_reasons[]
    fallback_state
```

All output slots must be aligned to the EMS's 15-minute UTC grid, finite, non-negative, and ordered
`low_w <= expected_w <= high_w`. Invalid output is rejected rather than repaired silently at the
planner boundary; an internal calibration/blending step may restore ordering before validation.

Initial adapters:

- `BaselineLoadForecaster`: an improved version of the existing historical profile, including
  weekday/weekend structure and a conservative recent-demand correction.
- `QuantileTreeLoadForecaster`: CPU-native low/expected/high prediction.
- `BlendedLoadForecaster`: selects or shrinks the personalized forecast toward the baseline based
  on evidence and `forecast.use_developing_model`.

A later `NeuralLoadForecaster` implements the same output contract but may use a different internal
sequence representation and training pipeline.

### 3.2 Model service

Create an injected model service rather than adding more long-lived closures to `ems/web/api.py`.
It owns:

- Forecast serving and baseline fallback.
- Training-job scheduling, deduplication, cancellation, and status.
- Candidate evaluation and atomic promotion.
- Model registry and last-known-good rollback.
- Forecast-quality calculation.
- Scheduled and manually triggered training.

CPU-heavy training runs outside the FastAPI event loop in a worker process. A training
failure or process crash cannot stop sensing, planning, API service, or battery control.

### 3.3 One forecast-evidence authority

The prediction ledger and its scorer become the single authority for user-visible forecast quality.
The existing `ems.analysis.forecast_error()` matched-slot calculation must not remain as a parallel
source capable of producing a different answer.

The scorer returns one versioned `ForecastEvidenceReport` containing separate `solar` and `load`
component metrics plus an aggregate `Low | Medium | High` result. The aggregate uses
worst-component-wins across components required by the current plan; its deciding reason names that
component. Component panels may show their own status, but every consumer receives them from this
same report and evidence window.

Reconcile all existing consumers in the first delivery batch:

- `plan_confidence()` consumes the canonical `ForecastQuality` result rather than its current
  `_MIN_SKILL_SLOTS`/matched-slot gate.
- B-76 `model_health()` consumes the same result and does not import or re-derive separate solar
  evidence thresholds.
- `GET /api/accuracy`, the System page, the support export, and the solar-confidence advisor read
  the same canonical scoring service.
- `forecast_error()` may remain temporarily as a compatibility view for legacy exports/tests, but
  it must delegate to canonical ledger evidence or be labeled legacy diagnostic data; it cannot
  drive a second household-facing score.

Contract tests assert that plan confidence, model health, API accuracy, advisor evidence, and export
report the same evidence version, eligible-day count, quality label, and deciding reason. This avoids
the trust failure of two screens reporting different forecast quality from the same history.

### 3.4 Planner integration and bounded candidate generation

The forecaster supplies uncertain load trajectories to `ems.intelligence`. The existing
deterministic adaptive planner currently returns one plan, so candidate enumeration is explicit new
machinery rather than an assumed capability. Add a pure candidate generator with at most six unique
plans:

1. The current baseline rule plan.
2. The prior still-valid plan, when one exists.
3. An adaptive plan sized to the 50th-percentile protected-window net demand.
4. An adaptive plan sized to the 70th-percentile protected-window net demand.
5. An adaptive plan sized to the 85th-percentile protected-window net demand.
6. An adaptive plan sized to the 95th-percentile protected-window net demand.

Identical plans are deduplicated before evaluation. Candidate sizing may change only the forecast
path/energy target supplied to the existing planner; it does not add new intents or bypass planner
economics. Candidate-generation failure returns only the baseline rule plan.

The intelligence layer evaluates every candidate across the same set of plausible load-and-solar
trajectories using the existing energy projection model. It selects the lowest-cost candidate that
satisfies the chosen risk preference, then sends it through the unchanged plan validator.

This work implements the required B-47 slice: a `Planner` protocol, registry entry for the baseline
and risk-aware planners, and a persisted `PlannerInputSnapshot`. The snapshot owns the deterministic
scenario seed and cache key. The broader future learned-planner `rule_based | ml | advisory` switch
remains separate.

Scenario construction and candidate evaluation run off the event loop with a hard two-second caller
budget. An overrun discards that evaluation and returns the baseline rule plan for the cycle. Only
one evaluation is in flight: while an over-budget worker finishes, later cycles use baseline rather
than queueing more work; a worker still running after ten seconds is terminated/restarted. The
result is memoized by `PlannerInputSnapshot` for the quantized control cycle so dashboard, API, and
iOS reads cannot trigger duplicate ensembles. This is the planning-critical slice of B-48; the
backlog item retains its broader per-cycle reporting/projection cleanup.

No learned model emits unrestricted battery actions or continuous power commands.

### 3.5 Relationship to the existing optional ML design

This design deliberately changes the current `SPEC.md`/`docs/ml-layer.md` premise that all learned
load forecasting is accelerator-gated:

- CPU baseline and quantile-tree load forecasting become an optional forecasting capability that
  can run on Mac, Pi, or Jetson.
- `forecast.use_developing_model` controls early forecast blending.
- `planner.risk_preference` controls the deterministic risk-aware planner and is orthogonal to
  model family.
- The existing `planner.mode = rule_based | ml | advisory` contract is reserved for a future
  learned planner; this cluster does not implement or activate `MlPlanner`.
- A future neural load forecaster may be CPU- or accelerator-backed behind `LoadForecaster` without
  changing the deterministic planner contract.
- Local-LLM acceleration and the independent external/template explainer choices remain unchanged.

The first implementation batch must reconcile `SPEC.md` §§2/4/8/9/13/15,
`docs/ml-layer.md`, `docs/config-reference.md`, `CLAUDE.md`, and `README.md` with these boundaries.
Documentation updates ship in the same commits as their corresponding schema/config/runtime changes;
the implementation is not complete while the old accelerator-gated forecaster story remains.

## 4. Data foundation and provenance

### 4.1 Compact long-horizon observation store

The current 90-day raw-history retention is insufficient for seasonal evidence. Add a compact
15-minute observation table retained for 400 days independently of raw five-minute sample
retention. One year is approximately 35,000 rows.

Each row contains:

- Slot start in UTC.
- Mean reconstructed `non_ev_load_w`.
- Actual solar power/energy.
- Sample count and coverage fraction.
- Relevant source freshness and quality flags.
- Flags for implausible/clamped input, manual override, calibration/setup activity, and other
  exclusions.

Training and evaluation exclude incomplete or flagged rows. UTC defines slot identity; the site
timezone is applied only when deriving calendar features, including DST-aware local hour and day.

### 4.2 Prediction ledger

Persist every forecast before its outcome is known:

- Exact `issued_at` timestamp.
- Target slot and lead time.
- Forecast type (`load` or `solar`).
- Baseline and active low/expected/high values.
- Source/model version and feature-schema version.
- Forecast-quality state at issue time.

This ledger is the only source used for out-of-sample scoring. Recomputing a historical forecast
with information learned later is not valid evaluation.

### 4.3 Solar forecast provenance

Replace or migrate the existing date-only solar snapshot semantics to exact `issued_at` provenance.
Overlapping forecasts for the same target slot remain distinct. At 18:00 site-local time, persist a
canonical load and solar forecast for every slot in the next local calendar day. Accuracy and model
quality score that canonical snapshot; later nowcasts remain available for planning and diagnostics
but cannot make day-ahead accuracy look artificially good. If the 18:00 attempt fails, the first
successful snapshot before 20:00 is canonical; otherwise that target day has no canonical forecast
and is excluded rather than backfilled with hindsight.

Solar uncertainty uses a separate lightweight calibrator over provider forecast residuals. It
calibrates the existing P10/P50/P90-like paths rather than replacing the provider. Dawn and dusk
slots, where multiplicative ratios are unstable, are handled with an additive or daylight-normalized
error model rather than raw `actual / forecast` ratios.

### 4.4 Weather scope

Weather is excluded from the first production model because the repository does not yet persist
the weather forecast that was available at each historical forecast origin. Solar forecasts already
encode much of the cloud signal.

Weather may be added later only after issue-time weather snapshots are stored and an ablation test
shows repeatable unseen-data improvement. Present-day or hindsight weather must never be joined into
historical training examples.

### 4.5 Migration and historical backfill

This is the first schema evolution that cannot be expressed safely with another
`CREATE TABLE IF NOT EXISTS`. Complete B-52's remaining ordered `PRAGMA user_version` migration
runner before changing forecast storage. Startup migration must be transactional, idempotent,
backup-tested, and exercised against both the previous schema and a fresh database.

During migration:

- Aggregate retained raw/derived samples into the 400-day compact observation table.
- Copy existing `(issued_date, start)` forecast rows into the prediction ledger without deleting
  the legacy table until export/replay compatibility has moved.
- Mark migrated rows `provenance=legacy_date_keyed` and never pretend they have exact issue time.
- Treat a legacy date as an approximate midnight snapshot only when raw-recorder evidence exists
  within the first 15 minutes of that UTC date; assign `issued_at=00:00Z` and require at least an
  18-hour target lead. All other legacy rows are `legacy_unknown` and excluded from quality gates.
- Eligible `legacy_date_keyed` rows may warm-start `Low`/`Medium` evidence and residual calibration,
  but cannot contribute to `High`, canonical head-to-head promotion, or the four-week stability
  requirement. The UI explains when quality is temporarily based partly on migrated evidence.

This backfill prevents a continuously running installation with valid historical snapshots from
needlessly restarting at zero while preserving the anti-leakage distinction between approximate
legacy provenance and the new canonical 18:00 snapshots.

### 4.6 Pre-migration production diagnostic

Before writing the migration, capture a read-only diagnostic from the live production database:

- Raw, derived, and forecast row counts and date ranges.
- Forecast rows grouped by `issued_date`.
- Raw/forecast 15-minute bucket overlap and daytime overlap.
- Parse failures, timezone offsets, duplicate target slots, and first/last recorder timestamps per
  issue date.
- The exact rows supplied to the current `forecast_error()` path when it reports `n_slots=0`.

Classify the cause as missing persistence, no elapsed overlap, timestamp parsing/alignment, query
window/cap, wrong database, or another demonstrated condition. Add a regression fixture reproducing
the finding before building the ledger on the same pipeline. If the live database is unavailable,
the implementation batch is blocked at this diagnostic gate rather than inferring from demo/e2e
data.

## 5. Forecast model and feature procedure

### 5.1 Features

The initial tree model uses only data available at the forecast origin:

- Local hour/day/weekend/season and daylight state.
- Lagged household demand known at the origin, including recent, prior-day, and prior-week values.
- Rolling demand averages and variability computed strictly before the origin.
- Missingness indicators.
- Forecast horizon position.

Recursive features that depend on unknown future actual load are prohibited. A single pure,
versioned feature builder is shared by training and live inference to prevent training/serving skew.

### 5.2 Quantile prediction and calibration

Train low, expected, and high quantile models. Use rolling-origin evaluation rather than random
train/test splits. After prediction, apply conformal-style calibration based only on prior
out-of-sample residuals. Enforce finite non-negative ordered output before publishing the bundle.

Early personalized forecasts shrink toward the baseline. The shrinkage weight is capped by the
evidence state and then recalibrated; simply averaging two sets of quantiles is not assumed to
preserve probability coverage.

With too little history for a credible tree model, early value comes from the improved baseline,
recent-demand correction, prediction tracking, and calibrated default bands. The UI must not imply
that pressing the training button creates information that has not yet been observed.

### 5.3 Forecast-quality evidence

Quality is calculated from independent evaluation days, not the number of correlated 15-minute
slots. Evidence includes:

- Complete distinct forecast days.
- Weekday/weekend and time-of-day coverage.
- Model and calibration age.
- Expected-value MAE and signed energy bias.
- Quantile pinball loss.
- Interval coverage and sharpness.
- Performance relative to the active baseline over multiple rolling windows.
- Downstream replay cost and protected-window coverage.

The label describes the reliability of the active forecast system, not algorithm sophistication. A
well-calibrated baseline may be `High`; a boosted model that fails to improve it remains rejected.

Initial versioned evidence gates, evaluated over the most recent eligible 56 days, are:

- `Low`: any state that does not satisfy `Medium`, including missing/stale evaluation evidence.
- `Medium`: at least 7 complete evaluation days containing at least 5 weekdays and 2 weekend days;
  at least 80% usable slot coverage; absolute daily-energy bias no greater than 20%; normalized MAE
  no greater than 45% of mean observed load; nominal 80% interval coverage between 60% and 95%; and
  normalized mean 80% interval width no greater than 200%; and weighted interval score no more than
  5% worse than `BaselineLoadForecaster`.
- `High`: at least 28 complete evaluation days containing at least 20 weekdays and 8 weekend days;
  at least 90% usable slot coverage; absolute daily-energy bias no greater than 10%; normalized MAE
  no greater than 30%; nominal 80% interval coverage between 72% and 88%; normalized mean interval
  width no greater than 125%; and the bias, normalized MAE, coverage, and width thresholds holding
  independently in each of the last four complete weekly evaluation windows.

If 28 eligible days do not naturally contain 20 weekdays and 8 weekend days because rows were
excluded, evaluation expands backward within the 56-day cap; otherwise quality remains `Medium`.
An evaluation older than 7 days caps quality at `Medium`; older than 30 days is `Low`. These are
starting gates to be validated with replay, stored with a `quality_rules_version`, and changed only
deliberately. Elapsed age or sample count alone never awards a higher label.

Normalized MAE divides MAE by `max(mean observed load, 100 W)` so an unusually low-load evaluation
window cannot create an unstable ratio. Normalized interval width uses the same denominator. The
width ceilings prevent an uninformatively broad band from earning `Medium` merely by covering most
observations.

Before 7 complete evaluation days exist, no tree model is eligible for promotion. After 3 complete
days, the developing path may apply only the regularized recent-demand correction, subject to the
25% cap and the user's toggle; `Update models now` otherwise reports `More history needed`.

## 6. Mathematically defensible scenario planning

### 6.1 Whole-trajectory uncertainty

Combining per-slot low solar with high load does not create a known end-to-end success probability,
because forecast errors persist across adjacent slots. Preserve temporal correlation by sampling
complete historical residual blocks from comparable days. Until enough comparable residual days
exist, use broad, conservative default trajectories.

Load and solar residuals are sampled as a paired local-calendar-day block so their observed
correlation is retained. Comparable-day selection is deterministic and hierarchical:

1. Prefer the same weekday/weekend class, climatological season (`DJF`, `MAM`, `JJA`, `SON`), and
   canonical forecast-solar daily-energy tercile (`low`, `middle`, `high`).
2. If fewer than 14 eligible paired days remain, drop the solar-tercile match.
3. If still fewer than 14 remain, drop weekday/weekend matching but retain season.
4. A pool of 7–13 days may be used only with residual magnitudes inflated by 25% and forecast
   quality capped at `Low` for risk-planning purposes.
5. Fewer than 7 eligible paired days disables block bootstrap; use the versioned broad analytic
   fallback trajectories and the baseline rule plan remains the only executable plan.

Sampling is with replacement from the selected pool. Pool definition, member dates, fallback tier,
and inflation factor are stored with the `PlannerInputSnapshot` for replay.

Every planning evaluation uses a fixed random seed derived from its input snapshot or an otherwise
stored scenario-set identifier. Replaying the same snapshot must reproduce the same choice.
The initial ensemble contains 200 trajectories, including the baseline/central trajectory. This is
small enough for the existing projection engine and large enough to distinguish the initial
70%/85%/95% policy gates.

### 6.2 Candidate scoring

Project each candidate over the identical trajectory ensemble and calculate:

- Expected grid cost under the configured import/export economics.
- Battery degradation cost.
- Ability to cover the protected expensive window without exhausting the usable planning buffer.
- Target-SoC feasibility by its deadline.
- Grid import caused by forecast shortfall.
- Mode switches and other operational constraints.

The minimum reserve remains a hard deterministic constraint. The probabilistic criterion governs
the additional planning buffer and protected-window coverage, not permission to cross that floor.

The chosen plan minimizes expected economic cost among candidates that satisfy the versioned
internal threshold for the household's qualitative preference. If none qualifies, use the baseline
rule plan. The unchanged validator has final authority.

### 6.3 Minimal explanation evidence

B-65 records enough structured evidence to explain:

- Selected qualitative risk preference.
- Forecast source and quality.
- Whether fallback or developing-model shrinkage was used.
- Why the selected candidate beat or displaced the baseline.
- The dominant uncertainty or safety constraint.

This is deliberately narrower than B-74's future cross-platform optimization explainability layer.

## 7. Dynamic reserve recommendation

The B-67 advisor evaluates the smallest additional planning buffer that would satisfy the selected
risk preference across the calibrated trajectory ensemble. It compares that result with the user's
current default and emits advice only when the difference is at least 5 percentage points of SoC.
The recommendation is clamped between the configured hard reserve floor and the applicable seasonal
SoC ceiling.

Advice contains:

- Recommended reserve or buffer.
- Plain-language reason based on observed evidence.
- Forecast-quality label and any limitation.
- Expected consequence of accepting or declining.

Example: “Tomorrow looks cloudy and evening use has recently been higher, so keeping a little more
energy available would reduce the chance of buying during the peak.”

The advisor cannot modify settings in this release. Any existing one-tap adoption mechanism must
still require a user action, confirmation, audit entry, and reversibility. Automatic daily adoption
belongs to B-78.

## 8. Training lifecycle and artifact management

### 8.1 Champion/challenger lifecycle

1. Emit and persist baseline and active forecasts.
2. Record actual outcomes and materialize eligible examples.
3. Train a candidate on schedule or via a manual request.
4. Evaluate it with rolling-origin splits against the baseline and current champion.
5. Replay candidate forecasts through planning.
6. Promote only when forecast, calibration, and downstream safety gates pass.
7. Load the promoted artifact in a fresh worker before marking it active.
8. Continue monitoring and fall back on drift, staleness, corruption, or inference failure.

Repeated manual requests with no new complete evidence return `No new evidence to learn from`
instead of refitting and reconsidering the same data.

Scheduled training runs once per local day at 03:00 only when at least one new complete observation
day exists. The manual trigger uses the same eligibility rule and pipeline; it is not a separate
training path.

### 8.2 Promotion requirements

A candidate must satisfy all of the following:

- Valid finite ordered forecasts over the supported horizon.
- At least 7 complete evaluation days and all `Medium` evidence gates.
- At least a 2% improvement over `BaselineLoadForecaster` in either weighted interval score or MAE,
  with the other metric no more than 1% worse.
- Acceptable interval calibration, sharpness, and signed bias under the quality gates in §5.3.
- No increase in protected-window failures during replay.
- Replay cost and degradation no more than 1% worse than the baseline plan.
- Successful serialization, integrity verification, and fresh-process reload.
- Sufficient independent evaluation days for the quality claim.

Replacing an existing personalized champion additionally requires at least a 1% weighted-interval-
score improvement, or at least a 1% replay-cost improvement with weighted interval score no more
than 1% worse. A personalized model may receive 100% forecast weight only at `High` quality and when
the upper bound of a 90% day-block-bootstrap confidence interval for its weighted-interval-score
difference versus `BaselineLoadForecaster` is below zero.

Failed candidates are retained only as bounded diagnostic metadata or deleted; they never overwrite
the champion.

### 8.3 Versioned atomic artifacts

Store artifacts below the configured data directory with:

- Unique model version and creation timestamp.
- Training/evaluation windows.
- Model family and runtime/dependency versions.
- Feature-schema version.
- Evaluation and replay summary.
- Integrity checksum.

Write to a temporary version directory, verify it, then atomically update the active pointer. Keep
at least the current and previous known-good versions. Never load an artifact supplied through an
API request or other untrusted path.

The tree implementation is packaged as an optional CPU `forecasting` dependency group containing
scikit-learn and its transitive numerical runtime. If it is absent or incompatible, the capability
is reported as unavailable and the baseline starts normally. Model data and artifacts remain local;
no household history is sent to an external training service.

B-43's default lean image remains unchanged and must continue to build with
`uv sync --frozen --no-dev` without installing scikit-learn. A separate documented forecasting
install/build target uses `uv sync --frozen --no-dev --extra forecasting`; its lockfile state is
committed and tested in CI. The Mac installer and container deployment expose an explicit
forecasting-capability option rather than silently enlarging every installation.

## 9. Manual training and UI/API contract

### 9.1 Settings and endpoints

Settings:

- `forecast.use_developing_model: bool`, default `false`.
- `planner.risk_preference: cautious | balanced | savings_focused`, default `balanced`.

Endpoints:

- `GET /api/models/status`: active source/version, quality level and reasons, evidence coverage,
  last successful update, fallback state, reserve advice, and current job summary.
- `POST /api/models/train`: authenticated asynchronous `Update models now` request; returns the
  existing active job when one is already running.
- `GET /api/models/jobs/{id}`: job stage and outcome.

Training stages are `preparing`, `training`, `evaluating`, `calibrating`, and `finished`. Outcomes
use household language: `Forecast improved`, `Current model remains better`, `More history needed`,
`No new evidence to learn from`, or `Could not update`.

### 9.2 Portal and iOS

The web System page extends its existing B-76 panel. iOS has no Model Health view today, so this
batch adds a new read-mostly `ModelHealthView`, API models/client methods, navigation entry, loading/
error/empty states, and Swift tests. Both surfaces show:

- Forecast quality and its deciding reason.
- Active baseline/developing/mature source and fallback state.
- The developing-model toggle.
- Risk preference with plain-language consequence.
- `Update models now`, progress, and the last outcome.
- Current reserve recommendation, when available.

The iOS action is a narrow authenticated maintenance action, not general battery control. It uses
the existing stored bearer token. Web mutations use the existing authentication and same-origin
protections. Every manual trigger and setting change is audit-logged.

Training completion never issues an immediate battery command. A promoted forecast becomes eligible
only during a subsequent normal planning cycle.

## 10. Failure and missing-data behavior

- A missing recent lag uses a baseline-derived value plus an explicit missingness indicator.
- An incomplete or flagged target slot is excluded from training and scoring.
- A missing optional feature is omitted or marked missing; hindsight data is never substituted.
- Insufficient residual history uses broad default uncertainty bands.
- A missing ML dependency disables the personalized adapter but not the baseline or EMS startup.
- A slow or failed inference falls back for that forecast cycle.
- A corrupt/incompatible artifact rolls back to the previous champion, then the baseline.
- A training failure leaves the active model unchanged.
- A partial aligned load/solar/price horizon produces a shorter honest plan horizon.
- A scenario or candidate evaluation failure returns to the baseline rule plan.
- An invalid selected plan is rejected by the existing validator.

Promotions, rejections, fallbacks, manual jobs, and settings changes are audit-logged. Model health
reports recent forecast error, calibration, active version, artifact age, fallback rate, training
outcome, and any unavailable capability.

## 11. Verification

### 11.1 Unit and contract tests

- Feature generation contains no information after the forecast origin.
- UTC alignment and local calendar features work across DST transitions.
- Missing data and quality flags follow the documented exclusions/fallbacks.
- Forecast bundles are finite, non-negative, aligned, and ordered.
- Baseline, tree, blended, and fake future-neural adapters satisfy the same public contract.
- Quality transitions and developing-model gates are deterministic.
- Medium/High width ceilings reject uninformatively broad intervals.
- All risk preferences select only eligible candidates and preserve the hard reserve.
- Candidate generation emits at most six unique plans and degrades to baseline-only on error.
- Artifact promotion, rollback, checksum failure, and version incompatibility are safe.

Statistical tests use fixed inputs/seeds and assert invariants or bounded metrics rather than fragile
byte-for-byte model coefficients.

### 11.2 Offline and replay verification

- Rolling-origin evaluation only; no random time-series split.
- Day-level resampling for uncertainty on metric differences.
- MAE and energy bias for expected forecasts.
- Pinball loss, interval coverage, and sharpness for forecast bands.
- Comparisons across several evaluation windows, not one favorable split.
- Planner replay measures cost, degradation, protected-window failures, target feasibility, reserve
  behavior, and switching.
- A candidate cannot be promoted on forecast metrics alone when downstream replay regresses.

### 11.3 Integration and failure tests

- Prediction/outcome ledger persistence and canonical lead-time scoring.
- Previous-schema migration, backup/restore, legacy provenance classification, and historical
  observation/forecast backfill.
- A production `n_slots=0` regression fixture captured from the diagnostic in §4.6.
- Plan confidence, B-76 model health, `/api/accuracy`, advisor evidence, and export all report the
  same canonical evidence version and quality result.
- Scheduled and manually triggered training.
- Concurrent-trigger deduplication and clean shutdown/interruption.
- Authenticated web and iOS requests.
- Fresh-process artifact reload.
- Missing dependency, corrupt artifact, timeout, worker crash, and partial-horizon fallback.
- A candidate/scenario computation exceeding two seconds returns the baseline plan and does not
  block the event loop; no second evaluation queues behind it; a ten-second worker is restarted;
  repeated reads in one control cycle hit the snapshot memo.
- Promotion does not immediately command the battery.
- Advisory/dry-run/live mode boundaries remain intact.

## 12. Delivery batches

This design is implemented through three separately reviewed implementation plans. A batch may use
multiple small PRs, but its acceptance gate is shared. Do not create one 500-line-design-sized
implementation plan or one monolithic PR.

### Batch A — Evidence foundation, migration, and status surfaces (start next)

1. Run the live `n_slots=0` diagnostic and land its regression fixture.
2. Complete B-52's migration runner and verify backup/restore from the previous schema.
3. Add/backfill the compact observation store and exact-provenance prediction ledger.
4. Add the baseline forecast contract, canonical 18:00 snapshot, single scoring authority, quality
   rules, and legacy warm-start classification.
5. Rewire plan confidence, B-76 model health, `/api/accuracy`, solar advisor, and export to that one
   scoring authority.
6. Add the model-service shell, status/manual-update job lifecycle, audit events, and baseline-only
   `Update models now` behavior.
7. Extend the web System panel and add the new iOS `ModelHealthView` with contract tests.
8. Reconcile SPEC/ML/config/CLAUDE/README documentation and establish optional-dependency build
   targets without installing scikit-learn in the default image.

Deploy Batch A as soon as its migration and regression gates pass. Evidence only accumulates after
the ledger exists: each week of delay moves the 7-day `Medium` and 28-day `High` earliest dates by a
week. Historical backfill reduces, but cannot eliminate, that delay because migrated approximate
snapshots cannot qualify for `High`.

### Batch B — Personalized challenger and calibration

1. Add the shared versioned feature builder and quantile-tree optional adapter.
2. Add worker-process training, atomic artifacts, rolling-origin evaluation, and promotion gates.
3. Run the quantile tree as a shadow challenger and expose comparisons in model status.
4. Add the developing-model toggle and recalibrated shrinkage.
5. Add canonical solar-band calibration and scheduled/manual retraining behavior.

Batch B does not change executable battery plans. Acceptance requires forecast contract, leakage,
artifact-failure, and shadow-evaluation tests plus production evidence from Batch A.

### Batch C — Risk-aware planning and reserve advice

1. Implement the B-47 planner-protocol/input-snapshot slice and bounded candidate generator.
2. Add comparable-day paired residual blocks, analytic low-data fallback, deterministic 200-path
   ensembles, the two-second budget, and B-48-style per-snapshot memoization.
3. Evaluate candidates in replay/advisory mode and expose minimal B-65 evidence.
4. Enable risk-aware selection behind the existing dry-run gate.
5. Activate live only after replay and multi-week dry-run show no safety regression.
6. Ship recommendation-only B-67 dynamic reserve advice; retain B-78 as the later automation item.

This sequencing starts the time-dependent evidence clock immediately while keeping personalized
forecasting and forecast-driven control as separate, independently reversible releases.

## 13. Explicit non-goals

- No GPU/Jetson requirement for this cluster.
- No neural network in the first implementation.
- No learned model emitting battery commands or continuous power setpoints.
- No automatic reserve adoption; that is B-78.
- No weather features without issue-time weather provenance and proven benefit.
- No general B-74 optimization-explanation implementation.
- No weakening of validator, reserve, freshness, dwell, write-cap, or fail-safe rules.
