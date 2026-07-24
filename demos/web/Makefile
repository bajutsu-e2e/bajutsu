# Build / run targets for the web demo (BE-0041): the Playwright backend driving a tiny static
# web app. Unlike the iOS demos this needs no Mac and no Simulator — it runs on Linux inside the
# same toolchain as `make check`. Invoke from anywhere with `make -C demos/web <target>`.
.PHONY: web-deps app-serve e2e e2e-network codegen-e2e record record-handoff record-offline record-handoff-offline

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
APP := $(ROOT)/demos/web/app
RECORD := $(ROOT)/demos/web/record
CODEGEN := $(ROOT)/demos/web/codegen
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
	cd $(ROOT) && uv run bajutsu run --target web --backend web --config $(CONFIG) --no-network --exclude network --workers 2

# Web network smoke (BE-0282): the real network path — page.route interception, requestfinished
# capture, the `mocked` flag, and redaction of really-captured evidence — which `e2e` above opts out
# of with --no-network. Runs ONLY the `network`-tagged scenario (network.yaml) WITH network on
# (--network), into a throwaway runs dir, then asserts the persisted network.json masks the Sync
# request's secret (the redaction gap the run grammar can't assert). Same background-serve +
# readiness poll as `e2e`; no device, no Simulator — pure Linux.
#
# Android has no native network monitor yet (adb declares no NETWORK capability), so there is no
# Android twin of this lane — the gap is deliberate and recorded in web-e2e.yml and docs, not an
# oversight.
NET_RUNS ?= $(ROOT)/tmp/web-network-runs
e2e-network: web-deps
	cd $(APP) && python3 -m http.server $(PORT) >/dev/null 2>&1 & srv=$$!; \
	trap 'kill $$srv 2>/dev/null' EXIT; \
	ready=0; for i in $$(seq 1 100); do \
	  if curl -sf --max-time 2 -o /dev/null "http://localhost:$(PORT)/"; then ready=1; break; fi; \
	  sleep 0.1; \
	done; \
	if [ "$$ready" -ne 1 ]; then echo "demo server did not answer on port $(PORT) within the readiness timeout" >&2; exit 1; fi; \
	rm -rf $(NET_RUNS); \
	cd $(ROOT) && uv run bajutsu run --target web --backend web --config $(CONFIG) \
		--network --tag network --runs-dir $(NET_RUNS) && \
	uv run python demos/web/network/assert_redaction.py $(NET_RUNS)

# Real-compile gate for the Playwright codegen target (BE-0293): the web twin of demos/showcase's
# `ui-test`. Where the string-only tests/test_codegen_playwright.py check the emitted source as text,
# this proves codegen's actual claim — that the emitted file is a runnable native test. It (1)
# re-generates the checked-in spec from the smoke scenario, (2) runs the fresh output with the REAL
# @playwright/test runner against a real Chromium — which transpiles the TypeScript and executes it,
# so it is a compile *and* a run, not a `tsc --noEmit` syntax check — then (3) fails if the output
# drifts from the committed fixture, so the emitter and the fixture never silently diverge. The drift
# check runs LAST so a legitimate emitter change still gets exercised (the run is the point); its
# failure just asks you to commit the regenerated spec. `webServer` in codegen/playwright.config.ts
# serves demos/web/app on the baked-in port, so no hand-started server here. Node/npm only (the
# @playwright/test runner is the destination framework, not our Python backend); no Simulator, no
# macOS — pure Linux like `e2e`.
codegen-e2e:
	cd $(ROOT) && uv run bajutsu codegen demos/web/scenarios/smoke.yaml --target web --emit playwright \
		--config $(CONFIG) -o $(CODEGEN)/smoke.spec.ts
	cd $(CODEGEN) && npm ci && npx playwright install --with-deps chromium && npx playwright test
	cd $(ROOT) && git diff --exit-code HEAD -- demos/web/codegen/smoke.spec.ts \
		|| { echo "generated spec drifted from the checked-in fixture — commit the regenerated demos/web/codegen/smoke.spec.ts" >&2; exit 1; }

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
		--goal "$(GOAL)" --out "$(OUT)" --no-erase --no-alert-handling

# Human-in-the-loop handoff demo (BE-0179): a HEADED record you drive alongside the AI. The agent
# opens device verification, recognizes it cannot know the out-of-band one-time code, and hands off
# (`ask_human`). Enter the code shown in the visible browser, click Verify, then answer the terminal
# prompt (`done`) to resume — the loop re-observes the verified screen and finishes. Needs
# ANTHROPIC_API_KEY. Runs in your terminal (a TTY), which is what makes the interactive prompt work.
HANDOFF_GOAL ?= Verify a device: open device verification and complete the one-time code step.
HANDOFF_OUT ?= $(ROOT)/tmp/web-record-handoff.generated.yaml
record-handoff: web-deps
	cd $(APP) && python3 -m http.server $(PORT) >/dev/null 2>&1 & srv=$$!; \
	trap 'kill $$srv 2>/dev/null' EXIT; \
	ready=0; for i in $$(seq 1 100); do \
	  if curl -sf --max-time 2 -o /dev/null "http://localhost:$(PORT)/"; then ready=1; break; fi; \
	  sleep 0.1; \
	done; \
	if [ "$$ready" -ne 1 ]; then echo "demo server did not answer on port $(PORT) within the readiness timeout" >&2; exit 1; fi; \
	mkdir -p $(ROOT)/tmp; \
	cd $(ROOT) && uv run bajutsu record --target web --backend web --config $(CONFIG) \
		--goal "$(HANDOFF_GOAL)" --out "$(HANDOFF_OUT)" --headed --no-erase --no-alert-handling

# The offline, API-key-free twin of `record`: the same record loop driven by a deterministic
# keyword agent against an in-memory FakeDriver (no browser, no model), so it's reproducible in the
# `make check` toolchain. Prints the goal, the generated scenario, and a deterministic replay.
record-offline:
	cd $(ROOT) && uv run python demos/web/record/record_offline.py

# The offline, key-free twin of `record-handoff`: the same handoff pause/resume driven by a scripted
# agent + responder against an in-memory app (no browser, no model, no human), so the mechanism is
# reproducible without a TTY or an API key. Prints the request, the response, and the scenario.
record-handoff-offline:
	cd $(ROOT) && uv run python demos/web/record/record_handoff_offline.py
