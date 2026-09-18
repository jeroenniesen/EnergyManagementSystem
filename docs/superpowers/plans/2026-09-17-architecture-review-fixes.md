# Architecture review fixes

Authorized follow-up to review of bf17485..3ea1ffb. Work in the isolated architecture
worktree; preserve one writer, AUTO fail-safe, settings, and API compatibility.

1. Reproduce the omitted car-guard setpoint through the real control cycle; restore its
   state input and verify unchanged commands do not consume the retune budget.
2. Reproduce savings/metadata disagreement; share configured efficiency, wear and risk.
3. Reject malformed core API values with accurate nested response models while preserving
   valid empty/unavailable responses and additive fields.
4. Strengthen adapter checks to compare contract arguments, optionality and types; include
   deliberately incompatible adapters to prove the check detects real call failures.
5. Thread one injectable clock through moved services and their planning/diagnostic
   collaborators; replace untyped service callback lookup with explicit typed dependencies.
6. Run independent review, full backend/lint, frontend build/unit and relevant browser flows.
   Publish the architecture fixes and integrate them into the existing draft bill PR.

No live settings, deployment or hardware operations. This fixes the reviewed boundaries;
full migration of all application orchestration out of create_app is a separate project.

## Verification ledger

- Both behavioral bugs reproduced in failing tests before fixes.
- Added negative response/adapter fixtures plus populated API and Dutch DST-clock coverage.
- Independent reviews of response contracts, clock composition, and savings consistency found
  no remaining verified issue. Source planning callbacks retain their explicit timestamp contract.
- Architecture verification: 1,832 backend tests passed, plus 30 focused control/clock tests
  after the final callback signature alignment; 92 browser checks and 32 frontend tests passed.
- Frontend production build, Ruff, and diff checks passed.
- No live settings, hardware writes, or deployment performed. Publish as the prerequisite for
  the existing bill-minimization draft PR; keep main unchanged.
