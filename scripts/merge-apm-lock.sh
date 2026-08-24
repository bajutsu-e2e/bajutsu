#!/usr/bin/env bash
# Git merge driver for apm.lock.yaml (BE-0390, mirroring BE-0043's uv.lock driver).
#
# apm.lock.yaml is `apm install` output, not hand-written. Its `generated_at` line is rewritten by
# every install, so two branches that each touched a skill conflict on line 2 alone; and its
# content_hash entries are resolver output, where a line-by-line three-way merge can produce a
# lockfile matching neither side's sources — which `make lint-skills` would then fail on a branch
# whose sources are individually fine. So don't merge it: regenerate it from the (already-merged)
# .apm/skills/ tree and hand git the fresh result. The same `apm install` refreshes the deployed
# .claude/skills/ tree, so lockfile and deployment stay consistent; stage what it rewrote.
#
# Wired by `make hooks` / `make setup` as the `apm-lock` merge driver; `.gitattributes` maps
# `apm.lock.yaml` to it. Git invokes us with the path of the current/ours temp file (%A) that we
# must overwrite with the merged result. Every refusal below exits non-zero so git leaves
# apm.lock.yaml conflicted for manual resolution rather than recording a lockfile we don't trust.
set -euo pipefail

merged="$1" # %A — git expects the resolved lockfile written here

if ! command -v apm >/dev/null 2>&1; then
	echo "merge-apm-lock: apm not installed — install apm-cli, then 'make skills' and re-merge" >&2
	exit 1
fi

# A conflicted source skill would deploy and hash its own conflict markers: SKILL.md is markdown,
# so `apm install` parses it happily and cannot fail the way `uv lock` fails on a broken
# pyproject.toml. Scan the working tree rather than the index — merge drivers run per file in no
# defined order, so a source conflict may not be recorded yet when we run.
if grep -rqE '^<{7} ' .apm/skills 2>/dev/null; then
	echo "merge-apm-lock: .apm/skills/ has unresolved conflicts — resolve them, run 'make skills', then re-merge" >&2
	exit 1
fi

if ! apm install >/dev/null; then
	echo "merge-apm-lock: 'apm install' failed — fix the skill sources, then re-merge" >&2
	exit 1
fi

cp apm.lock.yaml "$merged"
