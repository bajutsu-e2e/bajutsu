# CLAUDE.md — working agreement for AI sessions

> The shared premise every session (human or agent) starts from. Read this first.
> Deeper rationale lives in [`DESIGN.md`](DESIGN.md) (ja) and [`docs/`](docs/README.md);
> human contributors start from [`CONTRIBUTING.md`](CONTRIBUTING.md) (ja: [`CONTRIBUTING.ja.md`](CONTRIBUTING.ja.md)).

## What this is

**Bajutsu** (馬術) is a natural-language-driven **E2E testing tool** built on a backend-agnostic
driver: a **platform is a backend** behind one interface, so the deterministic core is unchanged
across targets — the **iOS Simulator** (XCUITest), a **web (Playwright)** backend, and an
**Android (adb)** backend are all landed; Flutter is planned next.
A scenario (YAML) is the shared hub: AI helps *author* and *investigate*; a deterministic
runner decides pass/fail. Python logic core lives in [`bajutsu/`](bajutsu/); the Swift
test-support package is [`BajutsuKit/`](BajutsuKit/); runnable examples are in [`demos/`](demos/).

## Find it before you read it

Three commands print a map of the tree, derived on every run — no committed index to go stale:

- `make repo-map ARGS="--docs"` — every `docs/` page, with its length and its own one-line
  description. `make repo-map ARGS="--code"` — every `bajutsu/` package and top-level module. Add
  `--grep <word>` to either. What each module is *for* is prose, in
  [`docs/architecture.md`](docs/architecture.md#module-list-and-roles).
- `make roadmap-find ARGS="--grep <word>"` — the roadmap items on a topic, out of ~380.

**Load a large file in stages.** `make repo-map ARGS="--headings <path>"` prints each heading and
its span. Read that range, not the file. CLAUDE.md links a dozen times into
[`docs/ai-development.md`](docs/ai-development.md), where every anchor lands in a section a small
fraction of the page long. Reading a file whole is a last resort, once you can say why the staged
path would not answer. In `roadmaps/`, the `-ja.md` mirror holds nothing the English file lacks — read the `.md`
alone.

**See a class's full method surface without opening the file.** `make repo-map ARGS="--methods
<path>"` walks one file or a whole package and lists every class (with its bases), every method
(signature + docstring), and every top-level function — one level deeper than `--code`, which names
only a module's top-level declarations. `--grep <word>` narrows it the same way.

**Don't read a file back after editing it.** `Edit` and `Write` fail loudly when a change does not
apply. A Read afterwards confirms nothing and pays for the file twice. Every tool result is re-sent
on each later turn, so one needless read keeps costing.

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
                  #   + lint-actions + lint-js + lint-roadmap + lint-skills + lint-module-map
                  #   + lint-secrets
                  #   + lock-check + typecheck + test (total coverage floor)
                  #   + lint-coverage-floors (per-file floors)   — mirrors CI exactly
```

Individual steps: `make format-check` · `make lint` · `make lint-docstrings` · `make lint-imports`
· `make lint-sh` · `make lint-actions` · `make lint-js` · `make lint-roadmap` · `make lint-skills`
· `make lint-module-map` · `make lint-secrets` · `make lock-check` · `make typecheck` · `make test`
· `make lint-coverage-floors`.
(`make format` rewrites; the gate only checks — and so does `lint-coverage-floors`: `make
coverage-floors` is the deliberate step that rewrites `coverage-floors.json` once coverage has
risen, [BE-0385](roadmaps/BE-0385-coverage-floor-continuous-ratchet/BE-0385-coverage-floor-continuous-ratchet.md).)
Every step is uv-native and runs on a fresh clone —
except `actionlint` and `gitleaks`, two tools that CI installs separately but `make` skips (with a
notice) when they are absent. CI
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs the same steps on every PR —
keeping the local bar identical is what makes "green locally" predict "green in CI".

**Coverage ratchets both ways it can (BE-0385).** The total floor is `fail_under` in
`[tool.coverage.report]` ([`pyproject.toml`](pyproject.toml)) — not a `Makefile` flag — and
`make lint-pr` reminds you to raise it once measured coverage drifts more than two points above it.
The per-file floors live in [`coverage-floors.json`](coverage-floors.json); `make check` fails when
a file drops below its own recorded number and never blocks a rise. Raise the snapshot with `make
coverage-floors` and commit it; that same command is the deliberate escape hatch for a drop you have
decided to accept. Full guide:
[`docs/ai-development.md`](docs/ai-development.md#the-coverage-ratchet-be-0385).

On-device E2E (macOS + Simulator) is a separate, heavier path and is **not** part of this
gate: `make -C demos/showcase run-swiftui` (requires `make deps` first). Don't block core work on it.

## Environment

- Python **3.13**, managed with **[uv](https://docs.astral.sh/uv/)**. `uv sync --group dev`
  installs everything the gate needs. In Claude Code web sessions this is done for you by
  [`.claude/hooks/session-start.sh`](.claude/hooks/session-start.sh).
- Secrets: only the AI paths (`record`, `crawl`, `triage --ai`) need `ANTHROPIC_API_KEY`.
  Copy [`.env.example`](.env.example) → `.env` (gitignored). The deterministic gate needs none.
- `mypy` is **strict** and `ruff` is configured in [`pyproject.toml`](pyproject.toml) — match
  the existing style. Fullwidth/Japanese characters in strings are intentional (RUF001 is off).
- **Always launch the web UI with `make serve`** — never `bajutsu serve` / `python -m bajutsu
  serve` directly. `make serve` ([`scripts/serve.sh`](scripts/serve.sh)) installs the configured
  backend's deps on demand (for the web backend, Playwright's browser), which a bare `serve` skips —
  leaving runs to fail with `no available actuator`. Pass flags through `ARGS`, e.g.
  `make serve ARGS="--config demos/showcase/showcase.config.yaml --port 8766"` (the showcase config
  is needed for the showcase app, since the repo has no root `bajutsu.config.yaml`).

## Agent skill layout

One skill is **one source directory**, managed with the Agent Package Manager (APM) since
[BE-0390](roadmaps/BE-0390-apm-skill-management/BE-0390-apm-skill-management.md):

- `.apm/skills/<name>/SKILL.md` is the skill — its frontmatter (`name`, `description`, `model`) and
  its whole procedure, including the Claude Code tools, slash commands, and plugins it uses. There
  is no separate adapter and no second copy to keep in step.
- `.apm/skills/<name>/references/` holds depth the body would otherwise carry: a step's long
  procedure, a norm set's rules. `SKILL.md` stays within APM's budget — roughly 500 lines and
  5,000 tokens — and points at the reference file *at the step that needs it*, so a session loads
  only what it uses.
- `.claude/skills/<name>/` is the **deployed** tree, written by `apm install` and committed, so a
  fresh clone has a working skill set before anyone installs APM. Never edit it by hand.
- `apm.yml` names the package and pins `targets: [claude]`; `apm.lock.yaml` records a SHA-256 per
  deployed file.

Edit the source, then run `make skills` and commit both sides. `make lint-skills` (part of
`make check`) runs `apm audit --ci`, which fails when a deployed file no longer matches its source —
whether it was hand-edited or its `make skills` was forgotten. It audits a scratch mirror of the
files git sees ([`scripts/audit_skills.py`](scripts/audit_skills.py)), because APM governs
`.claude/` whole and would otherwise scan every concurrent session's `.claude/worktrees/` checkout.

The one exception to loading `references/` on demand is a **norm set** — `document-writing`,
`english-document-writing`, `japanese-document-writing` — which this file mandates applying in full
rather than step by step. Where such a skill splits, its `SKILL.md` opens by telling the host to
load every `references/` file before drafting: no step *needs* a norm, so a host left to load on
demand could apply a subset and still believe it followed the skill.

## Working in parallel without breaking each other

Several people and agents work in this repo at once. The rules below keep sessions from
colliding or regressing each other. Full guide: [`docs/ai-development.md`](docs/ai-development.md).

- **One topic per branch.** Branch off `main` as `claude/<short-topic>` (or `<user>/<topic>`).
  Keep changes small and focused — small PRs merge fast and conflict rarely.
- **The gate is the contract.** Never push red. The tracked pre-push hook runs `make check` for
  you; run `make setup` once on a fresh clone (web sessions get it automatically). The
  deterministic test suite is the regression net — if you change behavior, a test should change
  with it. Self-heal mechanism: [`docs/ai-development.md`](docs/ai-development.md#never-push-red).
  **`git push --no-verify` is strictly forbidden — no exceptions, including emergencies.** It
  skips the same `make check` gate CI would otherwise run first, so it does not save time — it only
  moves the same red result from your terminal onto the shared PR. If the hook fails on something
  you believe is a false positive (stale `.claude/worktrees/` state, e.g.), fix the root cause or
  verify from a clean sibling worktree — never bypass the hook to force a push through. No git-side
  mechanism can enforce this on its own: `--no-verify` skips every hook unconditionally, and git
  refuses to let a config alias override an existing subcommand
  ([`docs/ai-development.md`](docs/ai-development.md#never-push-red) has the evidence). `make
  setup` best-effort installs a personal shell safeguard for this (`make git-guard-install` runs it
  standalone); CI's independent `make check` re-run before merge is what actually makes the rule
  hold regardless of what runs locally.
- **Git defenses are wired the same way (BE-0043).** `make hooks` also self-heals the local git
  settings that ease parallel work: a `uv.lock` merge driver, the matching `apm.lock.yaml` one
  (BE-0390), and `rerere`. No manual `git config` needed. Mechanism:
  [`docs/ai-development.md`](docs/ai-development.md#rebase-early-integrate-small-conflicts).
- **Never commit a secret.** A tracked pre-commit/prepare-commit-msg/commit-msg hook and a CI
  re-scan, both backed by [gitleaks](https://github.com/gitleaks/gitleaks) and its tracked
  `.gitleaks.toml` config, block a secret before and after it lands on a branch. Mechanism:
  [`docs/ai-development.md`](docs/ai-development.md#block-a-secret-before-its-committed).
- **Rebase, don't drift.** Before pushing, `git fetch origin && git rebase origin/main` so you
  integrate others' merged work early and surface conflicts while they're small. `make preflight`
  (BE-0069) does this and runs the gate, then prints the "definition of done" reminder — the
  advisory, run-it-early version of the pre-push gate.
- **Stay in your lane.** Touch only the files your task needs. If a change must cut across many
  modules (e.g. a driver-API change), say so up front so others can avoid that surface. A minor
  defect or small improvement you notice *outside* that lane goes to
  [`record-issue`](.apm/skills/record-issue/SKILL.md) (BE-0384), which files it as a GitHub Issue
  once you approve the draft — so the finding outlives the session without widening the change in
  hand.
- **Isolate concurrent sessions with worktrees.** Run each session in its own
  `git worktree` + branch so two agents never edit the same checkout. Outside Claude Code, create
  that worktree with `make worktree TOPIC=<topic>` (BE-0069). Pass `PREFIX=<user>` for a human
  branch. Claude Code keeps its own tooling under `.claude/worktrees/`. Do not invent ad-hoc
  `git worktree add` paths on Cursor, Codex, or other environments. Generated/scratch output
  (`runs/`, `tmp/`, `.venv/`) is gitignored — keep it that way. **Never put `core.worktree`, or
  `core.bare = true`, in the shared `.git/config`** — with `extensions.worktreeConfig` on, git drops
  its exception for those two, so a shared value governs *every* worktree at once: `core.worktree`
  silently points them all at one directory, and `core.bare = true` fails every command that needs a
  work tree. `make hooks` refuses to run until the value is cleared from the shared config; a
  worktree that genuinely needs one adds it back with `git config --worktree` (issue #1803). Recipe:
  [`docs/ai-development.md`](docs/ai-development.md#isolate-concurrent-sessions-with-worktrees).
- **Right-size the model and reasoning effort (BE-0103).** Match a session's model/effort to the
  task: heavy work (implementing, refactors, design) runs on a capable model at high effort; light
  chores (a roadmap item's `Status` flip, link fixes, mechanical renames) downshift. The in-repo skills carry a default
  `model:` in their frontmatter, so the economical choice is automatic and still overridable. The
  task→capability matrix and the phase/subagent guidance live in
  [`docs/ai-development.md`](docs/ai-development.md#right-sizing-the-model-and-reasoning-effort-be-0103).
- **Who opens the PR depends on the work (BE-0230).** Two paths:
  - **BE-creation work** — a proposal PR from [`ideation`](.apm/skills/ideation/SKILL.md) or the
    proposal phase of [`propose-and-build`](.apm/skills/propose-and-build/SKILL.md): **don't
    auto-create it.** Push to your branch and let the human open the PR. A proposal is a human
    checkpoint, and its BE id is allocated only when a human merges it (BE-0089) — auto-opening
    would erode that checkpoint.
  - **Implementation work** — [`implement-be`](.apm/skills/implement-be/SKILL.md) and
    [`fix-issue`](.apm/skills/fix-issue/SKILL.md) (BE-0380), whose output
    is always a self-contained, gate-green change: **auto-open the Draft PR after the gate, then run
    a paced `/loop`** that drives the mechanical tail (CI fixes, review replies) to quiet-and-green,
    delegating each iteration's `pr-followup` work to a fresh subagent so the heavy implement
    transcript stays out of it. The loop escalates to the human on a design-change comment or a
    merge conflict, and never marks the PR ready itself. See `implement-be` steps 10–12 —
    `fix-issue` runs the follow-up steps 11–12 unmodified, and its own step 7 adapts step 10 for a
    plain GitHub issue that carries no BE id.
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
- **A wording-only review finding arrives as a companion PR (BE-0343).** When the automated reviewer
  marks a finding `(non-blocking, prose)`, a job applies that finding's own `suggestion` block to a
  `prose-fix/pr-<N>` branch and opens a small PR **based on your branch**, so your PR pays no CI
  cycle for a change with no behavioral risk. Review and merge it like any other small PR; the job
  has already replied to and resolved the source thread, so don't answer those threads yourself.
  Mechanism: [`docs/ai-development.md`](docs/ai-development.md#the-companion-pr-for-wording-only-findings-be-0343).

## Conventions

- Comments explain **why**, not what; match the surrounding density and tone (the codebase
  favors short, purposeful comments). Don't add narration. When the reasoning already lives in a
  roadmap item or a `docs/` page, cite it and keep only the result — don't re-derive the argument
  ([`docs/ai-development.md`](docs/ai-development.md#cite-an-argument-instead-of-repeating-it-comments-and-docstrings)).
  The full inline-comment rule (provenance and `TODO(BE-NNNN)` format, suppression reasons, the
  docstring boundary) is in
  [`docs/ai-development.md`](docs/ai-development.md#inline-code-comments).
- **Docstrings (BE-0065).** The public API surface (`Driver` + shared types, CLI, MCP tools,
  scenario schema, public functions of runner / `assertions` / `network`) uses **Google-style**
  docstrings — a one-line summary then `Args:` / `Returns:` / `Raises:` *only where they add
  information*; internal `_helpers` keep one prose line of *why*. **Never restate types**; describe
  meaning. English, like all code. Migrate module by module in small PRs. Full rule:
  [`docs/ai-development.md`](docs/ai-development.md).
- **Invoke the writing skills *before* you write or revise a BE roadmap item or a prose doc, not
  after.** [`document-writing`](.apm/skills/document-writing/SKILL.md) is the authoritative
  norm and the umbrella above two language layers; apply it together with the layer for the
  language you are writing — [`english-document-writing`](.apm/skills/english-document-writing/SKILL.md)
  for English, [`japanese-document-writing`](.apm/skills/japanese-document-writing/SKILL.md)
  for Japanese. The Japanese layer binds **without exception, for any Japanese you produce** — new
  prose, translations, and revisions alike, not only `docs/ja/` and roadmap `*-ja.md`. Like the
  bilingual-docs rule, all three are review-time norms, not a CI gate.
- Docs are **bilingual**: English in `docs/`, Japanese mirror in `docs/ja/`. Update both when
  you change a documented behavior.
- **Keep DESIGN.md and `docs/architecture.md` in step with behavior (BE-0113).** A PR that changes
  behavior described by [`DESIGN.md`](DESIGN.md) or [`docs/architecture.md`](docs/architecture.md)
  must update the affected document in the same change. This rule stays a review-time norm, not a CI
  gate: checking that a paragraph of prose still matches the code needs semantic judgment, which
  would put an LLM on the `run` / CI verdict path (prime directive 1) — so it holds the same way as
  the bilingual-docs rule above.
- **Link glossary terms on first use; don't re-explain them (BE-0286).** When prose in a BE roadmap
  item or a `docs/` page uses a term defined in [`docs/glossary.md`](docs/glossary.md) in its
  Bajutsu-specific sense, link its first substantive mention to the term's glossary entry
  (`glossary.md#anchor`, or `docs/ja/glossary.md#anchor` on the Japanese side) rather than
  re-explaining the term inline. Like the two rules just above, this is a review-time norm, not a CI
  gate: deciding whether an ordinary word like *step* or *target* carries its Bajutsu-specific sense
  needs human judgment, which prime directive 1 keeps off the `run` / CI path.
- **Documentation style (both languages, every doc and every update).** Write natural prose. **No
  coined terms** (use established technical/ordinary words); **no forced translation** (keep the
  original term — `selector`, `actuator`, `backend` — when a translation reads unnaturally); **no
  omissions** (each document self-contained; spell out an acronym in full on first use with the
  acronym in parentheses, e.g. role-based access control (RBAC), then the acronym alone). Japanese
  docs — `docs/ja/` and every roadmap `*-ja.md` — are written in **敬体 (ですます調)**, never 常体,
  under the [`japanese-document-writing`](.apm/skills/japanese-document-writing/SKILL.md) skill (above). Full
  guidance: [`docs/ai-development.md`](docs/ai-development.md).
- **Roadmap items use BE IDs (strict).** Every item is one directory `roadmaps/BE-NNNN-<slug>/`
  holding **both** language files `BE-NNNN-<slug>.md` and `BE-NNNN-<slug>-ja.md` (`BE` = *Bajutsu
  Evolution*, `NNNN` a zero-padded monotonic ID). The path is fixed when the ID is allocated and
  **never moves**; `Status` (`Implemented` / `In progress` / `Proposal` / `Deferred` / `Rejected`)
  decides only the [dashboard](https://bajutsu-e2e.github.io/bajutsu/api/roadmap.html) bucket —
  `roadmaps/README.md` carries no status table to keep in sync. Name new items with the `BE-XXXX`
  placeholder — the number is allocated **on `main` after merge** (BE-0089), and **IDs are
  permanent — never renumber.** Full rule
  (file format, metadata fields, MECE `Detailed design`, living `Progress` checklist, both-way PR
  links, author handle, reciprocal `Related`/`Superseded by`):
  [`roadmaps/README.md`](roadmaps/README.md) ·
  [`docs/ai-development.md`](docs/ai-development.md#roadmap-items-be-ids-strict).
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
  PR`, both languages) — including `In progress` items — and the row is updated in the same change that lands the PR.
