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
- **Ship the roadmap item where its code lands.** A roadmap item lives under `roadmaps/proposals/`
  while it is only a plan; when the **same PR also ships the implementation**, create it directly
  under `roadmaps/implemented/` with `Status: Implemented` instead of filing it as a proposal.
- **Prefix the PR title with the roadmap ID.** When a PR is tied to a roadmap item, begin its title
  with the ID in brackets, e.g. `[BE-0017] feat(mcp): add MCP server`. PRs with no roadmap item keep
  the plain scoped title (`feat(...)`, `fix(...)`, `docs: …`). PR titles stay in English.
- **Write a thorough PR body, never a one-line restatement of the title.** A reviewer should grasp
  the change from the body without reconstructing it from the diff: *what* changed and *why* (the
  motivation/context), a short summary of the key changes (grouped by area when the diff is large),
  how you verified it (e.g. `make check`), and the relevant links (roadmap item, issue) and
  call-outs (trade-offs, follow-ups, things to look at closely). This expectation applies to humans and AI
  alike. Bodies stay in English.
- **Resolve AI PR reviews before stopping, and reply per comment with the grounds.** When an AI
  reviewer (Copilot and the like) leaves comments on a pull request, keep working until every
  comment is resolved, then **reply to each comment individually** — never a single summary reply.
  Each reply states that the comment is addressed *and* the grounds for it: the concrete change
  that resolves it (cite the commit or file/line), or, when you make no change, the specific reason
  it does not apply. A bare "done" or 👍 does not satisfy this. When you are unsure how a comment
  should be handled, ask the human instead of guessing. The same is expected of human contributors
  (see [`CONTRIBUTING.md`](CONTRIBUTING.md)) — it is not an AI-only rule. Full guide:
  [`docs/ai-development.md`](docs/ai-development.md#responding-to-pr-review-comments).
- **Launch the web UI with `make serve`** (never `bajutsu serve` directly) — it installs the idb
  backend's deps on demand; pass flags via `ARGS`.
- **Write docs as plain technical prose.** State facts and reasons directly, in the register of
  technical documentation. No literary, story-like, or metaphorical writing, and no
  colloquial filler. Convert rhetorical questions into statements and avoid dramatic punctuation
  or emphasis used for effect. Japanese documentation uses です・ます調. These rules apply to English
  and Japanese alike, in every doc and every update.
