#!/usr/bin/env bash
# Manage the refs/be-claims/* ledger that reserves BE ids across open PRs *atomically*, closing the
# same-window allocation race the ROADMAP_RESERVED_IDS env list only narrows. Each claim is a git ref
# named refs/be-claims/<NNNN>; GitHub's create-ref API is a compare-and-set (HTTP 422 if the ref
# already exists), so two branches allocating at once cannot both take a number — the loser re-picks.
#
# Usage:
#   scripts/be_claims.sh list                 # print claimed ids (4-digit, space-separated)
#   scripts/be_claims.sh claim <NNNN> <SHA>   # atomically create the claim; exit 0 won, 1 lost/taken
#   scripts/be_claims.sh release <NNNN>       # delete the claim (PR closed, or id now on main)
#
# Best effort on read/release (any hiccup is non-fatal); `claim` reports the race outcome via its
# exit code. Needs `gh` authenticated and must run inside the repo checkout (gh resolves the repo,
# and `git ls-remote` the claims, from there).
set -uo pipefail

cmd="${1:-}"

case "$cmd" in
	list)
		# refs/be-claims/0056 -> 0056. ls-remote degrades to empty output (and so to "no claims")
		# if the remote is unreachable, matching the allocator's other best-effort lookups.
		git ls-remote origin 'refs/be-claims/*' 2>/dev/null \
			| sed -n 's#.*refs/be-claims/\([0-9]\{4\}\)$#\1#p' | sort -u | paste -sd' ' -
		;;
	claim)
		id="${2:?claim needs a 4-digit id}"
		sha="${3:?claim needs a commit sha to point the ref at}"
		# 201 Created -> we won; 422 (ref exists) or any error -> we lost the race / cannot claim.
		if gh api -X POST "repos/{owner}/{repo}/git/refs" \
			-f "ref=refs/be-claims/$id" -f "sha=$sha" >/dev/null 2>&1; then
			echo "claimed BE-$id"
		else
			echo "::notice::BE-$id is already claimed by another PR; re-picking the next free id."
			exit 1
		fi
		;;
	release)
		id="${2:?release needs a 4-digit id}"
		# DELETE /git/refs/{ref} takes the ref *without* the leading refs/. Idempotent: a missing
		# claim (already released, or never created) is fine, so failure is swallowed.
		gh api -X DELETE "repos/{owner}/{repo}/git/refs/be-claims/$id" >/dev/null 2>&1 \
			&& echo "released BE-$id" || true
		;;
	*)
		echo "usage: $0 {list | claim <NNNN> <SHA> | release <NNNN>}" >&2
		exit 2
		;;
esac
