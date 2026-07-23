**English** · [日本語](BE-0286-glossary-link-requirement-ja.md)

# BE-0286 — Link glossary terms to their definitions on first use

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0286](BE-0286-glossary-link-requirement.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0286") |
| Implementing PR | [#1179](https://github.com/bajutsu-e2e/bajutsu/pull/1179) |
| Topic | Contributor workflow |
| Related | [BE-0213](../BE-0213-glossary-and-docs-structure/BE-0213-glossary-and-docs-structure.md) |
<!-- /BE-METADATA -->

## Introduction

[BE-0213](../BE-0213-glossary-and-docs-structure/BE-0213-glossary-and-docs-structure.md) stated a
rule once. This proposal turns it into a standing convention: when a BE roadmap item or a page
under `docs/` uses a term defined in [`docs/glossary.md`](../../docs/glossary.md) in its
Bajutsu-specific sense, its first substantive mention links to the term's glossary entry instead of
re-explaining the term inline. It adds that rule to `CLAUDE.md` and `docs/ai-development.md`'s
*Documentation style* section, next to the bilingual-docs and DESIGN.md-alignment norms those
documents already carry. It also backfills the `docs/` pages that define a glossary term without
linking it.

## Motivation

BE-0213 built the glossary and, in its own Detailed design, wrote "existing pages should link a
term's first mention to its glossary entry instead of re-explaining it inline." That sentence
described a one-time editorial choice for the PR that introduced the page. It lived inside one
item's design section, not in `CLAUDE.md` or `docs/ai-development.md`, where this project's working
conventions actually live — so it was never a rule future contributors could find and follow.

The resulting gap is close to total, not a handful of missed spots. Of the 27 top-level pages
under `docs/` (excluding `glossary.md` itself), only four — `overview.md`, `concepts.md`,
`index.md`, and `README.md` — link `glossary.md` at all. Every page that substantively explains a
term cluster has zero links to the glossary. `drivers.md` explains driver / backend / actuator /
platform. `cli.md` explains target / app / device and the CLI verbs. `scenarios.md` explains
scenario / step / precondition / expect. `evidence.md` explains evidence and `capturePolicy`.
`recording.md` explains Tier 1 and `goal`. `selectors.md` explains selector / identifier. None of
these pages points back to the page that defines those words. `architecture.md`, `vision.md`, and
both `getting-started/index.md` and `getting-started/ios.md` show the same gap, and `docs/ja/`
mirrors it term for term.

The roadmap corpus is worse. Of 277 BE items in each language, only BE-0213 itself — the item that
created the glossary — references it. Every other item that uses a driver / backend / actuator /
platform, target / app / device, `trace` / `triage`, or `capturePolicy` term does so with no link
to its definition. One page already gets this right:
[`getting-started/web.md`](../../docs/getting-started/web.md) links `glossary.md` on first mention
of its vocabulary, in the way this proposal wants every other page to read. That page is proof the
convention is simple to follow once it is written down as a rule.

Left alone, the same gap recurs. BE-0213's own motivation was a concrete drift it found by inventorying the
docs side by side: `getting-started.md` called a scenario a "test," while `scenarios.md` called the
same thing "a scenario." No glossary caught that mismatch before it shipped. The public docs site
([BE-0093](../BE-0093-public-docs-site/BE-0093-public-docs-site.md)) already publishes these pages.
More pages and more roadmap items keep arriving. Each new page that re-explains `capturePolicy` or
the driver/backend/actuator/platform cluster in its own words, rather than linking the glossary's
already-settled explanation, is a fresh chance for that explanation to drift from the canonical one.
A standing convention, stated where contributors and agents already look for writing rules, closes
that gap from here on.

## Detailed design

The work is two independent units:

1. **Add the convention to `CLAUDE.md`'s *Conventions* list and to `docs/ai-development.md`'s
   *Documentation style (every document, both languages)* section**, with the Japanese mirror in
   `docs/ja/ai-development.md`'s corresponding section (「ドキュメントの書き方（全ドキュメント、両言語に
   適用）」). In `CLAUDE.md`, add it next to the existing bilingual-docs and DESIGN.md-alignment
   ([BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md)) bullets, which
   already sit adjacent there. In `docs/ai-development.md`, add it alongside that section's existing
   rules (natural prose, no coined terms, spelling out acronyms, 敬体) — this section does not
   itself carry the bilingual-docs/BE-0113 adjacency; that pairing lives only in `CLAUDE.md`.
   Wording: "When prose in a BE roadmap item or a `docs/` page uses a term defined in
   `docs/glossary.md` in its Bajutsu-specific sense, its first substantive mention links to the
   term's glossary entry (`glossary.md#anchor` or `docs/ja/glossary.md#anchor`) rather than
   re-explaining the term inline." State plainly that this is a review-time norm, not a CI gate. The
   reason matches the two neighboring `CLAUDE.md` norms: deciding whether an ordinary English word
   like "step" or "target" invokes its Bajutsu-specific sense needs human judgment, and prime
   directive 1 keeps semantic judgment off the `run` / CI gate.
2. **Backfill the `docs/` pages the inventory above identifies**, in both languages: `drivers.md`,
   `cli.md`, `scenarios.md`, `evidence.md`, `recording.md`, `selectors.md`, `architecture.md`,
   `vision.md`, `getting-started/index.md`, and `getting-started/ios.md`, plus their `docs/ja/`
   mirrors. Apply the edits with a small one-off script, not an agent editing each page by hand, to
   keep the mechanical part of the work cheap. Author a manifest first: one `(file, exact
   first-mention substring, glossary anchor)` triple per page, drawn directly from the inventory
   this proposal already produced. Then run a short script that, for each manifest entry, wraps that
   exact substring in a Markdown link to `glossary.md#anchor` (or `docs/ja/glossary.md#anchor`) on
   its first occurrence in the file. Judgment about which mention is the substantive one is spent
   once, authoring the manifest. Applying the manifest is a deterministic string replacement, not
   per-page reasoning. Hand-check the resulting diff: an exact-substring match can hit the wrong
   occurrence when a term's spelling repeats earlier on the page. The script is a one-off
   implementation tool for this backfill, not a permanent addition to `make check` — the convention
   it applies stays the review-time norm from unit 1.

Out of scope: the 277 existing roadmap items. Sweeping almost the entire corpus, in both languages,
to add a glossary link would churn many files — most of them closed and `Implemented` — for a
documentation nicety whose benefit is far smaller than its diff. The convention from unit 1 governs
new items from here on. An item already being edited for other reasons (an `implement-be` run, a
status flip) is a natural place to apply the convention, but no item gets edited just to add a
glossary link retroactively.

## Alternatives considered

- **Enforce the rule with a lint script, folded into `make check`.** Rejected for now. Most glossary
  terms — `step`, `target`, `app`, `platform` — are also ordinary English words. A mechanical
  matcher would misfire constantly on prose that does not invoke the Bajutsu-specific sense, and
  that noise would cost a reviewer more than the check would save. Try the norm first. If it proves
  too easy to forget in practice, a lint check becomes its own, separately scoped follow-up.
- **Sweep all 277 existing roadmap items now, in both languages.** Rejected. Most of the corpus is
  closed historical record, so clearer cross-references inside those items do not justify a diff
  that size. New and actively edited items get the convention; the rest stay as they are.
- **Fold this into BE-0213 as an addendum instead of drafting a new item.** Rejected: BE-0213 is
  `Implemented`, with a completed *Progress* checklist describing a finished, five-unit scope. A
  convention that applies indefinitely to every future document reads more clearly as its own item
  than as a late addition bolted onto a proposal whose own checklist is already closed.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] 1. Add the standing convention to `CLAUDE.md` and `docs/ai-development.md` (both languages)
- [x] 2. Backfill the identified `docs/` pages (English + `docs/ja/` mirrors) with a glossary link
      on first substantive mention of the term each page defines

**Log**

- 2026-07-17 — Both units landed. Unit 1 adds the convention as a review-time norm to `CLAUDE.md`'s
  *Conventions* list (next to the bilingual-docs and BE-0113 norms) and to the *Documentation style*
  section of `docs/ai-development.md` and its `docs/ja/ai-development.md` mirror. Unit 2 backfills a
  glossary link on the first substantive mention in `drivers.md`, `cli.md`, `scenarios.md`,
  `evidence.md`, `recording.md`, `selectors.md`, `architecture.md`, `vision.md`,
  `getting-started/index.md`, and `getting-started/ios.md`, plus every `docs/ja/` mirror.

## References

- [BE-0213](../BE-0213-glossary-and-docs-structure/BE-0213-glossary-and-docs-structure.md) — built
  `docs/glossary.md` and first stated the link-on-first-mention rule this proposal makes standing
- [`docs/glossary.md`](../../docs/glossary.md) · [`docs/ja/glossary.md`](../../docs/ja/glossary.md)
  — the page every link in this proposal points to
- [`docs/ai-development.md`](../../docs/ai-development.md) — *Documentation style* section this
  proposal's convention is added to, next to the bilingual-docs and DESIGN.md-alignment norms
- [BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md) — the
  DESIGN.md-alignment norm this proposal's enforcement status (review-time, not a CI gate) follows
- [`docs/getting-started/web.md`](../../docs/getting-started/web.md) — the one page that already
  follows this convention, cited as the model for the backfill
