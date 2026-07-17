# AGENTS.md

This repository's agent instructions live in **[`CLAUDE.md`](CLAUDE.md)** — the single
source of truth for both Claude Code and any other AI coding agent (Codex, Cursor, etc.).

Please read [`CLAUDE.md`](CLAUDE.md) before starting. In short:

- **Verify with `make check`** before calling a change done and before pushing — it mirrors CI
  exactly (the full step list is in [`CLAUDE.md`](CLAUDE.md)).
- **AI never judges a `run`.** Pass/fail is deterministic, from machine assertions only — never
  add an LLM call to the run/CI gate.
- **Work in parallel safely:** one topic per `claude/<topic>` branch, keep changes small, never
  push red (the pre-push hook enforces the gate), rebase on `origin/main` before pushing.
  Full guide: [`docs/ai-development.md`](docs/ai-development.md).
- **Split features into files to avoid conflicts.** When implementing a feature, prefer adding a
  new focused file over editing a shared monolith, and keep modules split by concern so concurrent
  work touches disjoint files. New CLI commands go in `bajutsu/cli/commands/<name>.py`, new tests in
  the matching `tests/<area>/` module — adding a file conflicts far less than appending to a large
  shared one (BE-0043).
- **Ship the roadmap item where its code lands** — the PR that ships an item's code sets its
  `Status` to `Implemented` in that same PR. Full rule:
  [`docs/ai-development.md`](docs/ai-development.md#roadmap-items-be-ids-strict).
- **PR titles and bodies follow one shape** — a thorough body, never a one-line restatement of the
  title, plus the `[BE-NNNN]` prefix and a back-link when the PR implements a roadmap item. Bodies
  and titles stay in English. Full convention:
  [`docs/ai-development.md`](docs/ai-development.md#pull-requests-title-and-body).
- **Resolve AI PR reviews before stopping, and reply per comment with the grounds** — when unsure
  how to handle a comment, ask the human instead of guessing. Full guide:
  [`docs/ai-development.md`](docs/ai-development.md#responding-to-pr-review-comments).
- **Launch the web UI with `make serve`** (never `bajutsu serve` directly) — it installs the idb
  backend's deps on demand; pass flags via `ARGS`.
- **Write docs as plain technical prose, both languages.** Full rule:
  [`docs/ai-development.md`](docs/ai-development.md#documentation-style-every-document-both-languages),
  and the [`japanese-document-writing`](.claude/skills/japanese-document-writing/) skill for Japanese
  prose.
