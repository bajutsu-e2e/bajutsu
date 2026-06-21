# Build / run targets for the web demo (BE-0041): the Playwright backend driving a tiny static
# web app. Unlike the iOS demos this needs no Mac and no Simulator — it runs on Linux inside the
# same toolchain as `make check`. Invoke from anywhere with `make -C demos/web <target>`.
.PHONY: web-deps app-serve e2e

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
APP := $(ROOT)/demos/web/app
CONFIG := $(ROOT)/demos/web/demo.config.yaml
PORT ?= 8787

# Install the web backend: the Playwright python package (uv extra) + the Chromium binary it
# drives (a separate download, not a pip dep). Idempotent.
web-deps:
	cd $(ROOT) && uv sync --extra web && uv run playwright install chromium

# Serve the static demo app for manual poking (or to point the Web UI at). Ctrl-C to stop.
app-serve:
	cd $(APP) && python3 -m http.server $(PORT)

# End-to-end: serve the app in the background, run the smoke scenario through the Playwright
# backend, then tear the server down. No device, no Simulator — pure Linux.
e2e: web-deps
	cd $(APP) && python3 -m http.server $(PORT) >/dev/null 2>&1 & srv=$$!; \
	trap 'kill $$srv 2>/dev/null' EXIT; \
	sleep 1; \
	cd $(ROOT) && uv run bajutsu run --app web --backend web --config $(CONFIG) --no-network
