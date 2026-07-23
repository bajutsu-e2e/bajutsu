**English** · [日本語](BE-0043-conflict-resistant-file-flow-ja.md)

# BE-0043 — Conflict-resistant file flow (generated indexes, modular files, git hygiene)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0043](BE-0043-conflict-resistant-file-flow.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0043") |
| Implementing PR | [#66](https://github.com/bajutsu-e2e/bajutsu/pull/66), [#69](https://github.com/bajutsu-e2e/bajutsu/pull/69), [#73](https://github.com/bajutsu-e2e/bajutsu/pull/73) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

Many parallel sessions and contributors work this repository at once (see
[ai-development.md](../../docs/ai-development.md)), and pull requests conflict far more often than the
work is actually semantically overlapping. This item proposes treating *merge conflicts as a
design smell* and reshaping the file flow so that independent changes touch disjoint files: turn
hand-edited shared ledgers into generated artifacts, split monolith modules and test files so new
work adds files rather than editing shared ones, and add the minimal git-side defenses
(`rerere`, a lockfile merge driver) that the repo currently lacks.

## Motivation

A scan of the last 200 commits shows the conflict hot spots are structural, not accidental — the
same few shared files are edited by nearly every PR:

| Kind | Files (change frequency) | Why it conflicts |
|---|---|---|
| Shared append-only ledger | `roadmaps/README.md` (12), `README-ja.md` (11) | Every roadmap PR appends a row to the **same** topic table; independent items collide textually |
| Monolith modules | `cli.py` (20), `orchestrator.py` (13), `runner.py` (12), `serve.py` (11), `scenario.py` (10) | Every feature edits adjacent lines of one file |
| Single-file test suites | `test_serve.py` (16), `test_scenario.py`, `test_orchestrator.py` | Multiple PRs append to the same test file |
| EN/JA mirroring + deps | `README.md` / `README.ja.md`, `docs/*` ↔ `docs/ja/*`, `uv.lock` (59) / `pyproject.toml` | One change always touches two files; the conflict surface doubles |

The repo also carries **no git-side defenses**: no `.gitattributes` (no merge drivers), `rerere`
disabled (not wired into `make setup`), and the history is full of `Merge branch 'main' into
<branch>` commits — long-lived branches drift and integrate late, so conflicts surface large.

The BE-ID allocation race is already solved (the `BE-0043` placeholder + `scripts/allocate_roadmap_ids.py`
run by the [`roadmap-id`](../../.github/workflows/roadmap-id.yml) workflow), but the **index
tables that carry those IDs are still hand-edited**, so they remain the single largest conflict
source. This proposal closes that gap and generalizes the lesson.

## Detailed design

Four mechanisms, in order of leverage:

1. **Turn shared ledgers into generated artifacts.** Each `BE-NNNN/*.md` file already carries the
   metadata an index row needs (`Status`, `Track`, `Topic`). A `scripts/build_roadmap_index.py`
   would read those metadata blocks and regenerate the tables in `README.md` / `README-ja.md`;
   `make check` (and CI) would verify the committed index is up to date and fail on drift. A
   roadmap PR then touches only its own directory, so the index never conflicts — and if a
   generated file ever does conflict, "regenerate and overwrite" resolves it mechanically (and
   `rerere` replays it). The same fragment pattern (towncrier-style `changes/<id>.md`) removes the
   classic single-`CHANGELOG` conflict if a changelog is later introduced.

2. **Break up monoliths so new work adds files.** CLI commands are already independent
   `@app.command()` functions; move them to `bajutsu/commands/<name>.py` and register the Typer
   sub-apps by directory discovery, so a new command is a **new file**. Split single-file test
   suites into `tests/<area>/test_<feature>.py`. Deeper splits of `orchestrator.py` / `runner.py`
   are out of scope here — the CLI and tests are the high-value, low-risk wins.

3. **Add the minimal git-side defenses.** A `.gitattributes` entry plus a `make setup` step that
   registers a custom merge driver which **regenerates `uv.lock` (`uv lock`) on conflict** instead
   of merging it line by line; enable `rerere` (`git config rerere.enabled true`) in `make setup`
   so a once-resolved conflict auto-replays; restrict `merge=union` to genuinely append-only,
   line-independent generated lists only (misuse breaks semantics).

4. **Process / flow.** Keep PRs small and short-lived and rebase rather than merge (CLAUDE.md
   already says rebase, but the history shows merge commits — make squash/rebase the policy);
   optionally enable a GitHub **merge queue** so PRs are tested against the post-merge state
   serially, stopping drift-induced conflicts at the door.

Mechanisms 1 and 3 alone cover most of the measured top conflict sources (roadmap index + lockfile
+ re-resolution) at low cost; 2 and 4 are reinforcement.

## Alternatives considered

- **`merge=union` on the index tables.** Cheapest, but union concatenates both sides' lines,
  producing duplicate/misordered rows and silently broken tables — wrong for a structured,
  sorted index. Generation is the correct fix.
- **Drop the committed index entirely (build-only artifact).** Loses GitHub's rendered, browseable
  roadmap. Generating *and committing* (with a CI freshness check) keeps the browseable file while
  removing the hand-edit conflict.
- **Do nothing / rely on manual rebasing.** The status quo; the history shows it does not scale to
  the number of concurrent sessions.

## Progress

- [x] Shipped — see the *Implementing PR* above.
- **Historical note:** mechanism 1's generated `README.md` / `README-ja.md` index tables and their
  `merge=roadmap-index` git merge driver were retired by
  [#1257](https://github.com/bajutsu-e2e/bajutsu/pull/1257): the roadmap dashboard now covers what
  those tables did, so there is no longer a shared generated file there to conflict on, and the
  merge driver was removed rather than left wired to nothing. Mechanisms 2–4 (file splits, the
  `uv.lock` merge driver, `rerere`, small PRs) are unaffected.

## References

- [ai-development.md](../../docs/ai-development.md) — parallel-work guide (worktrees, rebase, lanes)
- [`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py) — the existing
  ID-race fix this generalizes
- [CLAUDE.md](../../CLAUDE.md) — "Working in parallel without breaking each other"
