# Targets for the serve Web UI dogfood: the Playwright (web) backend driving Bajutsu's *own*
# `serve` Web UI. Like demos/web this needs no Mac and no Simulator — the app under test is the
# serve SPA, so it runs on Linux in the same toolchain as `make check`. Invoke from anywhere with
# `make -C demos/serve-ui <target>`.
.PHONY: web-deps serve-ui e2e codegen e2e-playwright

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
HERE := $(ROOT)/demos/serve-ui
CONFIG := $(HERE)/dogfood.config.yaml
# The config the *inner* serve loads, so its dropdowns have a real app + scenario to show. We
# reuse the web demo's config (targets.web → demos/web/scenarios/smoke.yaml) rather than invent a
# fixture: the dogfood asserts the WebUI renders that config, not what the config contains.
INNER_CONFIG := $(ROOT)/demos/web/demo.config.yaml
PORT ?= 8799

# Install the web backend (Playwright python package + the Chromium binary it drives). Idempotent,
# same extra as demos/web.
web-deps:
	cd $(ROOT) && uv sync --extra web && uv run playwright install chromium

# Launch the serve Web UI under test for manual poking. We start serve directly (not `make serve`,
# which installs the idb companion) because the dogfood is web-only — no iOS actuator is involved.
# Ctrl-C to stop. --root confines the in-UI file browser to this directory.
serve-ui:
	cd $(ROOT) && uv run bajutsu serve --config $(INNER_CONFIG) --root $(HERE) --port $(PORT)

# End-to-end: drive the serve Web UI through the Playwright backend with the Tier-A deterministic
# scenarios. The app under test (the inner serve) is brought up and torn down by the config's
# `launchServer` (targets.webui), so the run is self-contained — no hand-started server here. No
# device, no Simulator, no LLM — pass/fail is the scenarios' machine assertions only.
e2e: web-deps
	cd $(ROOT) && uv run bajutsu run --target webui --backend web --config $(CONFIG) --no-network

# Export every dogfood scenario as a native Playwright spec (BE-0137). Regenerated from the YAML
# on every invocation — the scenarios stay the single source of truth and the specs are never
# edited by hand (playwright-tests/ is gitignored). Codegen is pure text mapping: no browser, no
# device, no AI.
codegen:
	rm -rf $(HERE)/playwright-tests && mkdir -p $(HERE)/playwright-tests
	cd $(ROOT) && for f in demos/serve-ui/scenarios/*.yaml; do \
		uv run bajutsu codegen --config $(CONFIG) --target webui --emit playwright \
			-o $(HERE)/playwright-tests/$$(basename $${f%.yaml}).spec.ts $$f; \
	done

# The same UI regression net, natively: generate the specs and run them with @playwright/test
# (needs Node). The inner serve is brought up by playwright.config.ts's webServer — self-contained
# like `e2e`, but running in a team's existing Playwright toolchain / CI instead of bajutsu's
# runner. No AI at test time; pass/fail is Playwright's own assertions.
# `npm ci` (not `install`) installs exactly the committed package-lock.json — deterministic and
# drift-proof, the right mode for a CI vehicle.
e2e-playwright: codegen
	cd $(HERE) && npm ci && npx playwright install chromium && npx playwright test
