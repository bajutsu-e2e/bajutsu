# One entry point for every demo. Run `make` (or `make help`) from here for the menu.
#
#   make tour       on-device, no API key — run -> modify -> diagnose on a real Simulator
#   make features   on-device, no API key — the scenario-feature showcase (tags/shared/secrets)
#   make offline    both of the above (the deterministic, AI-free demos)
#   make webui      on-device — the Web UI tour (every evidence type)
#   make record     on-device — AI authoring with real Claude, then modify + triage
#
# "tour"/"features"/"offline" run the dedicated demo app (demos/app/) on a booted Simulator
# via idb. They use no LLM and no API key — `run` and `triage` are fully deterministic — but
# they do need a Mac + Simulator. The zero-setup version that needs neither is the fake-driver
# script `uv run python demos/tour/tour.py` (see demos/tour/README.md).
#
# ROOT is the repo root so `uv` runs in the project and paths match demos/demo.config.yaml.
.PHONY: help tour features offline webui record app-gen app-build

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)
APP := $(ROOT)/demos/app
CONFIG := $(ROOT)/demos/demo.config.yaml

help:
	@echo "Bajutsu demos — pick one (full map: demos/README.md):"
	@echo ""
	@echo "  On-device, deterministic (no LLM / API key; needs macOS + Simulator, idb auto-installs):"
	@echo "    make tour       Run -> modify -> diagnose on a real Simulator (the headline story)."
	@echo "    make features   The scenario-feature showcase (tags, shared steps, secrets)."
	@echo "    make offline    Both of the above."
	@echo ""
	@echo "  On-device, AI-assisted:"
	@echo "    make webui      The Web UI tour — boot a Simulator, collect every evidence type."
	@echo "    make record     AI authoring with real Claude, then the modify + triage loop."
	@echo ""
	@echo "  No Mac? The whole story on a fake device: uv run python demos/tour/tour.py"

# Build the dedicated demo app (xcodegen -> xcodebuild for the iOS Simulator). `bajutsu run`
# / `serve` also build it on demand via the config's `build` command, so this is rarely
# needed directly.
app-gen:
	cd $(APP) && xcodegen generate

app-build: app-gen
	cd $(APP) && xcodebuild -project BajutsuDemo.xcodeproj -scheme BajutsuDemo \
		-destination 'generic/platform=iOS Simulator' -derivedDataPath build/dd build

# --- On-device, deterministic (no AI / API key) ------------------------------
tour:
	cd $(ROOT) && ./demos/tour/demo.sh

# Run the smoke-tagged feature scenarios (excluding the `slow` one). PASSWORD seeds the
# secret variable in features.yaml — any non-empty value works for the demo login.
features: app-build
	cd $(ROOT) && PASSWORD="$${PASSWORD:-s3cr3t}" uv run bajutsu run \
		--scenario demos/app/scenarios/features.yaml --target demo --config $(CONFIG) \
		--backend ios --no-network --tag smoke --exclude slow

offline: tour features

# --- On-device, AI-assisted --------------------------------------------------
# Delegate to each demo's own launcher (its prereq checks and config live there).
webui:
	$(MAKE) -C $(ROOT)/demos/features serve

record:
	cd $(ROOT) && ./demos/record/demo.sh
