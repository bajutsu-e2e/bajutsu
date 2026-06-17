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
make check        # lock-check + format-check + lint + lint-sh + lint-actions
                  #   + typecheck + test (coverage floor)   — mirrors CI exactly
```

Individual steps: `make format-check` · `make lint` · `make lint-sh` · `make lint-actions`
· `make lock-check` · `make typecheck` · `make test`. (`make format` rewrites; the gate only
checks.) Every step is uv-native and runs on a fresh clone — except `actionlint`, a standalone
binary CI installs and `make` skips with a notice when it's absent. CI
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs the same steps on every PR —
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
- **Always launch the web UI with `make serve`** — never `bajutsu serve` / `python -m bajutsu
  serve` directly. `make serve` ([`scripts/serve.sh`](scripts/serve.sh)) installs the idb
  backend's deps on demand (the idb client + `idb_companion`), which a bare `serve` skips —
  leaving runs to fail with `no available actuator`. Pass flags through `ARGS`, e.g.
  `make serve ARGS="--config demos/features/demo.config.yaml --port 8766"` (the demo config is
  needed for the sample app, since the repo has no root `bajutsu.config.yaml`).

## Working in parallel without breaking each other

Several people and agents work this repo at once. The rules below keep sessions from
colliding or regressing each other. Full guide: [`docs/ai-development.md`](docs/ai-development.md).

- **One topic per branch.** Branch off `main` as `claude/<short-topic>` (or `<user>/<topic>`).
  Keep changes small and focused — small PRs merge fast and conflict rarely.
- **The gate is the contract.** Never push red. The tracked pre-push hook
  ([`.githooks/pre-push`](.githooks/pre-push)) runs `make check` for you. `core.hooksPath` is a
  per-clone local setting that clone/pull never carry over, so `make check` (and `make hooks`)
  re-wires it on every run — the gate self-heals, no manual `git config` needed. Run `make setup`
  once on a fresh clone; web sessions get it automatically. The deterministic test suite is the
  regression net — if you change behavior, a test should change with it.
- **Git defenses are wired the same way (BE-0043).** `make hooks` also self-heals two local git
  settings that ease parallel work: a `uv.lock` merge driver that **regenerates the lockfile from
  `pyproject.toml` on conflict** (via [`scripts/merge-uv-lock.sh`](scripts/merge-uv-lock.sh) +
  [`.gitattributes`](.gitattributes)) instead of line-merging resolver output, and `rerere` so a
  once-resolved conflict replays automatically. No manual `git config` needed.
- **Rebase, don't drift.** Before pushing, `git fetch origin && git rebase origin/main` so you
  integrate others' merged work early and surface conflicts while they're small.
- **Stay in your lane.** Touch only the files your task needs. If a change must cut across many
  modules (e.g. a driver-API change), say so up front so others can avoid that surface.
- **Isolate concurrent sessions with worktrees.** Run each session in its own
  `git worktree` + branch so two agents never edit the same checkout. Always `git fetch origin`
  first so the worktree branches off the latest `origin/main`, never a stale ref. See the guide
  for the one-liner. Generated/scratch output (`runs/`, `tmp/`, `.venv/`) is gitignored — keep it that way.
- **Don't create PRs unless asked.** Push to your branch; let the human open the PR.

## Conventions

- Comments explain **why**, not what; match the surrounding density and tone (the codebase
  favors short, purposeful comments). Don't add narration.
- Docs are **bilingual**: English in `docs/`, Japanese mirror in `docs/ja/`. Update both when
  you change a documented behavior.
- **Documentation style (both languages, every doc and every update).** Write natural prose —
  natural Japanese in `docs/ja/`, natural English in `docs/` — and report the same way. **No coined
  terms:** use established, widely-used technical terms and ordinary words. **No forced
  translation:** use the conventional translation; if rendering a term would read unnaturally, keep
  the original (usually English) term (e.g. `selector`, `actuator`, `backend`). **No omissions:**
  each document must be self-contained — spell out abbreviations on first use and give the context
  a reader needs, without assuming they read another page first. Full guidance:
  [`docs/ai-development.md`](docs/ai-development.md).
- **Roadmap items use BE IDs (strict).** Every roadmap item is a directory
  `docs/roadmap/BE-NNNN-<slug>/` holding the English file `BE-NNNN-<slug>.md` and its Japanese
  version `BE-NNNN-<slug>-ja.md` — `BE` = *Bajutsu Evolution*, `NNNN` a zero-padded 4-digit
  monotonically increasing ID. When you add one: allocate the next ID
  (`ls -d docs/roadmap/BE-*/ | sort | tail -1`, then +1; never reuse, skip, or guess), create
  **both** language files in that directory, and add a row to **both** index pages
  (`docs/roadmap/README.md` and `docs/roadmap/README-ja.md`).
  Each file uses the **Swift-Evolution proposal format** (metadata block + Introduction /
  Motivation / Detailed design / Alternatives considered / References); its `Status` files it
  under **Accepted** (`Implemented` / `Accepted, in progress`) or **Proposals** (`Proposal` /
  `Proposal (deferred)`). **IDs are permanent — never renumber an existing item.** Full rule:
  [`docs/roadmap/README.md`](docs/roadmap/README.md) · [`docs/ai-development.md`](docs/ai-development.md).
- Commit messages: imperative, scoped (`feat(run): …`, `fix(record): …`, `docs: …`).
- **PR titles and bodies are always in English**, regardless of the language used in the
  session. This keeps the project history readable for every contributor.
- **Prefix the PR title with the roadmap ID** when the PR is tied to a roadmap item: start the
  title with the ID in brackets, e.g. `[BE-0017] feat(mcp): add MCP server`. PRs with no roadmap
  item keep the plain scoped title.
