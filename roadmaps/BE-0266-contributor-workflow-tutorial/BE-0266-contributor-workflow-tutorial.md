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

[`docs/roadmap-workflow.md`](../../docs/roadmap-workflow.md) explains *what* the `ideation` →
`implement-be` loop is and [`docs/ai-development.md`](../../docs/ai-development.md) explains the
mechanics in full (BE-ID lifecycle, model tiering, the three-skill triangle including
`propose-and-build`), but neither is a walkthrough: there is no single page that puts a
first-time contributor's hands on the keyboard and walks `/ideation` → merged proposal →
`/implement-be` → merged PR end to end, the way [`docs/getting-started.md`](../../docs/getting-started.md)
does for running Bajutsu itself. This proposal adds that walkthrough, plus the two things a
first-timer reaches for that the current reference pages don't provide: a worked example
contrasting a well-scoped proposal against an underspecified one, and a `propose-and-build`
section proportional to `ideation` / `implement-be`'s.

## Motivation

Three gaps surfaced when checking whether this exact contribution-workflow document already
existed (it mostly did, spread across three files):

1. **No single onboarding path.** `CONTRIBUTING.md` orients a human contributor and links out;
   `docs/roadmap-workflow.md` diagrams the ideation/implement-be loop; `docs/ai-development.md`
   holds the detailed rules (BE-ID mechanics, model tiers, PR templates). A newcomer has to
   assemble the actual "what do I type, in what order, to ship my first change" sequence
   themselves from three pages written for different purposes (orientation, conceptual overview,
   reference). [`docs/getting-started.md`](../../docs/getting-started.md) solved exactly this
   problem for *running* Bajutsu — install → scenario → run → report, one page, in order — and
   this proposal does the analogous thing for *contributing* to it.
2. **`propose-and-build` is a footnote.** `docs/roadmap-workflow.md`'s diagram and walkthrough
   cover only the two-skill loop (`ideation`, `implement-be`); `propose-and-build` gets one
   paragraph in `docs/ai-development.md`'s "Authoring and shipping roadmap items" section (its
   own [BE-0216](../BE-0216-propose-and-build-parallel-skill/BE-0216-propose-and-build-parallel-skill.md)
   documents the skill's mechanics, not when a contributor should reach for it over the serial
   path). A contributor deciding how to start a small, well-scoped feature has no walkthrough-level
   guidance on the trade-off, only a one-line rule of thumb.
3. **No worked example of what makes a proposal good or bad.** Every existing doc describes the
   *shape* a proposal must take (metadata block, MECE `Detailed design`, `Progress` checklist) but
   none shows a concrete before/after — a vague one-liner reshaped into a scoped, machine-checkable
   proposal — which is the fastest way for a newcomer to internalize "scoped enough" without first
   getting it wrong on a real PR.

Fixing these means a new contributor (human or agent, per this project's working agreement) reaches
their first merged proposal and first merged implementation without needing a maintainer to walk
them through it live.

## Detailed design

1. **A new tutorial page**, `docs/contributor-workflow-tutorial.md` (+ `docs/ja/` mirror), in the
   same tutorial register as `docs/getting-started.md` ("do these steps, in order" rather than
   "here is what each feature does"). It does not re-explain the BE-ID lifecycle, model tiers, or
   PR template — those stay owned by `docs/ai-development.md` and `docs/roadmap-workflow.md`,
   linked rather than duplicated — it walks a single concrete idea through:
   - Starting `/ideation`, grounding in the existing roadmap, and reaching a drafted `BE-0266`
     proposal.
   - Opening the proposal PR and watching CI (`roadmap-id`) allocate the real `BE-NNNN`.
   - Starting `/implement-be BE-NNNN` against the merged proposal, through plan-confirmation,
     implementation, the gate, and a merged PR.
2. **A worked good-vs-bad proposal example**, embedded in the tutorial page: a short, deliberately
   underspecified one-liner idea ("add retry to flaky steps"), followed by the reshaped version
   `ideation` should produce (bounded scope, which tier it touches, the machine-checkable outcome,
   surfaced tension with a prime directive if any) — reusing real, already-merged BE items (e.g.
   [BE-0214](../BE-0214-web-only-beginner-tutorial/BE-0214-web-only-beginner-tutorial.md) for a
   docs-shaped item, one code-shaped item) as the "here is what good looks like" reference rather
   than inventing hypothetical ones from scratch.
3. **A proportional `propose-and-build` section**: when to reach for it over the serial path (the
   existing rule of thumb from `docs/ai-development.md`, expanded with the walkthrough's own idea
   run through it hypothetically — same idea, "what if it had been small and settled enough to
   stack instead"), and what the hand-off (proposal merges → real `BE-NNNN` allocated →
   implementation branch rebases and retargets) looks like from the contributor's side.
4. **Cross-links, not duplication.** `CONTRIBUTING.md` (+ ja) points first-time contributors at
   this tutorial before the reference pages; `docs/roadmap-workflow.md` and
   `docs/ai-development.md` gain a "new to this? start with the tutorial" pointer at their top;
   `docs/README.md` / `docs/overview.md` reading order and `mkdocs.yml` nav include the new page
   next to `Getting started`.
5. **Bilingual pass.** `docs/ja/` mirror written as natural Japanese per the
   [`japanese-tech-writing`](../../.claude/skills/japanese-tech-writing/) skill, not a mechanical
   translation of the English walkthrough.

## Alternatives considered

- **Fold the walkthrough into `docs/roadmap-workflow.md` instead of a new page.** Rejected: that
  page is deliberately a conceptual overview (the loop diagram, why two skills not one) in the
  reference register; interleaving step-by-step tutorial prose into it would blur both purposes,
  the same reason `getting-started.md` and `overview.md` stay separate pages for the product itself.
- **Expand `docs/ai-development.md`'s existing propose-and-build paragraph in place, without a
  tutorial page.** Addresses gap 2 alone; leaves gaps 1 and 3 (no onboarding sequence, no worked
  example) unaddressed. This proposal's `Detailed design` item 3 folds the expanded
  `propose-and-build` guidance into the tutorial rather than leaving it stranded in the reference
  page.
- **Write the tutorial from a hypothetical idea instead of reusing real, merged BE items as the
  worked example.** Rejected: a hypothetical example can't show a reader the real proposal file,
  the real PR, or the real `Progress` log — reusing an already-merged item (like BE-0214) gives the
  reader something to click through and verify, which is exactly what a first-timer needs before
  trusting the shape of their own first proposal.

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

- (this PR) Added `docs/contributor-workflow-tutorial.md` (+ `docs/ja/` mirror) and cross-linked it
  from `CONTRIBUTING.md`/`.ja`, `docs/roadmap-workflow.md`, `docs/ai-development.md`,
  `docs/README.md`, `docs/overview.md` (both languages), and `mkdocs.yml` nav.

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
