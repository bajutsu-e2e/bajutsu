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
- **Don't create PRs unless asked.** Push to your branch; let the human open the PR.

## Conventions

- Comments explain **why**, not what; match the surrounding density and tone (the codebase
  favors short, purposeful comments). Don't add narration.
- **Docstrings (BE-0065).** The public API surface (`Driver` + shared types, CLI, MCP tools,
  scenario schema, the public functions of runner / `assertions` / `network`) uses **Google-style**
  docstrings — a one-line summary then `Args:` / `Returns:` / `Raises:` *only where they add
  information*; internal `_helpers` keep one prose line of *why*, and `TypedDict` / constant classes
  keep their per-field inline comments. **Never restate types** (they live in the annotations);
  describe meaning. English, like all code. The generated reference is `make docs` (out of the gate).
  Migrate to the structured form module by module in small PRs — don't rewrite a module's docstrings
  as a side effect. Full rule: [`docs/ai-development.md`](docs/ai-development.md).
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
- **Documentation style (both languages, every doc and every update).** Write natural prose —
  natural Japanese in `docs/ja/`, natural English in `docs/` — and report the same way. **No coined
  terms:** use established, widely-used technical terms and ordinary words. **No forced
  translation:** use the conventional translation; if rendering a term would read unnaturally, keep
  the original (usually English) term (e.g. `selector`, `actuator`, `backend`). **No omissions:**
  each document must be self-contained — spell out abbreviations on first use and give the context
  a reader needs, without assuming they read another page first. **The first time an acronym
  appears, spell it out in full with the acronym in parentheses right after** (e.g. role-based
  access control (RBAC)) — after that, the acronym alone is fine.
  **When generating the Japanese side — writing it fresh, or translating the English `docs/` into
  `docs/ja/` (and roadmap `*-ja.md`) — follow the [`japanese-tech-writing`](.claude/skills/japanese-tech-writing/)
  skill: it is the authoritative style for Japanese prose here, and a translation must read as
  natural Japanese under those norms, not a literal rendering of the English.** Every Japanese
  document — `docs/ja/` and every roadmap `*-ja.md` — is written in **敬体 (the polite *desu/masu*
  style, ですます調)**, never the plain *da/dearu* style (常体). Full guidance:
  [`docs/ai-development.md`](docs/ai-development.md).
- **Roadmap items use BE IDs (strict).** Every roadmap item is a directory
  `roadmaps/<category>/BE-NNNN-<slug>/` holding the English file `BE-NNNN-<slug>.md`
  and its Japanese version `BE-NNNN-<slug>-ja.md` — `BE` = *Bajutsu Evolution*, `NNNN` a
  zero-padded 4-digit monotonically increasing ID. Each item lives under one of **four** folders,
  one per `Status` value (BE-0078): `roadmaps/implemented/` (`Implemented`),
  `roadmaps/in-progress/` (`In progress`), `roadmaps/proposals/` (`Proposal`),
  `roadmaps/deferred/` (`Proposal (deferred)`). When you add
  one: name it with the `BE-XXXX` placeholder — the norm, since the number is allocated **on `main`
  after the PR merges** (contiguous in merge order; BE-0089) — or allocate the next ID by hand (`ls -d
  roadmaps/{implemented,in-progress,proposals,deferred}/BE-*/ | sort | tail -1`, then +1; never
  reuse, skip, or guess) when you want it fixed up front. Create **both** language files in a new
  directory under `roadmaps/proposals/` for a proposal, or under `roadmaps/implemented/` with `Status:
  Implemented` when the **same PR ships the implementation** (a new item is a proposal first
  *unless* its code lands with it). Don't hand-edit the index
  tables — run `make roadmap-index` to regenerate the tables in **both** index pages
  (`roadmaps/README.md` and `roadmaps/README-ja.md`) from each item's metadata;
  `make test` fails if the committed index drifts.
  Each file uses the **Swift-Evolution proposal format** (metadata block + Introduction /
  Motivation / Detailed design / Alternatives considered / Progress / References), with the metadata
  as a fenced `| Field | Value |` table — `<!-- BE-METADATA -->` … `<!-- /BE-METADATA -->`, opening
  with a `| Field | Value |` header row (`| 項目 | 値 |` on the Japanese side) and holding
  `Proposal` / `Author` / `Status` / `Topic` (plus `Implementing PR` once shipped, the optional
  cross-item links `Related` / `Superseded by`, and `Origin` last, when applicable); the Japanese
  mirror uses `提案` / `提案者` / `状態` / `トピック` / `関連` / `無効化` / `由来`. **`Detailed
  design` enumerates the work MECE** (mutually exclusive, collectively exhaustive), and **`Progress`
  is a living section kept current as work proceeds** (BE-0100): a checklist mirroring that breakdown
  (one `- [ ]` box per unit, ticked as it lands) plus a short chronological PR-linked log. Every PR
  that advances an item ticks its boxes and adds a log entry in the same change, exactly as it fills
  `Implementing PR`. `Related` / `Superseded by` are reciprocal: the superseding item lists the other
  under `Related`, the superseded one names its successor under `Superseded by`. The metadata block
  must name the author by GitHub handle — `| Author |
  [@handle](https://github.com/handle) |`, the account of whoever first authored the item (for an
  AI-assisted draft, the person who drove and committed it). `tests/test_roadmap_format.py` checks
  this shape (BE-0074). `Status` is the single source of truth for both an item's folder and its
  index bucket — one of `Implemented` / `In progress` / `Proposal` / `Proposal (deferred)`. When an
  item's status changes (it starts being built, or it ships), set its `Status`; CI
  (`roadmap-promote`) then **moves its directory** to the matching folder and regenerates the index —
  or run `make roadmap-promote` locally to do it yourself. `make test`
  fails if any item's directory doesn't match its `Status`. **IDs are permanent — never renumber an
  existing item.** Full rule:
  [`roadmaps/README.md`](roadmaps/README.md) · [`docs/ai-development.md`](docs/ai-development.md).
- Commit messages: imperative, scoped (`feat(run): …`, `fix(record): …`, `docs: …`).
- **PR titles and bodies are always in English**, regardless of the language used in the
  session. This keeps the project history readable for every contributor.
- **Write a thorough PR body — never a one-line restatement of the title.** A reviewer should
  understand the change from the body without reconstructing it from the diff: explain *what*
  changed and *why* (the motivation/context), give a short summary of the key changes (grouped by
  area when the diff is large), say how you verified it (e.g. `make check`), and include the
  relevant links (roadmap item, issue) and call-outs (trade-offs, follow-ups, anything a reviewer
  should look at closely). This expectation holds for humans and AI alike. Concretely: lead with
  `## Summary`, close with the `make check` verification (the green numbers), and add `What changed`
  / `Prime-directive compliance` / `Scope` / `Notes` as the change warrants — depth proportional to
  the diff. **When you (AI) draft a PR, follow the tracked body template
  [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md)** — fill the sections that
  apply and delete the rest; the recurring `Prime-directive compliance` and `Verification` blocks it
  ships pre-filled are the canonical wording, so trim them rather than re-inventing the phrasing.
  Full title-and-body rule:
  [`docs/ai-development.md`](docs/ai-development.md#pull-requests-title-and-body).
- **Prefix the PR title with the roadmap ID** when the PR *implements an already-numbered* item:
  start the title with the ID in brackets, e.g. `[BE-0017] feat(mcp): add MCP server`. A PR with no
  roadmap item — and a **BE-creation PR**, whose id is allocated on `main` only after the merge
  (BE-0089) — keeps the plain scoped title with no prefix. **CI enforces this**
  ([`pr-title.yml`](.github/workflows/pr-title.yml) runs `scripts/lint_pr.py --title-only` on every
  PR): the title must be a scoped conventional subject, and when the branch name encodes a roadmap id
  (`claude/be-0050-<slug>`) the title must carry the matching `[BE-0050]` prefix — a missing or
  mismatched id fails the check.
- **Always link a PR and its BE item, both ways.** Any PR that advances a roadmap item must name
  that item, and any roadmap item that has been worked on must name the PRs behind it — the link is
  never one-directional. Concretely: (1) the PR carries the `[BE-NNNN]` title prefix (above) and
  references the item in its body; (2) the BE item records every PR that delivered code under its
  `Implementing PR` row (`実装 PR` on the Japanese side) — a single `[#NNN](…)` or a
  comma-separated list when several PRs landed, kept in both language files. This holds for
  `In progress` items too, not just shipped ones: when a PR lands, add it to the item's
  `Implementing PR` row in the same change. A roadmap item is `Implemented` or `In progress` only
  if its PRs are discoverable from the item, and a roadmap-tied PR is traceable to its item.
