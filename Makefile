# Smart Energy Manager — convenience targets. The one-command install is `make install`
# (or run ./scripts/install.sh directly). Everything is configured afterwards in the web UI.
.PHONY: install upgrade uninstall restart dev build test e2e lint replay perf-check

install:        ## One-command install + auto-start (macOS / Apple Silicon)
	./scripts/install.sh

upgrade:        ## Pull the latest version, rebuild, and restart (keeps your data)
	./scripts/upgrade.sh

restart:        ## Restart the running app (needed after device/connection setting changes)
	./scripts/restart.sh

uninstall:      ## Stop + remove the auto-start service (keeps your data)
	./scripts/uninstall.sh

dev:            ## Run in the foreground on this machine (no auto-start service)
	./scripts/install.sh --foreground

build:          ## Build the React dashboard
	cd ems/web/frontend && npm ci --no-audit --no-fund && npm run build

test:           ## Run the Python test suite
	uv run pytest ems/tests

lint:           ## Lint the Python code
	uv run ruff check ems

replay:         ## Replay the last 14 recorded days through the planner (read-only cost report)
	uv run python -m ems.replay --days 14

e2e:            ## Run the Playwright end-to-end tests (hermetic; builds the SPA first)
	cd ems/web/frontend && npm run build && npx playwright test

perf-check:    ## Run canned perf workload and print budget pass/fail table
	uv run python -m ems.tools.perf_check
