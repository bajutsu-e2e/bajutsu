# One entry point for every demo. Run `make` (or `make help`) from here for the menu.
#
#   make tour       on-device, no API key — run -> modify -> diagnose on a real Simulator
#   make features   on-device, no API key — the scenario-feature showcase (tags/shared/secrets)
#   make offline    both of the above (the deterministic, AI-free demos)
#   make webui      on-device — the Web UI tour (every evidence type)
#   make record     on-device — AI authoring with real Claude, then modify + triage
#
# Every demo drives the single iOS fixture, the showcase suite (demos/showcase/) — the SwiftUI
# accessibility product — on a booted Simulator via XCUITest. "tour"/"features"/"offline" use no LLM
# and no API key (`run` and `triage` are fully deterministic) but need a Mac + Simulator; the
# zero-setup version that needs neither is the fake-driver script `uv run python demos/tour/tour.py`
# (see demos/tour/README.md).
#
# ROOT is the repo root so `uv` runs in the project and paths match demos/demo.config.yaml.
.PHONY: help tour features offline webui record app-build

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)
CONFIG := $(ROOT)/demos/demo.config.yaml

help:
	@echo "Bajutsu demos — pick one (full map: demos/README.md):"
	@echo ""
	@echo "  On-device, deterministic (no LLM / API key; needs macOS + Xcode + Simulator):"
	@echo "    make tour       Run -> modify -> diagnose on a real Simulator (the headline story)."
	@echo "    make features   The scenario-feature showcase (tags, shared steps, secrets)."
	@echo "    make offline    Both of the above."
	@echo ""
	@echo "  On-device, AI-assisted:"
	@echo "    make webui      The Web UI tour — boot a Simulator, collect every evidence type."
	@echo "    make record     AI authoring with real Claude, then the modify + triage loop."
	@echo ""
	@echo "  No Mac? The whole story on a fake device: uv run python demos/tour/tour.py"

# Build the showcase SwiftUI app (xcodegen -> xcodebuild for the iOS Simulator). `bajutsu run`
# / `serve` also build it on demand via the config's `build` command, so this is rarely needed
# directly.
app-build:
	$(MAKE) -C $(ROOT)/demos/showcase swiftui-build

# --- On-device, deterministic (no AI / API key) ------------------------------
tour:
	cd $(ROOT) && ./demos/tour/demo.sh

# Run the smoke-tagged feature scenarios (excluding the `slow` one). PASSWORD seeds the secret
# variable in features.yaml — any non-empty value works for the demo; its literal is masked in
# every run artifact. The menu's tour/features scenarios live in the non-globbed scenarios/menu/
# subdir, so they never run as part of a whole-directory `bajutsu run --target showcase-swiftui`.
features: app-build
	cd $(ROOT) && PASSWORD="$${PASSWORD:-s3cr3t}" uv run bajutsu run \
		--scenario demos/showcase/scenarios/menu/features.yaml --target showcase-swiftui --config $(CONFIG) \
		--backend ios --no-network --tag smoke --exclude slow

offline: tour features

# --- On-device, AI-assisted --------------------------------------------------
# Launch the Web UI bound to the showcase config — the guided tour in demos/showcase/WEBUI.md.
# Wraps the repo-root `make serve` (installs the configured backend's deps on demand). The Web UI builds
# + installs the app itself when its binary is missing. Override the port via PORT=8766.
PORT ?= 8765
webui:
	cd $(ROOT) && $(MAKE) serve ARGS="--config demos/showcase/showcase.config.yaml --port $(PORT)"

# `record` already targets the showcase (the no-a11y app, the stability-ladder A/B); delegate to
# its own launcher, where the goal and prereq checks live.
record:
	$(MAKE) -C $(ROOT)/demos/showcase record
