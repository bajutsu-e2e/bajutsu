# One entry point for every demo. Run `make -C demos` (or `make -C demos help`) for the menu.
#
#   make -C demos tour       # zero setup — the whole lifecycle on a fake device (Linux/CI/Mac)
#   make -C demos features   # zero setup — the scenario-feature showcase (FakeDriver)
#   make -C demos offline    # both of the above (everything that needs no Simulator)
#   make -C demos webui      # on-device — the Web UI tour (macOS + Simulator)
#   make -C demos record     # on-device — AI authoring -> run -> modify -> triage (macOS + Simulator)
#
# ROOT is the repo root so `uv` runs in the project and paths match each demo's config.
.PHONY: help tour features offline webui record

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)

help:
	@echo "Bajutsu demos — pick one (full map: demos/README.md):"
	@echo ""
	@echo "  Zero setup (no Simulator, runs anywhere):"
	@echo "    make tour       The whole story end to end — author, run, modify, diagnose."
	@echo "    make features   The scenario-feature showcase (tags, shared steps, data, secrets)."
	@echo "    make offline    Both of the above."
	@echo ""
	@echo "  On-device (macOS + Simulator; idb auto-installs):"
	@echo "    make webui      The Web UI tour — boot a Simulator, collect every evidence type."
	@echo "    make record     AI authoring with real Claude, then the modify + triage loop."

# --- Zero setup --------------------------------------------------------------
tour:
	cd $(ROOT) && uv run python demos/tour/tour.py

features:
	cd $(ROOT) && uv run python demos/features/run_demo.py

offline: tour features

# --- On-device ---------------------------------------------------------------
# Delegate to each demo's own launcher (its prereq checks and config live there).
webui:
	$(MAKE) -C $(ROOT)/demos/features serve

record:
	cd $(ROOT) && ./demos/record/demo.sh
