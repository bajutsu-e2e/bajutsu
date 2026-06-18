#!/usr/bin/env bash
# Git merge driver for uv.lock (BE-0043).
#
# uv.lock is a resolver output, not hand-written, so a line-by-line three-way merge produces
# noise and spurious conflicts on nearly every branch. Instead of merging it, regenerate it:
# resolve the (already-merged) pyproject.toml with `uv lock` and hand git the fresh result.
#
# Wired by `make hooks` / `make setup` as the `uv-lock` merge driver; `.gitattributes` maps
# `uv.lock` to it. Git invokes us with the path of the current/ours temp file (%A) that we must
# overwrite with the merged result. If pyproject.toml itself is conflicted, `uv lock` fails and
# we exit non-zero so git leaves uv.lock conflicted for manual resolution (fix pyproject first).
set -euo pipefail

merged="$1" # %A — git expects the resolved lockfile written here

if ! uv lock --quiet; then
	echo "merge-uv-lock: 'uv lock' failed — resolve pyproject.toml conflicts first, then retry" >&2
	exit 1
fi

cp uv.lock "$merged"
