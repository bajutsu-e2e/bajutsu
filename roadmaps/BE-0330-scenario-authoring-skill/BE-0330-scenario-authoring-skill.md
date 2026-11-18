**English** · [日本語](BE-0330-scenario-authoring-skill-ja.md)

# BE-0330 — Ship a Claude Code skill that drafts and self-validates scenarios from source

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0330](BE-0330-scenario-authoring-skill.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0330") |
| Topic | Authoring experience |
<!-- /BE-METADATA -->

## Introduction

This item ships a Claude Code skill. Claude Code is Anthropic's coding-agent Command Line Interface (CLI). The skill installs as a package inside the `bajutsu` distribution.

The skill reads the target's own source code. From that source, the skill drafts the `bajutsu.config.yaml` entry of the target. The skill also drafts the first scenario files of the target. The skill then checks the draft against the device-free tools that `run` already ships. A human runs those checks before launching a Simulator, a browser, or an emulator.

The package carries a thin, skill-specific playbook next to five documents. The five documents already define the grammar and its vocabulary. Three of them are `scenarios.md`, `dsl-grammar.md`, and `configuration.md`. The other two are `selectors.md` and `glossary.md`. The package keeps a verbatim copy of each document. The playbook orchestrates authoring. It does not restate a rule that already lives in the five documents.

A new `bajutsu skill install` command copies the package into a target project. The source is the installed `bajutsu` distribution. The destination is the project's own `.claude/skills/` directory. An installed copy always matches the `bajutsu` version that the project already depends on.

## Motivation

[Onboarding a new target](../../docs/configuration.md#onboarding-a-new-target) today takes five manual steps. A developer applies the target's identifier convention. The developer adds one `targets.<name>` config entry and optionally factors out a `setup:` prelude. The developer verifies the result with `bajutsu doctor`. The developer places scenarios that reference the new identifiers.

A developer new to the grammar must hold five documents in mind at once.

- [`configuration.md`](../../docs/configuration.md) covers the config shape.
- [`scenarios.md`](../../docs/scenarios.md) and [`dsl-grammar.md`](../../docs/dsl-grammar.md) cover step syntax.
- [`selectors.md`](../../docs/selectors.md) covers which selector kind stays stable.
- [`glossary.md`](../../docs/glossary.md) covers the vocabulary that the other four documents assume.

The developer must also read the target's own source, to find every identifier worth exercising.

[`record`](../BE-0012-action-capture-record/BE-0012-action-capture-record.md) already shortens this task for a developer who has a running target to drive. Claude explores the running target. Claude then writes the scenario from what Claude observes on screen.

A coding agent does not need that live-tree grounding. Claude Code already sits inside the repository of the target project. Claude Code can read `accessibilityIdentifier`, `data-testid`, and `resource-id` values from source. Claude Code needs no Simulator, no browser, and no emulator to do so.

Two rules in the shipped playbook turn that source access into safe authoring. Without the two rules, source access alone risks the "AI decides pass/fail" idea. This roadmap already declined that idea ([Not adopting](../README.md#not-adopting-already-covered--out-of-scope)).

First, the playbook requires every drafted step to ground in an identifier. The skill must have actually found the identifier in the target's source. When a goal needs an element that carries no identifier yet, the skill reports the gap. The skill never invents a fragile selector to cover the gap.

Second, the playbook requires the finished draft to pass four read-only tools. The skill reports the draft ready after the draft passes, and not before. `run` already ships all four tools. The four tools are `lint`, `audit`, `trace --explain`, and `doctor --scenario --environment-only`. None of the four tools invokes a model, and none decides pass or fail. The verdict of the skill never goes past "well-formed". The verdict never reaches "passing".

The one authoritative confirmation stays the same as today. A developer runs `bajutsu doctor --target <name>`. Or a developer runs a first `bajutsu run` against the running target. Onboarding already asks for this step. Prime directive 1 holds, because nothing here moves that boundary.

## Detailed design

### 1. Package layout keeps one source of truth for the grammar

The package source lives at `bajutsu/scenario_author_skill/` in this repository. The package holds two kinds of file.

- `SKILL.md` — the playbook. The playbook states the workflow steps below. The playbook also states the per-backend heuristic for finding a stable identifier in source. The playbook also states the self-validation loop. The playbook stays short on purpose. It orchestrates the reference documents and never restates their rules.
- `references/` — verbatim copies of five documents.
  - `docs/scenarios.md`
  - `docs/dsl-grammar.md`
  - `docs/configuration.md`
  - `docs/selectors.md`
  - `docs/glossary.md`

A test mirrors the existing bilingual-docs check. The test compares each file under `references/` with its `docs/` counterpart, byte for byte. The test fails `make check` when the two drift apart. The single source of truth for the grammar stays `docs/`. The bundled copy stays a synchronized mirror, not a fork that a maintainer could edit out of step.

The package does not copy the scenario JavaScript Object Notation (JSON) Schema at all. Instead, the playbook has the skill run `bajutsu schema` itself. The target project already has `bajutsu` installed alongside it. The authoritative shape is always that of the exact installed version. No copy exists to go stale.

### 2. `bajutsu skill install` materializes the bundle into a target project

`bajutsu skill install [--dest .claude/skills/bajutsu-scenario-author] [--force]` is a new command. The command is Claude-free. It copies the package from the installed `bajutsu` distribution into the target project.

The verb is `install`, not `export`. `bajutsu` already has an `export` command ([BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md)) that archives a finished run into a zip. That `export` moves a result out of `bajutsu`. The new command runs the other way: it moves a bundled package into a project. `install` names that direction, and it frees `export` from a second, unrelated meaning.

The package ships as ordinary package data ([`pyproject.toml`](../../pyproject.toml)). The package needs no force-include rule. That rule exists for one different case: `bajutsu/_xcuitest_runner/**`. The directory holds generated output, so the `.gitignore` of the repository excludes it. Without the rule, hatchling would drop the directory.

The command exits non-zero when the destination already exists. One exception applies: a destination that a prior install itself produced. The `--force` flag overrides this check. A plain re-run after a `bajutsu` upgrade always refreshes the bundle to the newly installed version.

### 3. The playbook grounds every draft in source, never in a guess

The playbook walks the skill through four steps.

1. **Identify the backend.** The backend is web, iOS, or Android. The playbook infers the backend from the shape of the target project. A `package.json` with a browser framework signals web. An `.xcodeproj` signals iOS. A Gradle module signals Android.
2. **Locate existing stable identifiers in source.** The playbook reads per-backend markers. On web, the skill looks for `data-testid` attributes. On iOS, the skill looks for `accessibilityIdentifier` assignments. On Android, the skill looks for `resource-id` / `testTag` values. The `id` row of [`selectors.md`](../../docs/selectors.md#resolution-semantics) states the exact mapping. The skill reads the source tree of the target directly. Any Claude Code invocation already has this access to the repository that it runs in.
3. **Draft `targets.<name>` and the scenario steps.** Step 2 supplies every identifier that the draft uses. The draft follows the stability ladder of [`selectors.md`](../../docs/selectors.md). An id-based selector always wins over a coordinate gesture or an index. Suppose a goal needs an element that carries no identifier. The skill then reports a gap for the developer to add. The gap report names the identifier per the namespace convention of the target ([`configuration.md`](../../docs/configuration.md#identifier-naming-convention)). The skill never falls back to a fragile selector to paper over the gap. Such a fallback would violate prime directive 2, determinism first.
4. **Run the self-validation loop.** Design unit 4 below defines the loop. The playbook runs the loop before reporting the draft ready.

### 4. A device-free self-validation loop stands between the draft and the developer

The playbook requires four tools before the skill reports a draft ready. The playbook requires them in the following order.

First, `bajutsu lint` checks that the draft stays grammar-valid.

Second, `bajutsu audit` checks the stability grade of the draft. The grade must not include a Fragile selector. When the grade does, the skill redrafts the step itself. The skill never reports a Fragile finding as someone else's problem.

Third, `bajutsu trace --explain` flags a broad capture-policy rule. The tool flags the rule before a first run ever pays the cost of the rule.

Fourth, the skill runs `bajutsu doctor --target <name> --scenario <file> --environment-only`. This is a capability preflight. It confirms that the chosen backend supports every construct that the draft uses.

All four tools stay read-only, and all four already count as `bajutsu`-Claude-free ([`ai-boundary.md`](../../docs/ai-boundary.md)). Wiring the tools into the playbook adds no new model call anywhere.

The report of the skill states that the draft is ready once all four tools pass, and not before. The report also names the next step for the developer: the one authoritative confirmation. That step is `bajutsu doctor --target <name>`, or a `bajutsu run` against the running target.

### 5. Documentation

- [`docs/getting-started/ios.md`](../../docs/getting-started/ios.md) and [`web.md`](../../docs/getting-started/web.md) each carry an existing "Author with AI" section. Each section gains a subsection that introduces `bajutsu skill install`. The subsection presents the new command as a source-grounded alternative to `record`. The alternative serves a developer with no running target yet. An Android getting-started page does not exist yet. Until one exists, [`docs/configuration.md`](../../docs/configuration.md) documents the install step for the Android backend instead.
- [`docs/cli.md`](../../docs/cli.md) documents the new `skill install` command next to `lint` / `schema`.
- [`docs/ai-boundary.md`](../../docs/ai-boundary.md) gains a new row for `skill install`, under the Claude-free column. The command itself does nothing but copy files. A companion note places the drafting work of the installed skill elsewhere. That work runs inside Claude Code, an external agent. The agent invokes the existing Claude-free commands of `bajutsu`. The drafting work is not a `bajutsu` code path at all, so the work earns no row of its own.

## Alternatives considered

* **Author a new, condensed cheat sheet instead of bundling the existing documents verbatim.** Rejected. A second, hand-maintained account of the grammar would drift from `scenarios.md` and `dsl-grammar.md`. It would drift the first time either document changed without a matching edit to the cheat sheet. The playbook reaches the same "quick to consult" goal a different way. It stays short and points into the bundled documents, instead of duplicating them.
* **Distribute the bundle as a Git submodule of the `bajutsu` repository.** Rejected, for three reasons.
  * A submodule pulls in the entire monorepo history for the sake of a handful of files.
  * Submodule checkout is a well-known source of friction. A forgotten `--init --recursive` is one pitfall. A detached `HEAD` is another. The friction lands hardest on a contributor who does not already use submodules elsewhere.
  * A submodule ties freshness to a Git ref, instead of to the `bajutsu` version. The target project already manages that version through its own dependency manager.
* **Document a manual copy-paste recipe and skip the new CLI command.** Considered as a smaller v1. `getting-started` would point at the installed location of the package. A developer would copy the package by hand. Rejected. A developer forgets to redo a hand copy after a `bajutsu` upgrade. The copy then drifts from the schema that the installed version actually accepts. Nobody notices the drift. `bajutsu skill install` costs one small command instead, and avoids that drift.
* **Extend `record` or `crawl` to also read source, instead of shipping a separate skill.** Rejected. `record` and `crawl` are `bajutsu` Python code paths, and both exist to drive a running target. Teaching them to read arbitrary source trees would blur a boundary. That boundary separates "the tool drives the target" from "a coding agent drafts from source". The change would also require `bajutsu` to embed source-reading agentic behavior. Claude Code already provides that behavior for free, when invoked as a skill.

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
