# AGENTS.md

This repository's agent instructions live in **[`CLAUDE.md`](CLAUDE.md)** — the single
source of truth for both Claude Code and any other AI coding agent (Codex, Cursor, etc.).

Please read [`CLAUDE.md`](CLAUDE.md) before starting. In short:

- **Verify with `make check`** (ruff + mypy + pytest) before calling a change done and before
  pushing. It mirrors CI exactly.
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
- **Link the PR back to the roadmap.** When a PR implements a roadmap item (`roadmaps/**/BE-NNNN-*`),
  always add a link to that PR in the item's markdown (both the English and Japanese files) so the
  roadmap entry and its implementing PR are cross-referenced.
- **Prefix the PR title with the roadmap ID.** When a PR is tied to a roadmap item, begin its title
  with the ID in brackets, e.g. `[BE-0017] feat(mcp): add MCP server`. PRs with no roadmap item keep
  the plain scoped title (`feat(...)`, `fix(...)`, `docs: …`). PR titles stay in English.
- **Resolve AI PR reviews before stopping.** When an AI reviewer (Copilot and the like) leaves
  comments on a pull request, keep working until every comment is resolved. Reply to each PR
  comment once you have addressed it, stating what you changed or why no change was needed. When
  you are unsure how a comment should be handled, ask the human instead of guessing.
- **Launch the web UI with `make serve`** (never `bajutsu serve` directly) — it installs the idb
  backend's deps on demand; pass flags via `ARGS`.
- **Write docs as plain technical prose.** State facts and reasons directly, in the register of
  good technical documentation. No literary, story-like, or metaphorical writing, and no
  colloquial filler. Convert rhetorical questions into statements and avoid dramatic punctuation
  or emphasis used for effect. Japanese documentation uses です・ます調. This applies to English
  and Japanese alike, in every doc and every update.
