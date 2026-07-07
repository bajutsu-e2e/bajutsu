# Build / run targets for the web demo (BE-0041): the Playwright backend driving a tiny static
# web app. Unlike the iOS demos this needs no Mac and no Simulator — it runs on Linux inside the
# same toolchain as `make check`. Invoke from anywhere with `make -C demos/web <target>`.
.PHONY: web-deps app-serve e2e record record-offline

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
APP := $(ROOT)/demos/web/app
RECORD := $(ROOT)/demos/web/record
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
# sleep — matches the runner's condition-wait discipline), run the demo's scenarios through the
# Playwright backend, then tear the server down. No device, no Simulator — pure Linux.
# --workers 2 runs the two scenarios on two parallel BrowserContext lanes (BE-0054), so this
# also exercises the web pool's parallel path.
e2e: web-deps
	cd $(APP) && python3 -m http.server $(PORT) >/dev/null 2>&1 & srv=$$!; \
	trap 'kill $$srv 2>/dev/null' EXIT; \
	ready=0; for i in $$(seq 1 100); do \
	  if curl -sf --max-time 2 -o /dev/null "http://localhost:$(PORT)/"; then ready=1; break; fi; \
	  sleep 0.1; \
	done; \
	if [ "$$ready" -ne 1 ]; then echo "demo server did not answer on port $(PORT) within the readiness timeout" >&2; exit 1; fi; \
	cd $(ROOT) && uv run bajutsu run --target web --backend web --config $(CONFIG) --no-network --workers 2

# AI authoring (Tier 1) against the demo web app: real Claude drives the Playwright backend toward
# a natural-language goal and writes the executed steps out as a deterministic scenario — the web
# twin of `make -C demos record`, only the backend differs (a browser, not a Simulator). Serves the
# app first (same background-serve + readiness poll as e2e), then records. Needs ANTHROPIC_API_KEY;
# the deterministic `run`/e2e above needs none. GOAL overrides the first non-comment line of
# record/goals.txt. The authored scenario lands in a gitignored tmp/ file so it never pollutes the
# tracked scenarios dir.
GOAL ?= $(shell grep -vE '^[[:space:]]*#|^[[:space:]]*$$' $(RECORD)/goals.txt | head -1)
OUT ?= $(ROOT)/tmp/web-record.generated.yaml
record: web-deps
	cd $(APP) && python3 -m http.server $(PORT) >/dev/null 2>&1 & srv=$$!; \
	trap 'kill $$srv 2>/dev/null' EXIT; \
	ready=0; for i in $$(seq 1 100); do \
	  if curl -sf --max-time 2 -o /dev/null "http://localhost:$(PORT)/"; then ready=1; break; fi; \
	  sleep 0.1; \
	done; \
	if [ "$$ready" -ne 1 ]; then echo "demo server did not answer on port $(PORT) within the readiness timeout" >&2; exit 1; fi; \
	mkdir -p $(ROOT)/tmp; \
	cd $(ROOT) && uv run bajutsu record --target web --backend web --config $(CONFIG) \
		--goal "$(GOAL)" --out "$(OUT)" --no-erase --no-dismiss-alerts

# The offline, API-key-free twin of `record`: the same record loop driven by a deterministic
# keyword agent against an in-memory FakeDriver (no browser, no model), so it's reproducible in the
# `make check` toolchain. Prints the goal, the generated scenario, and a deterministic replay.
record-offline:
	cd $(ROOT) && uv run python demos/web/record/record_offline.py
