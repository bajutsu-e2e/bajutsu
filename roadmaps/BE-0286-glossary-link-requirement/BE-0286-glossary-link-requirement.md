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

[BE-0213](../BE-0213-glossary-and-docs-structure/BE-0213-glossary-and-docs-structure.md) stated a rule once. This proposal turns the rule into a standing convention.

A BE roadmap item or a page under `docs/` may use a term defined in [`docs/glossary.md`](../../docs/glossary.md) in its Bajutsu-specific sense. The first substantive mention of that term then links to the glossary entry. The text does not re-explain the term inline.

The proposal adds the rule to `CLAUDE.md` and to the *Documentation style* section of `docs/ai-development.md`. In `CLAUDE.md`, the rule sits next to the bilingual-docs and DESIGN.md-alignment norms. The proposal also backfills the `docs/` pages that define a glossary term without linking it.

## Motivation

BE-0213 built the glossary. Its Detailed design wrote: "existing pages should link a term's first mention to its glossary entry instead of re-explaining it inline."

That sentence described a one-time editorial choice for the PR that introduced the page. It lived in the design section of one item. It did not live in `CLAUDE.md` or `docs/ai-development.md`, where the working conventions of this project live. Future contributors had no rule to find and follow.

The resulting gap is close to total. It is not a handful of missed spots. The evidence covers two corpora.

**The `docs/` pages.** `docs/` holds 27 top-level pages, excluding `glossary.md` itself. Four of them link `glossary.md`: `overview.md`, `concepts.md`, `index.md`, and `README.md`. Every page that substantively explains a cluster of terms has zero links to the glossary:

- `drivers.md` explains driver, backend, actuator, and platform.
- `cli.md` explains target, app, device, and the command-line interface (CLI) verbs.
- `scenarios.md` explains scenario, step, precondition, and expect.
- `evidence.md` explains evidence and `capturePolicy`.
- `recording.md` explains Tier 1 and `goal`.
- `selectors.md` explains selector and identifier.

None of these pages points back to the page that defines those words. Four more pages show the same gap: `architecture.md`, `vision.md`, `getting-started/index.md`, and `getting-started/ios.md`. The `docs/ja/` mirror shows the gap term for term.

**The roadmap corpus.** The roadmap corpus is worse. It holds 277 BE items in each language. Only BE-0213, the item that created the glossary, references the glossary.

Other items use terms from the driver, backend, actuator, and platform cluster. They give no link to the definitions. The same holds for the target, app, and device cluster, for `trace` and `triage`, and for `capturePolicy`.

One page already gets this right. [`getting-started/web.md`](../../docs/getting-started/web.md) links `glossary.md` on the first mention of its vocabulary. This proposal wants every other page to read the same way. The page shows that the convention is straightforward to follow once a rule states it.

Left alone, the same gap recurs. BE-0213 began from a concrete drift, found by inventorying the docs side by side. `getting-started.md` called a scenario a "test". `scenarios.md` called the same thing "a scenario". No glossary caught that mismatch before it shipped.

The public docs site ([BE-0093](../BE-0093-public-docs-site/BE-0093-public-docs-site.md)) already publishes these pages. More pages and roadmap items keep arriving. A new page may re-explain `capturePolicy`, or the driver, backend, actuator, and platform cluster, in its own words. Such a page gives the explanation a fresh chance to drift from the canonical one. A page that links the settled explanation in the glossary gives no such chance.

A standing convention closes the gap from here on. The convention is stated where contributors and agents already look for writing rules.

## Detailed design

The work consists of two independent units.

1. **Add the convention to `CLAUDE.md` and `docs/ai-development.md`.**
   - Add it to the *Conventions* list of `CLAUDE.md`.
   - Add it to the *Documentation style (every document, both languages)* section of `docs/ai-development.md`.
   - Add the Japanese mirror to the corresponding section of `docs/ja/ai-development.md` (「ドキュメントの書き方（全ドキュメント、両言語に適用）」).
   - In `CLAUDE.md`, place the convention next to the existing bilingual-docs and DESIGN.md-alignment ([BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md)) bullets. These bullets already sit adjacent there.
   - In `docs/ai-development.md`, place it alongside the existing rules of that section: natural prose, no coined terms, spelling out acronyms, and 敬体. The section does not carry the bilingual-docs and BE-0113 adjacency. That pairing lives in `CLAUDE.md` alone.
   - Use this wording: "When prose in a BE roadmap item or a `docs/` page uses a term defined in `docs/glossary.md` in its Bajutsu-specific sense, its first substantive mention links to the term's glossary entry (`glossary.md#anchor` or `docs/ja/glossary.md#anchor`) rather than re-explaining the term inline."
   - State plainly that this is a review-time norm, not a CI gate. The reason matches the two neighboring norms in `CLAUDE.md`. An ordinary English word such as "step" or "target" may or may not invoke its Bajutsu-specific sense. Deciding which sense applies needs human judgment. Prime directive 1 keeps semantic judgment off the `run` / CI gate.
2. **Backfill the `docs/` pages that the inventory above identifies**, in both languages.
   - The pages are `drivers.md`, `cli.md`, `scenarios.md`, `evidence.md`, `recording.md`, `selectors.md`, `architecture.md`, `vision.md`, `getting-started/index.md`, and `getting-started/ios.md`.
   - Their `docs/ja/` mirrors get the same edit.
   - Apply the edits with a small one-off script. An agent editing each page by hand would cost more. The script keeps the mechanical part of the work cheap.
   - Author a manifest first. It holds one `(file, exact first-mention substring, glossary anchor)` triple per page. The triples come directly from the inventory that this proposal already produced.
   - Then run a short script. For each manifest entry, the script wraps the exact substring in a Markdown link to `glossary.md#anchor` (or `docs/ja/glossary.md#anchor`). The link goes on the first occurrence in the file.
   - Judgment about which mention is the substantive one is spent once, when authoring the manifest. Applying the manifest is a deterministic string replacement, not per-page reasoning.
   - Hand-check the resulting diff. An exact-substring match can hit the wrong occurrence when the spelling of a term repeats earlier on the page.
   - The script is a one-off implementation tool for this backfill. It is not a permanent addition to `make check`. The convention it applies stays the review-time norm from unit 1.

**Out of scope: the 277 existing roadmap items.** A sweep of the corpus in both languages would churn many files. Most of them are closed and `Implemented`. The benefit of a documentation nicety is far smaller than that diff. The convention from unit 1 governs new items from here on.

An item that someone edits for other reasons is a natural place to apply the convention. Examples are an `implement-be` run and a status flip. No item gets edited merely to add a glossary link retroactively.

## Alternatives considered

- **Enforce the rule with a lint script, folded into `make check`.** Rejected for now. Most glossary terms (`step`, `target`, `app`, `platform`) are also ordinary English words. A mechanical matcher would misfire constantly on prose that does not invoke the Bajutsu-specific sense. That noise would cost a reviewer more than the check would save. Try the norm first. If the norm proves too easy to forget, a lint check becomes a separately scoped follow-up.
- **Sweep all 277 existing roadmap items now, in both languages.** Rejected. Most of the corpus is closed historical record. Better cross-references inside those items do not justify a diff of that size. New and actively edited items get the convention. The rest stay as they are.
- **Fold this into BE-0213 as an addendum instead of drafting a new item.** Rejected. BE-0213 is `Implemented`. Its *Progress* checklist is complete for a finished, five-unit scope. A convention that applies to every future document reads better as its own item. A late addition to a proposal with a closed checklist reads worse.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] 1. Add the standing convention to `CLAUDE.md` and `docs/ai-development.md` (both languages)
- [x] 2. Backfill the identified `docs/` pages (English + `docs/ja/` mirrors) with a glossary link
      on first substantive mention of the term each page defines

**Log**

- 2026-07-17: both units landed.
  - Unit 1 adds the convention as a review-time norm to the *Conventions* list of `CLAUDE.md`, next to the bilingual-docs and BE-0113 norms.
  - Unit 1 also adds the convention to the *Documentation style* section of `docs/ai-development.md` and its `docs/ja/ai-development.md` mirror.
  - Unit 2 backfills a glossary link on the first substantive mention in each identified page.
  - The identified pages are `drivers.md`, `cli.md`, `scenarios.md`, `evidence.md`, `recording.md`, `selectors.md`, `architecture.md`, `vision.md`, `getting-started/index.md`, and `getting-started/ios.md`.
  - Every `docs/ja/` mirror gets the same link.

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
