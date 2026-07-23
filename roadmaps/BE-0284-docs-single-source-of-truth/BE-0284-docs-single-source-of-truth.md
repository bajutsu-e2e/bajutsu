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

This item designates one canonical file for each cross-cutting norm that Bajutsu's
documentation currently restates independently in several places, and replaces every other
copy with a short pointer to that file. A contributor who changes a rule today must find and
edit every restatement by hand; this item makes that a single edit instead.

## Motivation

An audit of `CLAUDE.md`, `docs/ai-development.md`, `CONTRIBUTING.md`, `AGENTS.md`,
`roadmaps/README.md`, `.github/PULL_REQUEST_TEMPLATE.md`, and `.github/claude-review-prompt.md`
found the same norms restated, word for word or paraphrased, across several files with no link
back to a canonical source. Two of those restatements have already drifted into contradiction,
which is the concrete cost of the pattern:

- `CONTRIBUTING.md`, `AGENTS.md`, and `.github/PULL_REQUEST_TEMPLATE.md` still describe roadmap
  items as filed under `roadmaps/proposals/` / `implemented/` / `in-progress/` / `deferred/`.
  [BE-0159](../BE-0159-flatten-roadmap-status-folders/BE-0159-flatten-roadmap-status-folders.md) retired
  that folder split; the current, correct layout — one flat `roadmaps/BE-NNNN-<slug>/` per
  item, with `Status` deciding only the index bucket — lives in `docs/ai-development.md` and
  `CLAUDE.md`. A contributor who reads the stale copies first would create a directory layout
  CI rejects.
- `CLAUDE.md`, `docs/ai-development.md`, `CONTRIBUTING.md`, `AGENTS.md`, and
  `docs/contributor-workflow-tutorial.md` each enumerate a different subset of the steps
  `make check` runs, none matching the actual eleven steps. A reader who trusts the shorter lists
  underestimates what the gate covers.

Beyond those two contradictions, the same pattern repeats without (yet) drifting: the roadmap
BE-ID conventions (placeholder `BE-0284`, allocation on merge, bilingual files, index
regeneration) are fully restated in `roadmaps/README.md` rather than linked from
`docs/ai-development.md`'s canonical section; the PR title/body/Draft conventions are
fully re-explained in `AGENTS.md` rather than pointed at `docs/ai-development.md`; and
`.github/claude-review-prompt.md` bundles condensed, unlinked restatements of six separate
norms — prime directives, docstring style ([BE-0065](../BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference.md)),
bilingual docs, Japanese prose style, the roadmap-link rule, and the comments-explain-why
convention — the exact case that prompted this item. Each restatement is a second place a
future edit can miss.

This is a project-process risk, not a product-behavior one, but it is squarely the kind of
contributor-facing infrastructure this roadmap already tracks — the same ground as
[BE-0069](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md)
(turning written procedure into a run command) and
[BE-0278](../BE-0278-tech-writing-skill/BE-0278-tech-writing-skill.md) (a single prose-style
authority for both languages). This item extends that lineage from *style* to
*cross-file norm placement*.

## Detailed design

For each norm cluster, designate the file that already reads as the fullest, most current
source, and reduce every other appearance to a short pointer or a paraphrase that carries no
independent detail to drift out of sync:

| Norm | Canonical home | Files to reduce to a pointer |
|---|---|---|
| Roadmap directory layout & BE-ID allocation | `docs/ai-development.md` | `roadmaps/README.md`, `CONTRIBUTING.md`, `AGENTS.md`, `.github/PULL_REQUEST_TEMPLATE.md` |
| PR title / body / Draft conventions | `docs/ai-development.md` | `AGENTS.md`, `CONTRIBUTING.md` |
| `make check` step list | `CLAUDE.md` (already the fullest) | `docs/ai-development.md`, `CONTRIBUTING.md`, `AGENTS.md`, `docs/contributor-workflow-tutorial.md` |
| Docstring style (BE-0065) | `docs/ai-development.md` | `.github/claude-review-prompt.md` |
| Bilingual docs process (which files, when) | `docs/ai-development.md` | `.github/claude-review-prompt.md`, `AGENTS.md` |
| Japanese prose style (敬体, no coined terms) | `japanese-document-writing` skill (per BE-0278) | `.github/claude-review-prompt.md`, `AGENTS.md` |
| Prime directives | `CLAUDE.md` (kept, see below) | — |

The work breaks down MECE as:

1. **Fix the stale contradiction first.** Update `CONTRIBUTING.md`, `AGENTS.md`, and
   `.github/PULL_REQUEST_TEMPLATE.md` to describe the flat `roadmaps/BE-NNNN-<slug>/` layout,
   citing BE-0159, and reconcile the five `make check` enumerations against the eleven steps
   `CLAUDE.md` lists.
2. **Fold `.github/claude-review-prompt.md`'s six restated norms into links.** Point each at
   its canonical section in `CLAUDE.md` or `docs/ai-development.md`, or at the
   `japanese-document-writing` skill for prose style, instead of re-explaining the rule.
3. **Replace `roadmaps/README.md`'s independent BE-ID restatement with a link** to
   `docs/ai-development.md`'s canonical section, keeping only the one or two sentences a reader
   needs before they reach the index table.
4. **Shorten `AGENTS.md`'s and `CONTRIBUTING.md`'s full PR-convention and roadmap-convention
   sections** to a pointer plus whatever detail is genuinely specific to that document's
   audience.
5. **Add a short "don't restate it, link it" line to `docs/ai-development.md` itself**, next to
   the existing documentation-style conventions, so a contributor adding a new norm later
   places it once by default.

**Prime directives are the deliberate exception.** They are short, load-bearing, and meant to
travel — `CLAUDE.md` already marks its own restatement in the PR-conventions section as an
explicit "short form" of the fuller rule. This item keeps every prime-directive restatement
that is short and accurate, and only removes ones that have drifted or that reproduce detail
beyond the directive itself.

Consolidation stays a review-time norm, not a CI check: whether a paragraph "restates" versus
"legitimately repeats a load-bearing rule" needs the same semantic judgment as the
bilingual-docs and [BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md)
DESIGN.md-alignment rules, which prime directive 1 keeps off the deterministic gate.

## Alternatives considered

- **A script that hashes or diffs prose blocks and fails CI on divergence.** Rejected:
  detecting that two differently-worded paragraphs state the same rule needs semantic
  judgment, which would put an LLM on the `run`/CI verdict path — forbidden by prime
  directive 1 — or, without an LLM, produce false positives on every deliberately short
  restatement (the prime directives, in particular).
- **Delete every restatement and require every document to link out, with no exceptions.**
  Rejected for the shortest, load-bearing rules: a document that must be self-contained on
  first read (the "no omissions" rule `CLAUDE.md` already states) legitimately keeps a short,
  accurate copy of the prime directives rather than sending a first-time reader elsewhere for
  three sentences.
- **One new top-level "conventions.md" merging every norm.** Rejected: `docs/ai-development.md`
  already serves as the canonical home for AI-development-process norms, and `CLAUDE.md` for
  prime directives and the quick-reference gate; a third top-level document would fragment the
  norms further rather than consolidate them.

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

- Consolidated the norms in one pass (PR #1169): flat-layout fix in `CONTRIBUTING.md` /
  `AGENTS.md` / `.github/PULL_REQUEST_TEMPLATE.md`; the `make check` enumerations in
  `docs/ai-development.md`, `CONTRIBUTING.md`, `AGENTS.md`, and `docs/contributor-workflow-tutorial.md`
  now point at `CLAUDE.md`; `.github/claude-review-prompt.md`'s house-convention norms link their
  canonical homes; `roadmaps/README.md`'s BE-ID restatement reduced to a pointer; and the
  "don't restate it, link it" norm added to `docs/ai-development.md`. Japanese mirrors updated
  in step.

## References

- [BE-0159 — Flatten roadmap directory (retire status-driven folders)](../BE-0159-flatten-roadmap-status-folders/BE-0159-flatten-roadmap-status-folders.md)
- [BE-0278 — Tech-writing skill](../BE-0278-tech-writing-skill/BE-0278-tech-writing-skill.md)
- [BE-0113 — DESIGN.md realignment](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md)
- [BE-0069 — Executable contributor guardrails](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md)
- [BE-0065 — Docstring standard / API reference](../BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference.md)
