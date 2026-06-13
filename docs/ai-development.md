**English** · [日本語](ja/ai-development.md)

# Developing with AI agents (and humans) in parallel

> How several sessions — humans and AI agents — work this repo at the same time without
> colliding or regressing each other. The short version lives in [`CLAUDE.md`](../CLAUDE.md);
> this page is the full operational guide.

The whole design rests on one property: **the deterministic gate is cheap, runs anywhere, and
mirrors CI exactly.** That is what lets work fan out safely — every branch is independently
verifiable, so "green locally" reliably predicts "green in CI", and the test suite is a
regression net that catches one session breaking another's feature.

## The gate

```bash
make check        # ruff check . + mypy bajutsu + pytest -q
```

Same three steps as [`.github/workflows/ci.yml`](../.github/workflows/ci.yml). The Python core
needs no Simulator, so it runs on Linux in seconds. Run it before you call a change done and
again before you push. On-device E2E (macOS + Simulator) is a separate, heavier path and is
**not** part of this gate.

## One topic per branch

- Branch off `main`: `claude/<short-topic>` for agents, `<user>/<topic>` for humans.
- Keep each branch small and single-purpose. Small diffs merge fast and rarely conflict.
- Don't open a PR unless the human asks; push your branch and let them open it.

## Never push red

The tracked **pre-push hook** runs `make check` and refuses the push if anything fails:

```bash
make setup   # uv sync --group dev + wire the git hooks (run once on a fresh clone)
```

`core.hooksPath` is a per-clone local setting that clone/pull never carry over, so an existing
clone won't have it — but you don't need to remember: `make check` (and `make hooks`) re-wires it
every time, so the gate self-heals right before you push. Claude Code web sessions also get it
automatically via [`.claude/hooks/session-start.sh`](../.claude/hooks/session-start.sh). In a real
emergency you can bypass with `git push --no-verify`, but the next CI run will still gate the PR.

When you change behavior, change a test with it — the suite is the contract that protects every
other session from your change.

## Rebase early, integrate small conflicts

```bash
git fetch origin
git rebase origin/main      # pull in others' merged work; resolve while conflicts are tiny
make check                  # re-verify after the rebase
```

Rebasing frequently means you meet other sessions' merged work early, when conflicts are a line
or two — not at the end as a tangled merge.

## Isolate concurrent sessions with worktrees

Two agents must never edit the same checkout. Give each session its own
[worktree](https://git-scm.com/docs/git-worktree) + branch, all sharing one `.git`:

```bash
# from the main checkout
git worktree add ../bajutsu-<topic> -b claude/<topic> origin/main
cd ../bajutsu-<topic>
make setup                   # uv sync --group dev + wire the hooks for this worktree
```

When the branch is merged (or abandoned), clean up:

```bash
git worktree remove ../bajutsu-<topic>
```

Generated and scratch output — `runs/`, `tmp/`, `.venv/`, build artifacts — is gitignored on
purpose; keep it out of commits so worktrees stay independent.

## Stay in your lane

Touch only the files your task needs. The architecture is layered (scenario → orchestrator →
driver → backend; see [architecture](architecture.md)), so most tasks live in one layer. If a
change must cut across many modules — e.g. altering the abstract **Driver API**, the scenario
**schema**, or a shared config shape — call it out up front so other sessions can steer clear of
that surface (or wait for it to land) instead of building on top of a moving target.

High-traffic shared surfaces to coordinate on:

| Surface | Files | Why it's shared |
|---|---|---|
| Driver API | [`bajutsu/drivers/base.py`](../bajutsu/drivers/base.py) | every backend + the orchestrator depend on it |
| Scenario schema | [`bajutsu/scenario.py`](../bajutsu/scenario.py) | the hub artifact; codegen/runner/report all read it |
| Config shape | [`bajutsu/config.py`](../bajutsu/config.py) | per-app layering every command resolves through |

## CI keeps the branches honest

CI runs the same gate on every PR and uses
`concurrency: ci-${{ github.ref }}` with `cancel-in-progress`, so re-pushes to the same branch
supersede stale runs instead of piling up. Two PRs that each pass independently can still
conflict in behavior — the merge is where they meet, which is exactly why the deterministic test
suite (not an LLM, not a human eyeball) is the arbiter. Keep the suite meaningful and your branch
rebased, and parallel work composes.
