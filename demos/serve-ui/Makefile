# Targets for the serve Web UI dogfood: the Playwright (web) backend driving Bajutsu's *own*
# `serve` Web UI. Like demos/web this needs no Mac and no Simulator — the app under test is the
# serve SPA, so it runs on Linux in the same toolchain as `make check`. Invoke from anywhere with
# `make -C demos/serve-ui <target>`.
.PHONY: web-deps serve-ui e2e

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
HERE := $(ROOT)/demos/serve-ui
CONFIG := $(HERE)/dogfood.config.yaml
# The config the *inner* serve loads, so its dropdowns have a real app + scenario to show. We
# reuse the web demo's config (apps.web → demos/web/scenarios/smoke.yaml) rather than invent a
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

# End-to-end: launch serve (the app under test) in the background, drive its Web UI through the
# Playwright backend with the Tier-A deterministic scenarios, then tear serve down. No device, no
# Simulator, no LLM — pass/fail is the scenarios' machine assertions only.
e2e: web-deps
	cd $(ROOT) && uv run bajutsu serve --config $(INNER_CONFIG) --root $(HERE) --port $(PORT) >/dev/null 2>&1 & srv=$$!; \
	trap 'kill $$srv 2>/dev/null' EXIT; \
	for _ in $$(seq 1 40); do curl -sf http://127.0.0.1:$(PORT)/ >/dev/null 2>&1 && break; sleep 0.5; done; \
	cd $(ROOT) && uv run bajutsu run --app webui --backend web --config $(CONFIG) --no-network
