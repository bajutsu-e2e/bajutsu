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
git fetch origin            # always sync main first — branch off the latest, not a stale ref
git worktree add ../bajutsu-<topic> -b claude/<topic> origin/main
cd ../bajutsu-<topic>
make setup                   # uv sync --group dev + wire the hooks for this worktree
```

The `git fetch origin` is not optional: `origin/main` is a local tracking ref that only
advances when you fetch, so skipping it branches the new worktree off whatever main looked like
last time — re-introducing conflicts that other sessions already merged away. Fetch, then branch
off the fresh `origin/main`.

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

## Roadmap items: BE IDs (strict)

The roadmap is **one directory per item** under [`roadmap/`](roadmap/README.md). Each item lives in
`docs/roadmap/BE-NNNN-<slug>/`, which holds the English file `BE-NNNN-<slug>.md` and its Japanese
version `BE-NNNN-<slug>-ja.md` (same ID and slug). **BE** stands for *Bajutsu Evolution* and `NNNN`
is a **zero-padded, 4-digit, monotonically increasing** ID.

When you add a roadmap item:

1. **Allocate the next ID** = the highest existing `BE-NNNN` + 1. Find the current max with:
   ```bash
   ls -d docs/roadmap/BE-*/ | sort | tail -1
   ```
   Never reuse, skip, or guess a number.
2. **Create the item directory and both language files** — `docs/roadmap/BE-NNNN-<slug>/BE-NNNN-<slug>.md`
   (English) and `docs/roadmap/BE-NNNN-<slug>/BE-NNNN-<slug>-ja.md` (Japanese, same ID & slug) — and add
   a row for it to the matching topic table in **both** index pages
   ([en](roadmap/README.md), [ja](roadmap/README-ja.md)).
3. **IDs are permanent.** Never renumber an existing item — not when its status changes, not when
   it is completed, not when it is removed from a table. A BE ID, once assigned, refers to that
   item forever.

Each file follows the **Swift-Evolution proposal format** — a metadata block (`* Proposal`,
`* Status`, `* Track`, `* Topic`, optional `* Origin`) followed by `## Introduction` /
`## Motivation` / `## Detailed design` / `## Alternatives considered` / `## References`. Fill what
you can and mark unknowns `TBD`. The **Status** field decides the management track and the index
section the item appears in:

| Status | Track |
|---|---|
| `Implemented` · `Accepted, in progress` | **Accepted** — a decision & implementation record |
| `Proposal` · `Proposal (deferred)` | **Proposals** — under consideration |

As an item advances, **update its Status** (and move its row to the right index group) rather than
renaming the file. Milestones M1–M4 are `BE-0001`–`BE-0004` (accepted & implemented).

This is a hard rule agents must follow; the short form is in [`CLAUDE.md`](../CLAUDE.md).

## Documentation style (every document, both languages)

These rules apply to all documentation — English under `docs/` and the Japanese mirror under
`docs/ja/` — and to every future update, not just new files. Agents must follow them, and they
apply equally when reporting on or summarizing work.

- **Write natural prose.** A Japanese document must read as natural Japanese; an English document
  must read as natural English. A mirror conveys the same content naturally in its own language —
  it is not a word-for-word transliteration of the other.
- **No coined terms.** Use established, widely-used technical terms and ordinary words. Do not
  invent vocabulary, and do not stretch a word into a meaning it does not normally carry.
- **No forced or unnatural translation.** Use the conventional translation of a term. When
  translating it would read unnaturally, keep the original term instead — usually the English word
  (e.g. `selector`, `actuator`, `backend`, `assertion`) rather than a contrived literal rendering.
- **No omissions; be self-contained.** A reader must be able to understand the document on its own.
  Spell out an abbreviation the first time it appears, give a term the context it needs, and do not
  assume the reader has already read another page.

The short form of these rules is in [`CLAUDE.md`](../CLAUDE.md).
