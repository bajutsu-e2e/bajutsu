#!/usr/bin/env bash
# Git merge driver for the generated roadmap index pages (BE-0043).
#
# Thin wrapper that hands the base/ours/theirs versions (%O %A %B) to the row-level merger,
# which three-way merges the generated tables keyed by BE id and writes the result back to
# ours (%A). See scripts/merge-roadmap-index.py for the why. Wired by `make hooks` / `make
# setup` as the `roadmap-index` merge driver; `.gitattributes` maps the index pages to it.
set -euo pipefail

if ! uv run --quiet python scripts/merge-roadmap-index.py "$@"; then
	echo "merge-roadmap-index: row merge failed — resolve roadmaps/ index by hand" >&2
	exit 1
fi
