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

Many parallel sessions and contributors work in this repository at once (see [ai-development.md](../../docs/ai-development.md)).
Pull requests conflict far more often than the work semantically overlaps.

This item proposes treating *a merge conflict as a design defect*.
It reshapes the file flow so that independent changes touch disjoint files, in three steps:

- Turn hand-edited shared ledgers into generated artifacts.
- Split monolith modules and test files, so that new work adds files instead of editing shared ones.
- Add the minimal git-side defenses that the repo currently lacks: `rerere` and a lockfile merge driver.

## Motivation

A scan of the last 200 commits shows that the conflict hot spots are structural, not accidental.
Nearly every PR edits the same few shared files.

| Kind | Files (change frequency) | Why it conflicts |
|---|---|---|
| Shared append-only ledger | `roadmaps/README.md` (12), `README-ja.md` (11) | Every roadmap PR appends a row to the **same** topic table; independent items collide textually |
| Monolith modules | `cli.py` (20), `orchestrator.py` (13), `runner.py` (12), `serve.py` (11), `scenario.py` (10) | Every feature edits adjacent lines of one file |
| Single-file test suites | `test_serve.py` (16), `test_scenario.py`, `test_orchestrator.py` | Multiple PRs append to the same test file |
| EN/JA mirroring + deps | `README.md` / `README.ja.md`, `docs/*` ↔ `docs/ja/*`, `uv.lock` (59) / `pyproject.toml` | One change always touches two files; the conflict surface doubles |

The repo also carries **no git-side defenses**:

- There is no `.gitattributes`, so no merge drivers exist.
- `rerere` is disabled and not wired into `make setup`.
- The history is full of `Merge branch 'main' into <branch>` commits.

Long-lived branches drift and integrate late, so conflicts surface large.

The BE-ID allocation race is already solved.
The `BE-0043` placeholder and `scripts/allocate_roadmap_ids.py` do the work, run by the [`roadmap-id`](../../.github/workflows/roadmap-id.yml) workflow.
The **index tables that carry those IDs are still hand-edited**, so they remain the single largest conflict source.
This proposal closes that gap and generalizes the lesson.

## Detailed design

Four mechanisms follow, in order of leverage.

1. **Turn shared ledgers into generated artifacts.**
   Each `BE-NNNN/*.md` file already carries the metadata that an index row needs (`Status`, `Track`, `Topic`).
   A `scripts/build_roadmap_index.py` would read those metadata blocks and regenerate the tables in `README.md` / `README-ja.md`.
   `make check` (and CI) would verify that the committed index is up to date, and would fail on drift.

   A roadmap PR then touches only its own directory, so the index never conflicts.
   If a generated file ever does conflict, "regenerate and overwrite" resolves it mechanically, and `rerere` replays the resolution.

   The same fragment pattern (towncrier-style `changes/<id>.md`) removes the classic single-`CHANGELOG` conflict, if a changelog is later introduced.

2. **Break up monoliths so that new work adds files.**
   CLI commands are already independent `@app.command()` functions.
   Move them to `bajutsu/commands/<name>.py`, and register the Typer sub-apps by directory discovery.
   A new command is then a **new file**.

   Split single-file test suites into `tests/<area>/test_<feature>.py`.

   Deeper splits of `orchestrator.py` and `runner.py` are out of scope here.
   The CLI and the tests are the high-value, low-risk wins.

3. **Add the minimal git-side defenses.**
   - Add a `.gitattributes` entry, plus a `make setup` step that registers a custom merge driver.
     The driver **regenerates `uv.lock` (`uv lock`) on conflict**, instead of merging the file line by line.
   - Enable `rerere` (`git config rerere.enabled true`) in `make setup`, so that a once-resolved conflict replays automatically.
   - Restrict `merge=union` to genuinely append-only, line-independent generated lists, because misuse breaks semantics.

4. **Process and flow.**
   Keep PRs small and short-lived, and rebase rather than merge.
   CLAUDE.md already says to rebase, but the history shows merge commits, so make squash or rebase the policy.

   Optionally, enable a GitHub **merge queue**.
   The queue tests PRs against the post-merge state, serially, and so stops drift-induced conflicts at the door.

Mechanisms 1 and 3 alone cover most of the measured top conflict sources at low cost.
Those sources are the roadmap index, the lockfile, and re-resolution.
Mechanisms 2 and 4 are reinforcement.

## Alternatives considered

- **`merge=union` on the index tables.**
  This is the cheapest option.
  But union concatenates both sides' lines, which produces duplicate or misordered rows and silently broken tables.
  That is wrong for a structured, sorted index, and generation is the correct fix.
- **Drop the committed index entirely (build-only artifact).**
  This loses GitHub's rendered, browseable roadmap.
  Generating *and committing* the index (with a CI freshness check) keeps the browseable file.
  It also removes the hand-edit conflict.
- **Do nothing and rely on manual rebasing.**
  This is the status quo.
  The history shows that it does not scale to the number of concurrent sessions.

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
