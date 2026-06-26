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

# End-to-end: serve the app in the background, wait for it to answer (poll, never a fixed
# sleep — matches the runner's condition-wait discipline), run the smoke scenario through the
# Playwright backend, then tear the server down. No device, no Simulator — pure Linux.
e2e: web-deps
	cd $(APP) && python3 -m http.server $(PORT) >/dev/null 2>&1 & srv=$$!; \
	trap 'kill $$srv 2>/dev/null' EXIT; \
	ready=0; for i in $$(seq 1 100); do \
	  if curl -sf --max-time 2 -o /dev/null "http://localhost:$(PORT)/"; then ready=1; break; fi; \
	  sleep 0.1; \
	done; \
	if [ "$$ready" -ne 1 ]; then echo "demo server did not answer on port $(PORT) within the readiness timeout" >&2; exit 1; fi; \
	cd $(ROOT) && uv run bajutsu run --target web --backend web --config $(CONFIG) --no-network
