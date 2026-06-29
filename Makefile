# Smart Energy Manager — convenience targets. The one-command install is `make install`
# (or run ./scripts/install.sh directly). Everything is configured afterwards in the web UI.
.PHONY: install uninstall dev build test e2e lint

install:        ## One-command install + auto-start (macOS / Apple Silicon)
	./scripts/install.sh

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

e2e:            ## Run the Playwright end-to-end tests (hermetic; builds the SPA first)
	cd ems/web/frontend && npm run build && npx playwright test
