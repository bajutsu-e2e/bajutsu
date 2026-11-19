# AGENTS.md

This repository's agent instructions live in **[`CLAUDE.md`](CLAUDE.md)**. That file is the single
source of truth for Claude Code and for any other AI coding agent (Codex, Cursor, and so on).

Read [`CLAUDE.md`](CLAUDE.md) before starting. In short:

- **Find it before you read it.** `make repo-map ARGS="--docs"` and `make repo-map ARGS="--code"`
  print a map of the tree on every run, and `make roadmap-find ARGS="--grep <word>"` finds the
  roadmap items on a topic. For a large file, `make repo-map ARGS="--headings <path>"` gives each
  heading and its line span. Read that range, not the file.
- **Verify with `make check`** before calling a change done and before pushing. The command mirrors
  CI exactly, and [`CLAUDE.md`](CLAUDE.md) lists its full step list.
- **AI never judges a `run`.** Pass/fail is deterministic and comes from machine assertions
  alone. Never add an LLM call to the run/CI gate.
- **Work in parallel safely.** Use one topic per `claude/<topic>` branch, keep changes small, never
  push red (the pre-push hook enforces the gate), and rebase on `origin/main` before pushing.
  Outside Claude Code, create topic worktrees with `make worktree`. Claude Code keeps its
  `.claude/worktrees/` flow. Full guide: [`docs/ai-development.md`](docs/ai-development.md).
- **Split features into files to avoid conflicts.** When implementing a feature, prefer adding a
  new focused file over editing a shared monolith, and keep modules split by concern so concurrent
  work touches disjoint files. New CLI commands go in `bajutsu/cli/commands/<name>.py`, and new
  tests go in the matching `tests/<area>/` module. Adding a file conflicts far less than appending
  to a large shared one (BE-0043).
- **Ship the roadmap item where its code lands.** The PR that ships an item's code sets the item's
  `Status` to `Implemented` in that same PR. Full rule:
  [`docs/ai-development.md`](docs/ai-development.md#roadmap-items-be-ids-strict).
- **PR titles and bodies follow one shape.** Write a thorough body, never a one-line restatement of
  the title. Add the `[BE-NNNN]` prefix and a back-link when the PR implements a roadmap item.
  Bodies and titles stay in English. Full convention:
  [`docs/ai-development.md`](docs/ai-development.md#pull-requests-title-and-body).
- **Resolve AI PR reviews before stopping, and reply per comment with the grounds.** When you are
  unsure how to handle a comment, ask the human instead of guessing. Full guide:
  [`docs/ai-development.md`](docs/ai-development.md#responding-to-pr-review-comments).
- **Launch the web UI with `make serve`** (never `bajutsu serve` directly). The target installs the
  configured backend's deps on demand (for the web backend, Playwright's browser). Pass flags via
  `ARGS`.
- **Write docs as plain technical prose, in both languages.** Full rule:
  [`docs/ai-development.md`](docs/ai-development.md#documentation-style-every-document-both-languages).
  For Japanese prose, also apply the
  [`japanese-document-writing`](.apm/skills/japanese-document-writing/SKILL.md) skill.
