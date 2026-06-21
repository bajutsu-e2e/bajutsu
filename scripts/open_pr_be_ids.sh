#!/usr/bin/env bash
# Print the BE IDs (4-digit, space-separated) that open pull requests have already allocated
# under roadmaps/, so the roadmap-id / roadmap-id-repair workflows never hand the same number to
# two branches in flight (the BE-0054 double-allocation). The caller folds the list into the
# allocator via ROADMAP_RESERVED_IDS; the allocator already floors on origin/main, so this only
# needs to cover IDs not yet merged.
#
# Pass the PR number to exclude (the one being allocated/repaired) as $1. Best effort: any gh/API
# hiccup prints nothing and exits 0, degrading to "no reservations". Needs `gh` authenticated and
# must run inside the repo checkout (gh resolves {owner}/{repo} from it).
#
# Usage: scripts/open_pr_be_ids.sh [EXCLUDE_PR_NUMBER]
set -uo pipefail

exclude="${1:-}"

ids="$(
	gh pr list --state open --limit 1000 --json number --jq '.[].number' 2>/dev/null \
		| while IFS= read -r pr; do
			[ "$pr" = "$exclude" ] && continue
			# Filenames carry the item dir, e.g. roadmaps/proposals/BE-0054-foo/BE-0054-foo.md.
			gh api "repos/{owner}/{repo}/pulls/$pr/files" --paginate --jq '.[].filename' 2>/dev/null || true
		done \
		| sed -n 's#.*/BE-\([0-9]\{4\}\)-.*#\1#p' | sort -u | paste -sd' ' -
)" || true

printf '%s\n' "$ids"
