#!/usr/bin/env sh
# Custom git merge driver for uv.lock (registered by `make hooks`; see .gitattributes).
#
# uv.lock is a fully-derived artifact of pyproject.toml, so a line-by-line 3-way merge yields
# noise and false conflicts. The deterministic resolution is to discard both sides and
# regenerate from the (already-merged) pyproject.toml with `uv lock`. git invokes this with the
# path to its temporary copy of our side ("%A"), which is also where it reads the result back.
set -eu

ours="$1"

if ! command -v uv >/dev/null 2>&1; then
	echo "uv-lock-merge: uv not on PATH; resolve uv.lock manually (run 'uv lock')" >&2
	exit 1
fi

# Regenerate against the working-tree pyproject.toml and hand the result back to git.
uv lock >&2
cp uv.lock "$ours"
