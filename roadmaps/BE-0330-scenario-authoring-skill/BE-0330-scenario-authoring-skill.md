**English** · [日本語](BE-0330-scenario-authoring-skill-ja.md)

# BE-0330 — Ship a Claude Code skill that drafts and self-validates scenarios from source

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0330](BE-0330-scenario-authoring-skill.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0330") |
| Topic | Authoring experience |
<!-- /BE-METADATA -->

## Introduction

This item ships a Claude Code skill. Claude Code is Anthropic's coding-agent CLI (Command Line
Interface). The skill installs as a package inside the `bajutsu` distribution. Reading the
target's own source code, the skill drafts its `bajutsu.config.yaml` entry. It also drafts the
target's first scenario files. It then checks that draft against `run`'s existing device-free
tools. A human runs those checks before ever launching a Simulator, a browser, or an emulator.

The package carries a thin, skill-specific playbook next to five documents. Those five documents
already define the grammar and its vocabulary. Three of the five are `scenarios.md`,
`dsl-grammar.md`, and `configuration.md`. The other two are `selectors.md` and `glossary.md`. The
playbook keeps a verbatim copy of each one. It orchestrates authoring, rather than restating a rule
that already lives in those five documents.

A new `bajutsu skill install` command copies the package into a target project. The source is the
installed `bajutsu` distribution. The destination is the project's own `.claude/skills/`
directory. An installed copy always matches the `bajutsu` version the project already depends on.

## Motivation

[Onboarding a new target](../../docs/configuration.md#onboarding-a-new-target) today takes five
manual steps. A developer applies the target's identifier convention. They add one
`targets.<name>` config entry. They optionally factor out a `setup:` prelude. They verify the
result with `bajutsu doctor`. They place scenarios that reference the new identifiers.

A developer new to the grammar must hold five documents in mind at once.
[`configuration.md`](../../docs/configuration.md) covers the config shape.
[`scenarios.md`](../../docs/scenarios.md) and [`dsl-grammar.md`](../../docs/dsl-grammar.md) cover
step syntax. [`selectors.md`](../../docs/selectors.md) covers which selector kind stays stable.
[`glossary.md`](../../docs/glossary.md) covers the vocabulary the other four assume. That developer
must also read the target's own source. They need every identifier worth exercising.

[`record`](../BE-0012-action-capture-record/BE-0012-action-capture-record.md) already shortens
this task, for a developer who has a running target to drive. Claude explores the running target.
Claude then writes the scenario from what it observes on screen.

That live-tree grounding is not what a coding agent needs. Claude Code already sits inside the
target project's own repository. It can read `accessibilityIdentifier`, `data-testid`, and
`resource-id` values straight from source. It needs no Simulator, no browser, and no emulator to
do so.

Two rules in the shipped playbook turn that source access into safe authoring. Without those
rules, source access alone risks the "AI decides pass/fail" idea. This roadmap already declined
that idea ([Not adopting](../README.md#not-adopting-already-covered--out-of-scope)).

First, the playbook requires every drafted step to ground in an identifier. The skill must have
actually found that identifier in the target's source. When a goal needs an element that
carries no identifier yet, the skill reports the gap. It never invents a fragile selector to cover
that gap.

Second, the playbook requires the finished draft to pass four read-only tools. The skill reports
the draft ready after that, and not before. `run` already ships all four: `lint`, `audit`, `trace
--explain`, and
`doctor --scenario --environment-only`. None of these four tools invokes a model. None of them
decides pass or fail. The skill's verdict never goes past "well-formed"; it never reaches
"passing".

The one authoritative confirmation stays the same as today. A developer runs `bajutsu doctor
--target <name>`. Or a developer runs a first `bajutsu run` against the running target. Onboarding
already asks for this same step. Prime directive 1 holds, because nothing here moves that
boundary.

## Detailed design

### 1. Package layout keeps one source of truth for the grammar

The package's source lives at `bajutsu/scenario_author_skill/` in this repository. It holds two
kinds of file:

- `SKILL.md` — the playbook. It states the workflow steps below. It states the per-backend
  heuristic for locating an existing stable identifier in source. It states the self-validation
  loop. The playbook stays short on purpose. It orchestrates the reference documents. It never
  restates their rules.
- `references/` — verbatim copies of `docs/scenarios.md`, `docs/dsl-grammar.md`,
  `docs/configuration.md`, `docs/selectors.md`, and `docs/glossary.md`.

A test mirrors the existing bilingual-docs check. The test fails `make check` when any file under
`references/` drifts from its `docs/` counterpart. It compares the two, byte for byte. The
grammar's single source of truth stays `docs/`. The bundled copy stays a synchronized mirror, not
a fork a maintainer could edit out of step.

The package does not copy the scenario JSON (JavaScript Object Notation) Schema at all. The
playbook instead has the skill run `bajutsu schema` itself. The target project already has
`bajutsu` installed alongside it. The authoritative shape is always the exact installed version.
No copy exists to go stale.

### 2. `bajutsu skill install` materializes the bundle into a target project

`bajutsu skill install [--dest .claude/skills/bajutsu-scenario-author] [--force]` is new. The
command is Claude-free. It copies the package from the installed `bajutsu` distribution. The
destination is the target project itself.

The verb is `install`, not `export`. `bajutsu` already has an `export` command
([BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md)) that archives a
finished run into a zip. That command's `export` moves a result out of `bajutsu`. This command's
job runs the other way: it moves a bundled package into a project. `install` names that direction,
and it frees `export` from a second, unrelated meaning.

The package ships as ordinary package data ([`pyproject.toml`](../../pyproject.toml)). It needs no
force-include rule. That rule exists for a single, different case: `bajutsu/_xcuitest_runner/**`.
The repository's `.gitignore` excludes that directory, since it holds generated output. Without
the rule, hatchling would drop the directory.

The command exits non-zero when the destination already exists. One exception applies: a
destination that a prior install itself produced. The `--force` flag overrides this check. A plain
re-run after a `bajutsu` upgrade always refreshes the bundle to the newly installed version.

### 3. The playbook grounds every draft in source, never in a guess

The playbook walks the skill through:

1. **Identify the backend.** The backend is web, iOS, or Android. The playbook infers it from the
   target project's own shape. A `package.json` with a browser framework signals web. An
   `.xcodeproj` signals iOS. A Gradle module signals Android.
2. **Locate existing stable identifiers in source.** The playbook reads per-backend markers. It
   looks for `data-testid` attributes on web. It looks for `accessibilityIdentifier` assignments on
   iOS. It looks for `resource-id` / `testTag` values on Android.
   [`selectors.md`](../../docs/selectors.md#resolution-semantics)'s `id` row states the exact
   mapping. The skill reads the target's source tree directly. Any Claude Code invocation already
   has this same access to the repository it runs in.
3. **Draft `targets.<name>` and the scenario steps.** Step 2 supplies every identifier the draft
   uses. The draft follows [`selectors.md`](../../docs/selectors.md)'s stability ladder. An id-based
   selector always wins over a coordinate gesture or an index. Suppose a goal needs an element
   that carries no identifier. The skill then reports a gap for the developer to add. The gap
   report names the identifier per the target's namespace convention
   ([`configuration.md`](../../docs/configuration.md#identifier-naming-convention)). The skill
   never falls back to a fragile selector to paper over that gap. A fallback like that would
   violate prime directive 2, determinism first.
4. **Run the self-validation loop.** Design unit 4 below defines this loop. The playbook runs it
   before reporting the draft ready.

### 4. A device-free self-validation loop stands between the draft and the developer

The playbook requires four tools before the skill reports a draft ready. It requires them in that
order.

First, `bajutsu lint` checks that the draft stays grammar-valid.

Second, `bajutsu audit` checks the draft's stability grade. That grade must not include a Fragile
selector. When it does, the skill redrafts the step itself. It never reports a Fragile finding as
someone else's problem.

Third, `bajutsu trace --explain` flags a broad capture-policy rule. It flags the rule before a
first run ever pays its cost.

Fourth, the skill runs `bajutsu doctor --target <name> --scenario <file> --environment-only`. This
is a capability preflight. It confirms the chosen backend supports every construct the draft uses.

All four tools stay read-only, and all four already count as `bajutsu`-Claude-free
([`ai-boundary.md`](../../docs/ai-boundary.md)). Wiring them into the playbook adds no new model
call anywhere.

The skill's report states the draft is ready once all four tools pass, and not before. That report
also names the developer's next step: the one authoritative confirmation. That step is `bajutsu
doctor --target <name>`, or a `bajutsu run` against the running target.

### 5. Documentation

- [`docs/getting-started/ios.md`](../../docs/getting-started/ios.md) and
  [`web.md`](../../docs/getting-started/web.md) each carry an existing "Author with AI" section.
  Each section gains a subsection that introduces `bajutsu skill install`. The subsection presents
  the new command as a source-grounded alternative to `record`, for a developer with no running
  target yet. An Android getting-started page does not exist yet, so
  [`docs/configuration.md`](../../docs/configuration.md) documents the Android backend's install
  step instead, until one exists.
- [`docs/cli.md`](../../docs/cli.md) documents the new `skill install` command next to `lint` /
  `schema`.
- [`docs/ai-boundary.md`](../../docs/ai-boundary.md) gains a new row for `skill install`, under the
  Claude-free column. The command itself does nothing but copy files. A companion note places the
  installed skill's own drafting work elsewhere. That work runs inside Claude Code, an external
  agent. That
  agent invokes `bajutsu`'s existing Claude-free commands. The drafting work is not a `bajutsu`
  code path at all, so it earns no row of its own.

## Alternatives considered

* **Author a new, condensed cheat sheet instead of bundling the existing documents verbatim.**
  Rejected. A second, hand-maintained account of the grammar would drift from `scenarios.md` and
  `dsl-grammar.md`. It would drift the first time either document changed without a matching edit
  to the cheat sheet. The playbook reaches the same "quick to consult" goal a different way: it
  stays short and points into the bundled documents, instead of duplicating them.
* **Distribute the bundle as a Git submodule of the `bajutsu` repository.** Rejected. A submodule
  pulls in this entire monorepo's history, for the sake of a handful of files. Submodule checkout
  is also a well-known source of friction. A forgotten `--init --recursive` is one pitfall. A
  detached `HEAD` is another. That friction lands hardest on a contributor who does not already use
  submodules elsewhere. A submodule also ties freshness to a Git ref, instead of to the `bajutsu`
  version. The target project already manages that version through its own dependency manager.
* **Document a manual copy-paste recipe and skip the new CLI command.** Considered as a smaller v1:
  `getting-started` would point at the package's installed location, and a developer would copy it
  by hand. Rejected. A developer forgets to redo a hand copy after a `bajutsu` upgrade. The copy
  then drifts from the schema the installed version actually accepts, without anyone noticing.
  `bajutsu skill install` costs one small command instead, and avoids that drift.
* **Extend `record` or `crawl` to also read source, instead of shipping a separate skill.**
  Rejected. `record` and `crawl` are `bajutsu` Python code paths. Both exist to drive a running
  target. Teaching them to read arbitrary source trees would blur a boundary. That boundary
  separates "the tool drives the target" from "a coding agent drafts from source". It would also
  require `bajutsu` to
  embed source-reading agentic behavior. Claude Code already provides that behavior for free, when
  invoked as a skill.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] **Package layout** — `bajutsu/scenario_author_skill/` with `SKILL.md` and `references/`. The
  drift check against `docs/` wires into `make check`.
- [ ] **`bajutsu skill install`** — the new CLI command, wheel packaging, `--dest` / `--force`.
- [ ] **Playbook** — backend identification. Per-backend identifier lookup. Source-grounded
  drafting. The missing-identifier gap report.
- [ ] **Self-validation loop** — `lint` → `audit` → `trace --explain` → `doctor --scenario
  --environment-only`. Wired into the playbook, with the redraft-on-Fragile rule.
- [ ] **Documentation** — `getting-started` subsections, `cli.md`, `ai-boundary.md`.

## References

- [`docs/configuration.md`](../../docs/configuration.md) — onboarding a target, and the identifier
  naming convention.
- [`docs/scenarios.md`](../../docs/scenarios.md) and
  [`docs/dsl-grammar.md`](../../docs/dsl-grammar.md) — the grammar this skill drafts against.
- [`docs/selectors.md`](../../docs/selectors.md) — the stability ladder.
- [`docs/glossary.md`](../../docs/glossary.md) — the vocabulary the other four bundled documents
  assume.
- [`docs/ai-boundary.md`](../../docs/ai-boundary.md) — the Claude-free / Uses-Claude split.
- [BE-0012](../BE-0012-action-capture-record/BE-0012-action-capture-record.md) — `record`, the
  running-target-driven [Tier-1](../../docs/glossary.md#the-two-tiers) authoring path this skill
  complements.
- [roadmap → Not adopting](../README.md#not-adopting-already-covered--out-of-scope) — the declined
  "AI decides pass/fail" framing this proposal stays clear of.
