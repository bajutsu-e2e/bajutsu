#!/usr/bin/env bash
# Self-heals the local git-secrets pattern registration.
#
# git-secrets (https://github.com/awslabs/git-secrets) stores its prohibited patterns in this
# clone's local `git config` (`secrets.patterns`), which — like `core.hooksPath` and the uv.lock
# merge driver — clone/pull never carries over. `make hooks` runs this on every clone/session so
# the patterns are always registered before the tracked pre-commit/prepare-commit-msg/commit-msg
# hooks (.githooks/) can catch anything with them. `git secrets --add`/`--register-aws` never
# duplicate an already-registered pattern, so re-running this is always safe — but `--add` (unlike
# `--register-aws`) exits non-zero on that already-registered no-op, so it's `|| true`-guarded
# below rather than left to `set -e`, which would otherwise fail every `make hooks` run after the
# first one that ever registers a pattern.
#
# Degrades gracefully when git-secrets isn't installed yet: prints how to get it and exits 0
# rather than blocking `make hooks` / `make check` — matching the way the commit-msg hook no-ops
# when `uv` is absent.
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v git-secrets >/dev/null 2>&1; then
	echo "hooks: git-secrets not installed — the pre-commit/CI secret scan will skip until it is" \
		"(macOS: 'brew install git-secrets' or 'make deps'; else clone" \
		"https://github.com/awslabs/git-secrets and run 'sudo make install')." >&2
	exit 0
fi

git secrets --register-aws >/dev/null

while IFS= read -r pattern; do
	[ -z "$pattern" ] && continue
	case "$pattern" in
	'#'*) continue ;;
	esac
	git secrets --add -- "$pattern" >/dev/null || true
done <.githooks/git-secrets-patterns.txt

echo "hooks: git-secrets patterns registered"
