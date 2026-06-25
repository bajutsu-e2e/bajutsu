#!/usr/bin/env bash
# Run-it-early version of the pre-push routine: sync, rebase onto origin/main, run the gate,
# then print the "definition of done" reminder.
#
# The pre-push hook already GATES `make check`; this is the advisory, human-initiated version
# you run before you think you are done (BE-0069 C). It deliberately is NOT a hook and does not
# replace the gate — it surfaces conflicts and gate failures early, while they are small.
#
# Usage: scripts/preflight.sh   (Makefile: make preflight)
set -euo pipefail

cd "$(dirname "$0")/.."

# Integrate others' merged work early, then re-verify on top of it.
git fetch origin
git rebase origin/main
make check

cat >&2 <<'EOF'

preflight: gate green. Before you call it done, confirm the definition of done:
  - Did a documented behavior change touch BOTH language docs (docs/ + docs/ja/)?
  - Did a behavior change carry a test that changes with it?
  - If this PR ships a roadmap item, is its Status flipped (and the directory/index moved)?
EOF
