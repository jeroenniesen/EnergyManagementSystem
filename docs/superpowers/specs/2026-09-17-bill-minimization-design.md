# Electricity bill minimization

The user approved the six recommendations in the application assessment. Implement them by
extending the current planners, finance and replay services, and Insights. Exclude car features.
No deployment, device writes, commits, or changes to production settings are authorized.

## Economic decisions

Both seasons must price every purchased kWh after losses, estimated wear and risk. Winter
charging accounts for forecast solar in chronological order; surplus after a peak cannot fund
that peak, and storage headroom limits retained surplus. Preserve mode-based intents, target
state of charge, deadlines, validation and the single writer. New planning and learned-load
behavior is evaluated only in dry-run behind an explicit opt-in setting, default off.

## Tariffs and invoice reconciliation

Support explicit date-effective tariff periods, preserving historical assumptions and existing
settings when no periods exist. Import and export must have separately declared components;
post-saldering export cannot be approximated by removing tax alone when the import surcharge
also differs. Validate periods and all numeric values. Provide a read-only invoice comparison
for an observed date window with coverage and a separate fixed-cost input; missing data must
not masquerade as zero cost. Do not invent or automatically activate supplier tariff rates.

## Evidence against AUTO

Extend replay rather than replace it. Use trailing load history available before a decision,
rolling replans, forecast issuance timestamps and known prices. Keep oracle foresight clearly
labeled. Carry scenario-specific stored energy across contiguous days and disclose/reinitialize
gaps. Report grid cost, estimated wear and net economic cost separately, with terminal stored
energy accounted for so draining the battery does not manufacture a saving. Retain existing
fields where possible and label replay as simulated, never measured savings.

## Calibration and household demand

Add a recent-weighted weekday/weekend hourly profile with uncertainty and cold-start fallback.
Evaluate its error against the existing hourly baseline on held-out chronological data.
Only evaluate changed live planning behavior in dry-run; advice and replay may use the new model.
Calibration is recommendation-only: estimate usable capacity and round-trip efficiency only
from sufficient, contiguous, physically plausible comparable charge/discharge evidence; report
insufficient evidence honestly. Standby loss is not identifiable from arbitrary household
meter readings and must not be fabricated. Expose measured idle draw only with clear evidence.

## Reserve and consumption advice

Recommend an operating target above the unchanged hard reserve floor using upcoming load,
solar uncertainty and prices. Explain both required energy and the estimated incremental cost
of extra stored energy; do not automatically change the reserve or any setting.
Detect sustained night-baseload increases and persistently high household usage using comparable
historical windows; surface solar forecast underperformance using existing accuracy evidence.
Add read-only contiguous appliance scheduling advice for a user-specified duration, consumption
and deadline, valued at marginal import cost or forgone solar export credit. No device control.

## Product integration and acceptance

Insights exposes money-saving advice and calibration evidence, appliance timing and invoice
comparison with loading/empty/error states. Counterfactual copy distinguishes simulated bill
reduction from net benefit after wear and from observed finance. Use existing authenticated
routes and styling. Backend regression tests cover economics, chronology, missing/stale inputs,
hard floors and read-only behavior; build and exercise the actual API/UI with the hermetic
harness. Update SPEC and BACKLOG with precise implemented scope and remaining real-world
acceptance (invoice input and multi-day dry-run require household evidence).
