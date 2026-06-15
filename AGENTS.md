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
- **Launch the web UI with `make serve`** (never `bajutsu serve` directly) — it installs the idb
  backend's deps on demand; pass flags via `ARGS`.
- **Write docs as plain technical prose.** State facts and reasons directly, in the register of
  good technical documentation. No literary, story-like, or metaphorical writing, and no
  colloquial filler. Convert rhetorical questions into statements and avoid dramatic punctuation
  or emphasis used for effect. Japanese documentation uses です・ます調. This applies to English
  and Japanese alike, in every doc and every update.
