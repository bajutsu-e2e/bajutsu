#!/usr/bin/env bash
# Git merge driver for apm.lock.yaml (BE-0390, mirroring BE-0043's uv.lock driver).
#
# apm.lock.yaml is `apm install` output, not hand-written: a flat list of per-file SHA-256 hashes,
# where a three-way merge of two branches that touched the same skill leaves conflict markers no
# reader can resolve by hand, since the correct hash sits on neither side. So don't merge it —
# rerun `apm install` and hand git the fresh result.
#
# What this driver produces is provisional, exactly as the uv.lock driver's output is. Git runs
# every merge driver before it writes any merged file, so the `apm install` below reads the
# pre-merge working tree; when the merge also touches a skill source, this lockfile is stale the
# moment the merge finishes. `make lint-skills` closes that gap — it fails the gate until
# `make skills` refreshes the lockfile and the deployed .claude/skills/ tree together, the same way
# `uv lock --check` backs the uv.lock driver. So after resolving a skill conflict, run `make skills`
# and commit what it rewrote.
#
# Wired by `make hooks` / `make setup` as the `apm-lock` merge driver; `.gitattributes` maps
# `apm.lock.yaml` to it. Git invokes us with the path of the current/ours temp file (%A) that we
# must overwrite with the merged result. Exiting non-zero leaves apm.lock.yaml conflicted for
# manual resolution — the right outcome when we cannot produce a lockfile at all.
set -euo pipefail

merged="$1" # %A — git expects the resolved lockfile written here

if ! command -v apm >/dev/null 2>&1; then
	echo "merge-apm-lock: apm not installed — install apm-cli, then run 'make skills' and re-merge" >&2
	exit 1
fi

if ! apm install >/dev/null; then
	echo "merge-apm-lock: 'apm install' failed — fix the skill sources, then run 'make skills'" >&2
	exit 1
fi

cp apm.lock.yaml "$merged"
