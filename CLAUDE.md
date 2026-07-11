# CLAUDE.md — working agreement for AI sessions

> The shared premise every session (human or agent) starts from. Read this first.
> Deeper rationale lives in [`DESIGN.md`](DESIGN.md) (ja) and [`docs/`](docs/README.md);
> human contributors start from [`CONTRIBUTING.md`](CONTRIBUTING.md) (ja: [`CONTRIBUTING.ja.md`](CONTRIBUTING.ja.md)).

## What this is

**Bajutsu** (馬術) is a natural-language-driven **E2E testing tool** built on a backend-agnostic
driver: a **platform is a backend** behind one interface, so the deterministic core is unchanged
across targets — the **iOS Simulator** (idb) today, a **web (Playwright)** backend landed,
**Android** planned.
A scenario (YAML) is the shared hub: AI helps *author* and *investigate*, a deterministic
runner decides pass/fail. Python logic core lives in [`bajutsu/`](bajutsu/); the Swift
test-support package is [`BajutsuKit/`](BajutsuKit/); runnable examples are in [`demos/`](demos/).

## Prime directives (do not violate)

1. **AI is the author and the failure investigator, never the judge.** `run` is fully
   deterministic — pass/fail comes only from machine-checkable assertions, never an LLM.
   Never introduce an LLM call into the Tier‑2 run/CI gate.
2. **Determinism first.** No fixed `sleep` (condition waits only); an ambiguous selector
   fails immediately rather than "tapping whatever matched first".
3. **App-agnostic.** Per-app differences live in config (`targets.<name>`); the tool, drivers,
   and runner stay unchanged across targets.

See README ["Core principles"](README.md#core-principles) for the full list.

## Verify your work (the gate)

The Python core needs no Simulator, so the gate is fast and runs anywhere (Linux included).
**Run this before you call a change done, and again before you push:**

```bash
make check        # format-check + lint + lint-docstrings + lint-imports + lint-sh
                  #   + lint-actions + lint-js + lint-roadmap + lock-check + typecheck
                  #   + test (coverage floor)   — mirrors CI exactly
```

Individual steps: `make format-check` · `make lint` · `make lint-docstrings` · `make lint-imports`
· `make lint-sh` · `make lint-actions` · `make lint-js` · `make lint-roadmap` · `make lock-check`
· `make typecheck` · `make test`. (`make format` rewrites; the gate only
checks.) Every step is uv-native and runs on a fresh clone — except `actionlint`, a standalone
binary CI installs and `make` skips with a notice when it's absent. CI
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs the same steps on every PR —
keeping the local bar identical is what makes "green locally" predict "green in CI".

On-device E2E (macOS + Simulator) is a separate, heavier path and is **not** part of this
gate: `make -C demos/showcase run-swiftui` (requires `make deps` first). Don't block core work on it.

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
  `make serve ARGS="--config demos/showcase/showcase.config.yaml --port 8766"` (the showcase config
  is needed for the showcase app, since the repo has no root `bajutsu.config.yaml`).

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
  integrate others' merged work early and surface conflicts while they're small. `make preflight`
  (BE-0069) does this and runs the gate, then prints the "definition of done" reminder — the
  advisory, run-it-early version of the pre-push gate.
- **Stay in your lane.** Touch only the files your task needs. If a change must cut across many
  modules (e.g. a driver-API change), say so up front so others can avoid that surface.
- **Isolate concurrent sessions with worktrees.** Run each session in its own
  `git worktree` + branch so two agents never edit the same checkout. `make worktree TOPIC=<topic>`
  (BE-0069) does it — fetches `origin/main` first (so the worktree never branches off a stale ref),
  creates `../bajutsu-<topic>` on `claude/<topic>` (override with `PREFIX=<user>`), and runs
  `make setup` in it. Generated/scratch output (`runs/`, `tmp/`, `.venv/`) is gitignored — keep it that way.
- **Right-size the model and reasoning effort (BE-0103).** Match a session's model/effort to the
  task: heavy work (implementing, refactors, design) runs on a capable model at high effort; light
  chores (index regen, link fixes, mechanical renames) downshift. The in-repo skills carry a default
  `model:` in their frontmatter, so the economical choice is automatic and still overridable. The
  task→capability matrix and the phase/subagent guidance live in
  [`docs/ai-development.md`](docs/ai-development.md#right-sizing-the-model-and-reasoning-effort-be-0103).
- **Who opens the PR depends on the work (BE-0230).** Two paths:
  - **BE-creation work** — a proposal PR from [`ideation`](.claude/skills/ideation/SKILL.md) or the
    proposal phase of [`propose-and-build`](.claude/skills/propose-and-build/SKILL.md): **don't
    auto-create it.** Push to your branch and let the human open the PR. A proposal is a human
    checkpoint, and its BE id is allocated only when a human merges it (BE-0089) — auto-opening
    would erode that checkpoint.
  - **Implementation work** — [`implement-be`](.claude/skills/implement-be/SKILL.md), whose output
    is always a self-contained, gate-green change: **auto-open the Draft PR after the gate, then run
    a paced `/loop`** that drives the mechanical tail (CI fixes, review replies) to quiet-and-green,
    delegating each iteration's `pr-followup` work to a fresh subagent so the heavy implement
    transcript stays out of it. The loop escalates to the human on a design-change comment or a
    merge conflict, and never marks the PR ready itself. See `implement-be` steps 10–12.
  - For any other request, the default is still: push and let the human open the PR unless they ask.
- **PRs created by Claude Code always start as Draft.** When asked to open a PR, create it with
  `gh pr create --draft`, then keep pushing fixes until `make check` and CI are both green before
  marking it ready for review (`gh pr ready`). Never mark a Claude-Code-created PR ready while any
  check is red.
- **Exception — documentation-only PRs open Ready for review.** A PR whose changes are purely
  documentation/prose (`docs/`, roadmap `*.md`/`*-ja.md` prose, `CLAUDE.md`/`CONTRIBUTING`, and
  other prose-only changes) is opened **Ready for review, not Draft**, with the `steering-committee`
  team assigned as reviewer: `gh pr create --reviewer bajutsu-e2e/steering-committee …` (omit
  `--draft`). Everything else — anything touching product code — still starts as Draft per the rule
  above.

## Conventions

- Comments explain **why**, not what; match the surrounding density and tone (the codebase
  favors short, purposeful comments). Don't add narration.
- **Docstrings (BE-0065).** The public API surface (`Driver` + shared types, CLI, MCP tools,
  scenario schema, public functions of runner / `assertions` / `network`) uses **Google-style**
  docstrings — a one-line summary then `Args:` / `Returns:` / `Raises:` *only where they add
  information*; internal `_helpers` keep one prose line of *why*. **Never restate types**; describe
  meaning. English, like all code. Migrate module by module in small PRs. Full rule:
  [`docs/ai-development.md`](docs/ai-development.md).
- **Always follow the [`japanese-tech-writing`](.claude/skills/japanese-tech-writing/) skill
  whenever you generate Japanese — without exception.** This is not limited to `docs/ja/` and
  roadmap `*-ja.md`: it covers *any* Japanese you produce, including freshly written prose,
  translations from English, and revisions/rewrites of existing Japanese. The skill is the
  authoritative style for Japanese prose in this project; invoke it before writing or editing the
  Japanese, not after.
- Docs are **bilingual**: English in `docs/`, Japanese mirror in `docs/ja/`. Update both when
  you change a documented behavior.
- **Keep DESIGN.md and `docs/architecture.md` in step with behavior (BE-0113).** A PR that changes
  behavior described by [`DESIGN.md`](DESIGN.md) or [`docs/architecture.md`](docs/architecture.md)
  must update the affected document in the same change. This stays a review-time norm, not a CI
  gate: checking that a paragraph of prose still matches the code needs semantic judgment, which
  would put an LLM on the `run` / CI verdict path (prime directive 1) — so it holds the same way as
  the bilingual-docs rule above.
- **Documentation style (both languages, every doc and every update).** Write natural prose. **No
  coined terms** (use established technical/ordinary words); **no forced translation** (keep the
  original term — `selector`, `actuator`, `backend` — when a translation reads unnaturally); **no
  omissions** (each document self-contained; spell out an acronym in full on first use with the
  acronym in parentheses, e.g. role-based access control (RBAC), then the acronym alone). Japanese
  docs — `docs/ja/` and every roadmap `*-ja.md` — are written in **敬体 (ですます調)**, never 常体,
  under the [`japanese-tech-writing`](.claude/skills/japanese-tech-writing/) skill (above). Full
  guidance: [`docs/ai-development.md`](docs/ai-development.md).
- **Roadmap items use BE IDs (strict).** Every item is a directory `roadmaps/BE-NNNN-<slug>/`
  holding **both** language files `BE-NNNN-<slug>.md` and `BE-NNNN-<slug>-ja.md` (`BE` = *Bajutsu
  Evolution*, `NNNN` a zero-padded monotonic ID). The path is fixed when the ID is allocated and
  **never moves**; `Status` (`Implemented` / `In progress` / `Proposal` / `Proposal (deferred)`)
  decides only the index bucket. Name new items with the `BE-XXXX` placeholder — the number is
  allocated **on `main` after merge** (BE-0089). Each file uses the **Swift-Evolution format** with a
  `<!-- BE-METADATA -->` `| Field | Value |` block; **`Detailed design` enumerates the work MECE**
  and **`Progress` is a living checklist + PR-linked log** kept current as work proceeds (BE-0100).
  Never hand-edit the index tables — run `make roadmap-index` (`make test` fails on drift).
  `tests/test_roadmap_format.py` checks the file shape (BE-0074). **IDs are permanent — never
  renumber.** Full rule (metadata fields, both-way PR links, author handle, reciprocal
  `Related`/`Superseded by`): [`roadmaps/README.md`](roadmaps/README.md) ·
  [`docs/ai-development.md`](docs/ai-development.md).
- Commit messages: imperative, scoped (`feat(run): …`, `fix(record): …`, `docs: …`).
- **PR titles and bodies are always in English**, regardless of the session language, so the
  history stays readable for every contributor.
- **Prefix the PR title with `[BE-NNNN]`** when the PR *implements an already-numbered* item (e.g.
  `[BE-0017] feat(mcp): add MCP server`). A PR with no item — and a **BE-creation PR** (id allocated
  on `main` after merge, BE-0089) — keeps the plain scoped title. **CI enforces this**
  ([`pr-title.yml`](.github/workflows/pr-title.yml)): a branch encoding a roadmap id
  (`claude/be-0050-<slug>`) must carry the matching `[BE-0050]` prefix.
- **Write a thorough PR body — never a one-line restatement.** Lead with `## Summary`, follow the
  tracked template [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md) (fill what
  applies, trim the rest — its `Prime-directive compliance` / `Verification` blocks are the canonical
  wording), and close with the `make check` verification. Full rule:
  [`docs/ai-development.md`](docs/ai-development.md#pull-requests-title-and-body).
- **Always link a PR and its BE item, both ways.** The PR carries the `[BE-NNNN]` prefix and
  references the item; the item records every delivering PR under its `Implementing PR` row (`実装
  PR`, both languages) — including `In progress` items, updated in the same change the PR lands.
