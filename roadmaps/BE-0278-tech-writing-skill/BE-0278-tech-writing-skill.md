**English** · [日本語](BE-0278-tech-writing-skill-ja.md)

# BE-0278 — A unified technical-writing skill for BE items and prose docs

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0278](BE-0278-tech-writing-skill.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0278") |
| Implementing PR | [#1148](https://github.com/bajutsu-e2e/bajutsu/pull/1148) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

This proposal adds one authoritative prose norm — a `tech-writing` skill — that every BE item and
every prose document follows, in both English and Japanese. It distills a compact set of
language-agnostic writing techniques from Jeffrey Scott Vitter's *Structure + Style =
Communication* and pairs them with the English-specific mechanics that article settles. The
existing [`japanese-tech-writing`](../../.claude/skills/japanese-tech-writing/) skill becomes the
Japanese layer beneath it, so a single norm governs writing while each language keeps the guidance
that only its own grammar needs.

## Motivation

The project's writing rules are real but lopsided. Japanese prose has a deep, dedicated norm — the
[`japanese-tech-writing`](../../.claude/skills/japanese-tech-writing/) skill covers filler phrases,
redundancy, paragraph structure, argument rigor, and restraint of rhetorical flourish. English
prose has only the handful of bullets in
[`docs/ai-development.md`](../../docs/ai-development.md)'s *Documentation style* section (no coined
terms, no forced translation, no omissions, spell out acronyms). Nothing states, for English, how
to structure a sentence, where to place its emphasis, how to keep the verb near the subject, or
which filler words to cut. The result is asymmetric: the same author writing the two sides of one
BE item works against a detailed rulebook in Japanese and near-silence in English.

Vitter's article is a proven, concise source for exactly the gap on the English side, and much of
it is language-agnostic — the value of a top-down outline before drafting, the two stress points of
a sentence, subject–verb proximity, active voice, and the discipline of cutting filler apply to
Japanese prose just as well. Several of these already have Japanese counterparts in
`japanese-tech-writing` (its filler-word and redundancy rules), which means the two norms should
share one language-agnostic core rather than restate it twice and drift apart.

BE items are the concrete beneficiary. Each is a bilingual, argued document — an Introduction that
must state a contribution up front, a Motivation that must move from the known problem to the new
result, a Detailed design that must read cleanly in both languages. A shared norm raises the floor
for every future item and gives review one place to point to, the same role BE-0065 gave docstrings
and BE-0074 gave the item template.

## Detailed design

The work breaks down into four independent units.

1. **A new `tech-writing` skill** at `.claude/skills/tech-writing/SKILL.md`, in two parts:
   - **Language-agnostic principles (both languages).** Distilled from Vitter:
     - *Version-by-version refinement.* Draft top-down: a rough outline of points in order first,
       then section titles and each section's main idea, then subideas and a story line, then the
       near-final prose. Do not draft a long argued section linearly from the first sentence.
     - *Clarity and simplicity.* On review, cut words, phrases, and sentences that carry no meaning.
     - *State the contribution up front.* A BE item's Introduction and Motivation state the problem,
       the background, the contribution, and why it matters — before the detail. A reader who stops
       after the first two paragraphs should still have the point.
     - *The two stress points of a sentence are its beginning and its end.* Reserve the end for the
       most important element; move from the known to the unknown, from background to result. Put
       transitional and background words ("Therefore", "However", "In the last section") at the
       front, saving the content for the end.
     - *Keep the verb close to the subject.* A long clause wedged between them makes a sentence hard
       to parse; rewrite so the subject's verb arrives early.
     - *Active voice, first-person plural.* Prefer the active voice; write as "we", not "I", in a
       proposal.
     - *Cut filler.* Drop words and phrases that add no meaning — "actually", "basically",
       "essentially", "it is important to note that", "note that", a bare "now". Keep "thus" /
       "therefore" only where they genuinely signal a conclusion.
   - **English-specific mechanics (English prose only).** From Vitter's grammar-and-style section:
     "many" / "much" over "a lot of"; spell out contractions; reserve symbols and abbreviations for
     formulas and spell out the word in text; the serial (Oxford) comma in a list of three or more;
     *that* (restrictive, no comma) versus *which* (non-restrictive, set off by commas); no bare
     "this" / "that" as a noun (say "this X"); avoid singular "they" for an unspecified single
     person — reword to the plural or a role; hyphen for a compound adjective, en-dash for a range,
     em-dash for emphasis (with surrounding spaces — this repo's own convention; Chicago itself
     closes them up); spell out a positive whole number
     below ten used as an adjective, numeral when used as a noun; a colon only after a complete
     sentence. A short source note (an HTML comment) credits Vitter, mirroring how
     `japanese-tech-writing` credits its own source.
2. **The layering with `japanese-tech-writing`.** The new skill declares itself the umbrella and
   names `japanese-tech-writing` as the Japanese layer: when writing Japanese prose, apply both, and
   where the two overlap (filler, redundancy, restraint), the Japanese skill's specific wording
   governs. `japanese-tech-writing` gains one line at the top pointing up to `tech-writing` as the
   shared language-agnostic norm, so a reader arriving at either finds the other. No Japanese rule
   changes; the English mechanics never apply to Japanese (Japanese has its own dash and punctuation
   rules, already in that skill).
3. **Reference wiring, no duplication.** Point the existing homes at the new skill instead of
   copying its contents:
   - [`docs/ai-development.md`](../../docs/ai-development.md)'s *Documentation style* section names
     `tech-writing` as the authoritative prose norm for both languages, beside its current
     `japanese-tech-writing` pointer.
   - [`CLAUDE.md`](../../CLAUDE.md)'s *Conventions* gains a short-form pointer, beside the existing
     `japanese-tech-writing` line.
   - [`roadmaps/README.md`](../../roadmaps/README.md) (and `README-ja.md`) note in the BE-authoring
     section that items follow `tech-writing`, next to the existing 敬体 / `japanese-tech-writing`
     note.
   - The authoring skills that draft BE prose point at `tech-writing` at the moment they write:
     [`ideation`](../../.claude/skills/ideation/SKILL.md) (its drafting step) and the Phase A of
     [`propose-and-build`](../../.claude/skills/propose-and-build/SKILL.md) invoke it before drafting,
     beside their existing `japanese-tech-writing` pointer, so the norm is active at authoring time —
     not merely discoverable in the docs.
4. **Scope: BE items and every prose document, both languages.** The norm applies to
   `roadmaps/BE-*/*.md` and `*-ja.md` and to every document under `docs/` (and its `docs/ja/`
   mirror), consistent with the *Documentation style* section's existing reach ("including roadmap
   items"). It does not extend to code docstrings, which keep their own separate standard (BE-0065).

**Enforcement stays a review-time norm, not a CI gate.** Judging whether prose actually reads
clearly, leads with its contribution, or cut its filler needs semantic judgment. Putting that on
the gate would put an LLM on the `run` / CI verdict path, which prime directive 1 forbids. So this
norm holds exactly the way the bilingual-docs rule and the DESIGN.md-realignment rule (BE-0113) hold
— a contributor and reviewer expectation, checked by people, not a machine-checkable assertion.

## Alternatives considered

- **Enrich the *Documentation style* bullets in place, with no dedicated skill.** Rejected: it would
  leave the asymmetry it is trying to fix. Japanese guidance lives in a rich skill the author is told
  to invoke *before* writing; folding the English techniques into a few doc bullets keeps them as
  passive reference the author reads *after*. A skill is the mechanism the repo already uses to make a
  norm active at authoring time.
- **A standalone `english-tech-writing` skill mirroring `japanese-tech-writing`, with no umbrella.**
  Rejected: the two would restate the language-agnostic core (outline first, cut filler, sentence
  stress) twice and drift apart over time. A single umbrella that owns the shared principles and
  defers to each language for its own mechanics keeps one source of truth.

  **Adopted after all ([#1157](https://github.com/bajutsu-e2e/bajutsu/pull/1157)).** The English
  mechanics were later split into an `english-tech-writing` skill — but *with* the umbrella, not
  without it, which sidesteps the drift the rejection was about: `tech-writing` still owns the
  shared language-agnostic core and states it once, while `english-tech-writing` and
  `japanese-tech-writing` are now symmetric layers that each hold only their own language's
  mechanics and defer up to it. The trigger was textlint gaining shared English-prose rules under
  `tech-writing`: with a machine check now covering English too, keeping the English mechanics
  inside the umbrella (while Japanese had its own layer) was the asymmetry worth removing.
- **Enforce a subset in CI (a prose linter for filler words, "a lot of", etc.).** Rejected on the
  prime directive: a lexical blocklist is machine-checkable, but the norm's substance (clarity,
  emphasis, argument order) is not, and a partial lint invites treating the unlintable majority as
  optional. The norm is a review expectation, matching how every other prose rule in the repo holds.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] 1. New `tech-writing` skill (language-agnostic principles + English mechanics + source note)
- [x] 2. Layering with `japanese-tech-writing` (umbrella declaration + reciprocal pointer)
- [x] 3. Reference wiring in `docs/ai-development.md`, `CLAUDE.md`, `roadmaps/README.md` (+ `-ja`), and
  the BE-authoring skills (`ideation`, `propose-and-build`) so the norm is active at authoring time
- [x] 4. Scope statement (BE items + `docs/`, both languages; docstrings excluded)

Log:

- The `tech-writing` skill, its `japanese-tech-writing` layering, and the wiring in
  `docs/ai-development.md` / `CLAUDE.md` / `roadmaps/README.md` (+ `-ja`) landed in
  [#1138](https://github.com/bajutsu-e2e/bajutsu/pull/1138), before this item was numbered.
- This PR completes the item: it wires `tech-writing` into the BE-authoring skills (`ideation`,
  `propose-and-build`), so the norm is invoked at drafting time, and flips the item to Implemented.
- [#1157](https://github.com/bajutsu-e2e/bajutsu/pull/1157) later refined the shape: the English
  mechanics moved out of `tech-writing` into a new `english-tech-writing` layer, making the two
  language layers symmetric under the umbrella (see *Alternatives considered*). `tech-writing`
  stays the single owner of the language-agnostic core.

## References

- Jeffrey Scott Vitter, *Structure + Style = Communication* (The University of Kansas, 2011) — the
  source these techniques are distilled from.
- [`japanese-tech-writing`](../../.claude/skills/japanese-tech-writing/SKILL.md) — the Japanese layer
  this skill sits above; the precedent for a dedicated, invoked-before-writing prose norm.
- [`docs/ai-development.md`](../../docs/ai-development.md) — the *Documentation style* section this
  skill becomes the authoritative reference for.
- [BE-0065](../BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference.md)
  — the docstring standard; the companion norm this one deliberately does not touch.
- [BE-0074](../BE-0074-be-template-standardization/BE-0074-be-template-standardization.md) — the BE
  item template this norm raises the writing floor for.
- [BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md) — the precedent for
  a documentation norm that holds at review time rather than on the CI gate.
