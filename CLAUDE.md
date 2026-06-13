# CLAUDE.md — working agreement for AI sessions

> The shared premise every session (human or agent) starts from. Read this first.
> Deeper rationale lives in [`DESIGN.md`](DESIGN.md) (ja) and [`docs/`](docs/README.md).

## What this is

**Bajutsu** (馬術) is a natural-language-driven **E2E testing tool for the iOS Simulator**.
A scenario (YAML) is the shared hub: AI helps *author* and *investigate*, a deterministic
runner decides pass/fail. Python logic core lives in [`bajutsu/`](bajutsu/); the Swift
test-support package is [`BajutsuKit/`](BajutsuKit/); runnable examples are in [`demos/`](demos/).

## Prime directives (do not violate)

1. **AI is the author and the failure investigator, never the judge.** `run` is fully
   deterministic — pass/fail comes only from machine-checkable assertions, never an LLM.
   Never introduce an LLM call into the Tier‑2 run/CI gate.
2. **Determinism first.** No fixed `sleep` (condition waits only); an ambiguous selector
   fails immediately rather than "tapping whatever matched first".
3. **App-agnostic.** Per-app differences live in config (`apps.<name>`); the tool, drivers,
   and runner stay unchanged across apps.

See README ["Core principles"](README.md#core-principles) for the full list.

## Verify your work (the gate)

The Python core needs no Simulator, so the gate is fast and runs anywhere (Linux included).
**Run this before you call a change done, and again before you push:**

```bash
make check        # = ruff check . + mypy bajutsu + pytest -q   (mirrors CI exactly)
```

Individual steps: `make lint` · `make typecheck` · `make test`. CI
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs the same three on every PR —
keeping the local bar identical is what makes "green locally" predict "green in CI".

On-device E2E (macOS + Simulator) is a separate, heavier path and is **not** part of this
gate: `make -C demos/features e2e` (requires `make deps` first). Don't block core work on it.

## Environment

- Python **3.13**, managed with **[uv](https://docs.astral.sh/uv/)**. `uv sync --group dev`
  installs everything the gate needs. In Claude Code web sessions this is done for you by
  [`.claude/hooks/session-start.sh`](.claude/hooks/session-start.sh).
- Secrets: only the AI paths (`record`, `run --dismiss-alerts`) need `ANTHROPIC_API_KEY`.
  Copy [`.env.example`](.env.example) → `.env` (gitignored). The deterministic gate needs none.
- `mypy` is **strict** and `ruff` is configured in [`pyproject.toml`](pyproject.toml) — match
  the existing style. Fullwidth/Japanese characters in strings are intentional (RUF001 is off).

## Working in parallel without breaking each other

Several people and agents work this repo at once. The rules below keep sessions from
colliding or regressing each other. Full guide: [`docs/ai-development.md`](docs/ai-development.md).

- **One topic per branch.** Branch off `main` as `claude/<short-topic>` (or `<user>/<topic>`).
  Keep changes small and focused — small PRs merge fast and conflict rarely.
- **The gate is the contract.** Never push red. The tracked pre-push hook
  ([`.githooks/pre-push`](.githooks/pre-push)) runs `make check` for you; enable it once with
  `git config core.hooksPath .githooks` (web sessions get this automatically). The deterministic
  test suite is the regression net — if you change behavior, a test should change with it.
- **Rebase, don't drift.** Before pushing, `git fetch origin && git rebase origin/main` so you
  integrate others' merged work early and surface conflicts while they're small.
- **Stay in your lane.** Touch only the files your task needs. If a change must cut across many
  modules (e.g. a driver-API change), say so up front so others can avoid that surface.
- **Isolate concurrent sessions with worktrees.** Run each session in its own
  `git worktree` + branch so two agents never edit the same checkout. See the guide for the
  one-liner. Generated/scratch output (`runs/`, `tmp/`, `.venv/`) is gitignored — keep it that way.
- **Don't create PRs unless asked.** Push to your branch; let the human open the PR.

## Conventions

- Comments explain **why**, not what; match the surrounding density and tone (the codebase
  favors short, purposeful comments). Don't add narration.
- Docs are **bilingual**: English in `docs/`, Japanese mirror in `docs/ja/`. Update both when
  you change a documented behavior.
- Commit messages: imperative, scoped (`feat(run): …`, `fix(record): …`, `docs: …`).
