**English** · [日本語](BE-0266-contributor-workflow-tutorial-ja.md)

# BE-0266 — Contributor workflow tutorial: a hands-on guide to ideation / implement-be / propose-and-build

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0266](BE-0266-contributor-workflow-tutorial.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0266") |
| Implementing PR | [#1072](https://github.com/bajutsu-e2e/bajutsu/pull/1072) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

Two existing pages describe the `ideation` → `implement-be` loop, and neither is a walkthrough.

- [`docs/roadmap-workflow.md`](../../docs/roadmap-workflow.md) explains *what* the loop is.
- [`docs/ai-development.md`](../../docs/ai-development.md) explains the mechanics in full: the
  BE-ID lifecycle, model tiering, and the three-skill triangle that includes `propose-and-build`.

No single page puts a first-time contributor's hands on the keyboard and walks `/ideation` → merged
proposal → `/implement-be` → merged PR end to end. [`docs/getting-started.md`](../../docs/getting-started.md)
does this for running Bajutsu itself.

This proposal adds that walkthrough. It also adds the two things that a first-timer reaches for and
the current reference pages do not provide:

- a worked example that contrasts a well-scoped proposal with an underspecified one
- a `propose-and-build` section proportional to the `ideation` and `implement-be` sections

## Motivation

We checked whether this contribution-workflow document already existed. It mostly did, spread across
three files. The check surfaced three gaps.

1. **No single onboarding path.**
   - `CONTRIBUTING.md` orients a human contributor and links out.
   - `docs/roadmap-workflow.md` diagrams the ideation and implement-be loop.
   - `docs/ai-development.md` holds the detailed rules: BE-ID mechanics, model tiers, and PR
     templates.

   A newcomer has to assemble the actual sequence themselves: "what do I type, in what order, to
   ship my first change". The three pages were written for different purposes (orientation,
   conceptual overview, reference).
   [`docs/getting-started.md`](../../docs/getting-started.md) solved exactly this problem for
   *running* Bajutsu, on one page and in order: install → scenario → run → report. This proposal does
   the analogous thing for *contributing* to Bajutsu.
2. **`propose-and-build` is a footnote.**
   - The diagram and walkthrough in `docs/roadmap-workflow.md` cover only the two-skill loop
     (`ideation`, `implement-be`).
   - `propose-and-build` gets one paragraph in the "Authoring and shipping roadmap items" section of
     `docs/ai-development.md`.
   - Its own [BE-0216](../BE-0216-propose-and-build-parallel-skill/BE-0216-propose-and-build-parallel-skill.md)
     documents the skill's mechanics, not when a contributor should reach for it over the serial
     path.

   A contributor deciding how to start a small, well-scoped feature has only a one-line rule of
   thumb on the trade-off, with no walkthrough-level guidance.
3. **No worked example of what makes a proposal good or bad.** Every existing document describes the
   *shape* a proposal must take: the metadata block, a MECE `Detailed design`, and the `Progress`
   checklist. None shows a concrete before and after, in which a vague one-liner is reshaped into a
   scoped, machine-checkable proposal. A before-and-after example is the fastest way for a newcomer
   to internalize "scoped enough" without first getting it wrong on a real PR.

Fixing these gaps lets a new contributor reach a first merged proposal and a first merged
implementation without a maintainer walking them through the process live. The contributor may be
human or agent, per this project's working agreement.

## Detailed design

1. **A new tutorial page**, `docs/contributor-workflow-tutorial.md` (plus a `docs/ja/` mirror). It
   uses the same tutorial register as `docs/getting-started.md`: "do these steps, in order" rather
   than "here is what each feature does". The page does not re-explain the BE-ID lifecycle, model
   tiers, or the PR template. `docs/ai-development.md` and `docs/roadmap-workflow.md` keep ownership
   of those topics, and the page links to them instead of duplicating them. The page walks a single
   concrete idea through three stages:
   - Starting `/ideation`, grounding in the existing roadmap, and reaching a drafted `BE-0266`
     proposal.
   - Opening the proposal PR and watching CI (`roadmap-id`) allocate the real `BE-NNNN`.
   - Starting `/implement-be BE-NNNN` against the merged proposal, through plan confirmation,
     implementation, the gate, and a merged PR.
2. **A worked good-versus-bad proposal example**, embedded in the tutorial page.
   - It opens with a short, deliberately underspecified one-liner idea ("add retry to flaky
     steps").
   - It then shows the reshaped version that `ideation` should produce: bounded scope, which tier the
     change touches, the machine-checkable outcome, and any surfaced tension with a prime directive.
   - It reuses real, already-merged BE items as the "here is what good looks like" reference, rather
     than inventing hypothetical ones from scratch. One example is
     [BE-0214](../BE-0214-web-only-beginner-tutorial/BE-0214-web-only-beginner-tutorial.md), for a
     docs-shaped item. Another is one code-shaped item.
3. **A proportional `propose-and-build` section.**
   - It explains when to reach for `propose-and-build` over the serial path. It starts from the
     existing rule of thumb in `docs/ai-development.md`. It expands the rule by running the
     walkthrough's own idea through it hypothetically: the same idea, as if it had been small and
     settled enough to stack instead.
   - It shows what the hand-off looks like from the contributor's side: the proposal merges, the real
     `BE-NNNN` is allocated, and the implementation branch rebases and retargets.
4. **Cross-links, not duplication.**
   - `CONTRIBUTING.md` (plus the Japanese version) points first-time contributors at this tutorial
     before the reference pages.
   - `docs/roadmap-workflow.md` and `docs/ai-development.md` gain a "new to this? start with the
     tutorial" pointer at their top.
   - The reading order in `docs/README.md` and `docs/overview.md`, and the `mkdocs.yml` nav, include
     the new page next to `Getting started`.
5. **Bilingual pass.** The `docs/ja/` mirror is written as natural Japanese, per the
   [`japanese-tech-writing`](../../.claude/skills/japanese-tech-writing/) skill. It is not a
   mechanical translation of the English walkthrough.

## Alternatives considered

- **Fold the walkthrough into `docs/roadmap-workflow.md` instead of a new page.** Rejected. That page
  is deliberately a conceptual overview in the reference register: the loop diagram, and why two
  skills rather than one. Interleaving step-by-step tutorial prose would blur both purposes. The same
  reason keeps `getting-started.md` and `overview.md` separate pages for the product itself.
- **Expand `docs/ai-development.md`'s existing propose-and-build paragraph in place, without a
  tutorial page.** This addresses gap 2 alone. It leaves gaps 1 and 3 (no onboarding sequence, no
  worked example) unaddressed. Item 3 of this proposal's `Detailed design` folds the expanded
  `propose-and-build` guidance into the tutorial, so it is not stranded in the reference page.
- **Write the tutorial from a hypothetical idea instead of reusing real, merged BE items as the
  worked example.** Rejected. A hypothetical example cannot show a reader the real proposal file, the
  real PR, or the real `Progress` log. Reusing an already-merged item (like BE-0214) gives the reader
  something to click through and verify. A first-timer needs that before trusting the shape of their
  own first proposal.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] 1. Write `docs/contributor-workflow-tutorial.md`: the ideation → merged proposal →
      implement-be → merged PR walkthrough
- [x] 2. Add the worked good-vs-bad proposal example, referencing real merged BE items
- [x] 3. Expand `propose-and-build` coverage to be proportional to `ideation` / `implement-be`
- [x] 4. Cross-link from `CONTRIBUTING.md`, `docs/roadmap-workflow.md`, `docs/ai-development.md`,
      `docs/README.md` / `docs/overview.md`, and `mkdocs.yml` nav
- [x] 5. Bilingual pass (`docs/ja/` mirror), per the `japanese-tech-writing` skill

**Log**

- (this PR) Added `docs/contributor-workflow-tutorial.md` (plus a `docs/ja/` mirror). Cross-linked the
  page from `CONTRIBUTING.md` and its Japanese version, `docs/roadmap-workflow.md`,
  `docs/ai-development.md`, `docs/README.md`, `docs/overview.md` (both languages), and the
  `mkdocs.yml` nav.

## References

- [`docs/getting-started.md`](../../docs/getting-started.md) — the tutorial register and
  step-by-step structure this proposal follows for the contribution workflow
- [`docs/roadmap-workflow.md`](../../docs/roadmap-workflow.md) — the conceptual overview this
  tutorial links to rather than duplicates
- [`docs/ai-development.md`](../../docs/ai-development.md) — the detailed rules (BE-ID mechanics,
  model tiers, PR template, the three-skill triangle) this tutorial links to rather than duplicates
- [`CONTRIBUTING.md`](../../CONTRIBUTING.md) — the human-contributor entry point this tutorial is
  cross-linked from
- [BE-0216](../BE-0216-propose-and-build-parallel-skill/BE-0216-propose-and-build-parallel-skill.md) —
  the `propose-and-build` skill this tutorial gives proportional, contributor-facing coverage to
- [BE-0214](../BE-0214-web-only-beginner-tutorial/BE-0214-web-only-beginner-tutorial.md) — precedent
  for a tutorial-shaped roadmap item in this same topic, and a candidate real example for the
  worked good-vs-bad proposal walkthrough
