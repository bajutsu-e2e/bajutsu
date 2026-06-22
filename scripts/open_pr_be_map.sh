#!/usr/bin/env bash
# Print "<PR-number><TAB><BE-id>" lines: every 4-digit BE id each open PR introduces under roadmaps/.
# The roadmap-id-repair workflow uses this to apply the open-PR tiebreaker — when a number is
# contested only between open PRs (none merged), the lowest PR number keeps it — by passing, for the
# PR it is repairing, the ids held by *lower*-numbered PRs as ROADMAP_LOWER_PR_IDS.
#
# Best effort: any gh/API hiccup prints nothing and exits 0, degrading to "no map" (no tiebreaker,
# same as before this existed). Needs `gh` authenticated and must run inside the repo checkout.
#
# Usage: scripts/open_pr_be_map.sh
set -uo pipefail

gh pr list --state open --limit 1000 --json number --jq '.[].number' 2>/dev/null \
	| while IFS= read -r pr; do
		# Filenames carry the item dir, e.g. roadmaps/proposals/BE-0054-foo/BE-0054-foo.md.
		gh api "repos/{owner}/{repo}/pulls/$pr/files" --paginate --jq '.[].filename' 2>/dev/null \
			| sed -n 's#.*/BE-\([0-9]\{4\}\)-.*#\1#p' | sort -u \
			| while IFS= read -r id; do
				[ -n "$id" ] && printf '%s\t%s\n' "$pr" "$id"
			done
	done
