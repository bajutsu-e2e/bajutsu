**English** · [日本語](BE-0284-docs-single-source-of-truth-ja.md)

# BE-0284 — Consolidate duplicated documentation norms under single sources of truth

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0284](BE-0284-docs-single-source-of-truth.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0284") |
| Implementing PR | [#1169](https://github.com/bajutsu-e2e/bajutsu/pull/1169) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

Bajutsu's documentation restates several cross-cutting norms independently in many files. This item designates one canonical file for each such norm and replaces every other copy with a short pointer to that file. Today a contributor who changes a rule must find and edit every restatement by hand. After this item, one edit in the canonical file suffices.

## Motivation

An audit covered `CLAUDE.md`, `docs/ai-development.md`, `CONTRIBUTING.md`, `AGENTS.md`, `roadmaps/README.md`, `.github/PULL_REQUEST_TEMPLATE.md`, and `.github/claude-review-prompt.md`. The audit found the same norms restated across several files, word for word or paraphrased, with no link back to a canonical source. Two of those restatements have already drifted into contradiction, which shows the concrete cost of the pattern:

- `CONTRIBUTING.md`, `AGENTS.md`, and `.github/PULL_REQUEST_TEMPLATE.md` still describe roadmap items as filed under `roadmaps/proposals/`, `implemented/`, `in-progress/`, and `deferred/`. [BE-0159](../BE-0159-flatten-roadmap-status-folders/BE-0159-flatten-roadmap-status-folders.md) retired that folder split. The current layout is one flat `roadmaps/BE-NNNN-<slug>/` per item, with `Status` deciding only the index bucket. `docs/ai-development.md` and `CLAUDE.md` describe it correctly. A contributor who reads the stale copies first would create a directory layout that CI rejects.
- `CLAUDE.md`, `docs/ai-development.md`, `CONTRIBUTING.md`, `AGENTS.md`, and `docs/contributor-workflow-tutorial.md` each list a different subset of the steps that `make check` runs. None of the lists matches the actual eleven steps. A reader who trusts the shorter lists underestimates what the gate covers.

The same pattern repeats elsewhere without having drifted yet:

- `roadmaps/README.md` fully restates the roadmap BE-ID conventions (placeholder `BE-0284`, allocation on merge, bilingual files, index regeneration) instead of linking the canonical section of `docs/ai-development.md`.
- `AGENTS.md` fully re-explains the PR title, body, and Draft conventions instead of pointing at `docs/ai-development.md`.
- `.github/claude-review-prompt.md` bundles condensed, unlinked restatements of six norms: the prime directives, the docstring style ([BE-0065](../BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference.md)), bilingual docs, the Japanese prose style, the roadmap-link rule, and the convention that comments explain why. This file is the case that prompted this item.

Each restatement is a second place where a future edit can miss.

The risk lies in the project process, not in product behavior. It still falls squarely within the contributor-facing infrastructure that this roadmap already tracks. [BE-0069](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md) turned written procedure into a run command. [BE-0278](../BE-0278-tech-writing-skill/BE-0278-tech-writing-skill.md) created a single prose-style authority for both languages. This item extends that lineage from *style* to *cross-file norm placement*.

## Detailed design

For each norm cluster, designate the file that already reads as the fullest and most current source. Reduce every other appearance to a short pointer, or to a paraphrase that carries no independent detail to drift out of sync.

| Norm | Canonical home | Files to reduce to a pointer |
|---|---|---|
| Roadmap directory layout & BE-ID allocation | `docs/ai-development.md` | `roadmaps/README.md`, `CONTRIBUTING.md`, `AGENTS.md`, `.github/PULL_REQUEST_TEMPLATE.md` |
| PR title / body / Draft conventions | `docs/ai-development.md` | `AGENTS.md`, `CONTRIBUTING.md` |
| `make check` step list | `CLAUDE.md` (already the fullest) | `docs/ai-development.md`, `CONTRIBUTING.md`, `AGENTS.md`, `docs/contributor-workflow-tutorial.md` |
| Docstring style (BE-0065) | `docs/ai-development.md` | `.github/claude-review-prompt.md` |
| Bilingual docs process (which files, when) | `docs/ai-development.md` | `.github/claude-review-prompt.md`, `AGENTS.md` |
| Japanese prose style (敬体, no coined terms) | `japanese-document-writing` skill (per BE-0278) | `.github/claude-review-prompt.md`, `AGENTS.md` |
| Prime directives | `CLAUDE.md` (kept, see below) | — |

The work breaks down MECE (mutually exclusive, collectively exhaustive) as follows:

1. **Fix the stale contradiction first.** Update `CONTRIBUTING.md`, `AGENTS.md`, and `.github/PULL_REQUEST_TEMPLATE.md` to describe the flat `roadmaps/BE-NNNN-<slug>/` layout, citing BE-0159. Then reconcile the five `make check` enumerations against the eleven steps that `CLAUDE.md` lists.
2. **Fold the six restated norms in `.github/claude-review-prompt.md` into links.** Point each norm at its canonical section in `CLAUDE.md` or `docs/ai-development.md`, or at the `japanese-document-writing` skill for prose style. Do not re-explain the rule.
3. **Replace the independent BE-ID restatement in `roadmaps/README.md` with a link** to the canonical section of `docs/ai-development.md`. Keep only the one or two sentences that a reader needs before reaching the index table.
4. **Shorten the full PR-convention and roadmap-convention sections of `AGENTS.md` and `CONTRIBUTING.md`** to a pointer plus whatever detail is genuinely specific to the audience of that document.
5. **Add a short "don't restate it, link it" line to `docs/ai-development.md` itself**, next to the existing documentation-style conventions. A contributor who later adds a new norm then places it once by default.

**The prime directives are the deliberate exception.** They are short, load-bearing, and meant to travel. `CLAUDE.md` already marks its own restatement in the PR-conventions section as an explicit "short form" of the fuller rule. This item keeps every prime-directive restatement that is short and accurate. It removes only the restatements that have drifted or that reproduce detail beyond the directive itself.

Consolidation stays a review-time norm, not a CI check. Judging whether a paragraph "restates" a rule or "legitimately repeats a load-bearing rule" needs semantic judgment. The bilingual-docs rule and the [BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md) DESIGN.md-alignment rule need the same judgment, and prime directive 1 keeps it off the deterministic gate.

## Alternatives considered

- **A script that hashes or diffs prose blocks and fails CI on divergence.** Rejected. Detecting that two differently worded paragraphs state the same rule needs semantic judgment. That judgment would put a large language model (LLM) on the `run`/CI verdict path, which prime directive 1 forbids. Without an LLM, the script would raise false positives on every deliberately short restatement, the prime directives in particular.
- **Delete every restatement and require every document to link out, with no exceptions.** Rejected for the shortest, load-bearing rules. `CLAUDE.md` already states the "no omissions" rule: a document must be self-contained on first read. Such a document legitimately keeps a short, accurate copy of the prime directives. It should not send a first-time reader elsewhere for three sentences.
- **One new top-level "conventions.md" merging every norm.** Rejected. `docs/ai-development.md` already serves as the canonical home for AI-development-process norms. `CLAUDE.md` serves as the canonical home for the prime directives and the quick-reference gate. A third top-level document would fragment the norms further instead of consolidating them.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Fix the stale roadmap-directory-layout references in `CONTRIBUTING.md`, `AGENTS.md`, and
      `.github/PULL_REQUEST_TEMPLATE.md` to match BE-0159's flat layout.
- [x] Reconcile the five divergent `make check` step enumerations to one list, linked from
      every other copy.
- [x] Fold `.github/claude-review-prompt.md`'s six restated norms into links back to their
      canonical sources.
- [x] Replace `roadmaps/README.md`'s independent BE-ID-convention restatement with a link to
      `docs/ai-development.md`.
- [x] Shorten `AGENTS.md`'s and `CONTRIBUTING.md`'s full PR-convention and roadmap-convention
      restatements to pointers.
- [x] Add a short "don't restate it, link it" line to `docs/ai-development.md`'s documentation
      conventions.

### Log

- Consolidated the norms in one pass (PR #1169):
  - `CONTRIBUTING.md`, `AGENTS.md`, and `.github/PULL_REQUEST_TEMPLATE.md` now describe the flat layout.
  - The `make check` enumerations in `docs/ai-development.md`, `CONTRIBUTING.md`, `AGENTS.md`, and `docs/contributor-workflow-tutorial.md` now point at `CLAUDE.md`.
  - The house-convention norms in `.github/claude-review-prompt.md` link their canonical homes.
  - The BE-ID restatement in `roadmaps/README.md` is reduced to a pointer.
  - The "don't restate it, link it" norm is added to `docs/ai-development.md`.
  - The Japanese mirrors are updated in step.

## References

- [BE-0159 — Flatten roadmap directory (retire status-driven folders)](../BE-0159-flatten-roadmap-status-folders/BE-0159-flatten-roadmap-status-folders.md)
- [BE-0278 — Tech-writing skill](../BE-0278-tech-writing-skill/BE-0278-tech-writing-skill.md)
- [BE-0113 — DESIGN.md realignment](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md)
- [BE-0069 — Executable contributor guardrails](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md)
- [BE-0065 — Docstring standard / API reference](../BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference.md)
