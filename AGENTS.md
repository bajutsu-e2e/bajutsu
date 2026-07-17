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
- **Link the PR back to the roadmap, both ways.** When a PR implements a roadmap item, prefix its
  title with the ID (`[BE-0017] feat(mcp): add MCP server`) and add the PR link to the item's
  markdown (both language files); a PR with no roadmap item keeps the plain scoped title
  (`feat(...)`, `fix(...)`, `docs: …`). PR titles stay in English.
- **Ship the roadmap item where its code lands.** Every item lives in one flat
  `roadmaps/BE-NNNN-<slug>/` directory (BE-0159 retired the per-`Status` folders; `Status` now
  decides only the index bucket). The PR that ships the code sets `Status: Implemented` in that
  same PR. Full rule: [`docs/ai-development.md`](docs/ai-development.md#roadmap-items-be-ids-strict).
- **Write a thorough PR body, never a one-line restatement of the title** — a reviewer should grasp
  the change from the body without reconstructing it from the diff. Bodies stay in English; the
  template and section-by-section guide are in
  [`docs/ai-development.md`](docs/ai-development.md#pull-requests-title-and-body).
- **Resolve AI PR reviews before stopping, and reply per comment with the grounds** — never a
  single summary reply, never a bare "done" or 👍; when unsure how to handle a comment, ask the
  human instead of guessing. Full guide:
  [`docs/ai-development.md`](docs/ai-development.md#responding-to-pr-review-comments).
- **Launch the web UI with `make serve`** (never `bajutsu serve` directly) — it installs the idb
  backend's deps on demand; pass flags via `ARGS`.
- **Write docs as plain technical prose, both languages.** Follow the documentation-style norms and
  the [`japanese-document-writing`](.claude/skills/japanese-document-writing/) skill for any
  Japanese (敬体, no coined terms). Full rule:
  [`docs/ai-development.md`](docs/ai-development.md#documentation-style-every-document-both-languages).
