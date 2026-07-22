# Targets for the serve Web UI dogfood: the Playwright (web) backend driving Bajutsu's *own*
# `serve` Web UI. Like demos/web this needs no Mac and no Simulator — the app under test is the
# serve SPA, so it runs on Linux in the same toolchain as `make check`. Invoke from anywhere with
# `make -C demos/serve-ui <target>`.
.PHONY: web-deps serve-ui e2e

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
# which installs the iOS backend deps) because the dogfood is web-only — no iOS actuator is involved.
# Ctrl-C to stop. --root confines the in-UI file browser to this directory; --runs points at the
# committed crawl-history fixture so the Crawl History tab shows the past run the dogfood reopens.
serve-ui:
	cd $(ROOT) && uv run bajutsu serve --config $(INNER_CONFIG) --root $(HERE) --runs $(HERE)/fixtures/crawl-runs --port $(PORT)

# End-to-end: drive the serve Web UI through the Playwright backend with the Tier-A deterministic
# scenarios. The app under test (the inner serve) is brought up and torn down by the config's
# `launchServer` (targets.webui), so the run is self-contained — no hand-started server here. No
# device, no Simulator, no LLM — pass/fail is the scenarios' machine assertions only.
e2e: web-deps
	cd $(ROOT) && uv run bajutsu run --target webui --backend web --config $(CONFIG) --no-network
