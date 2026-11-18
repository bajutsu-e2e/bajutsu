**English** · [日本語](BE-0383-motivation-verifiable-outcome-ja.md)

# BE-0383 — Require a verifiable outcome in every BE item's Motivation

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0383](BE-0383-motivation-verifiable-outcome.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0383") |
| Implementing PR | [#1691](https://github.com/bajutsu-e2e/bajutsu/pull/1691) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

This item extends the contribution-first principle of [`document-writing`][document-writing]. A BE
item's Motivation then names not only what should change, but also how a reader would later judge
whether the change delivered.

Today the norm requires stating the contribution up front. It does not require stating a way to check
it. GitHub's [Spec Kit][spec-kit] closes exactly this gap for its own specifications. It requires a
measurable success criterion.

This item imports one piece of that practice: a verifiable-outcome sentence in Motivation. It imports
nothing else. *Alternatives considered* records why the rest of Spec Kit does not fit the roadmap
format of Bajutsu or its review contract.

## Motivation

A BE item is argued prose. Motivation makes the case for a change, and `document-writing` already
requires that case to state its contribution before its detail. The norm does not require a way to
tell, once the change ships, whether the contribution actually arrived.

[BE-0347][] shows the gap. It argues that splitting a review pass into two roles "should let each
round close a finding instead of deferring it". It also argues that the split "should cut down how
often the live bot's own re-review cycle even starts". Both claims are plausible. Neither Motivation
nor the *Progress* checklist of the item states what a reader could check, after the fact, to confirm
that either one happened.

*Progress* tracks whether the units of work landed. It does not track whether the change produced the
effect that Motivation predicted. Those are two different questions, and only the first has a place to
live today.

GitHub's Spec Kit is built around closing this same gap for its own specifications. The success
criteria of a `spec.md` must be "measurable" and "technology-agnostic". A requirement such as
"checkout completes in under three minutes" gives every later reader a fact to check against the
shipped result.

Bajutsu cannot import the technology-agnostic half of that requirement. The Detailed design of a BE
item is expected to name concrete files, functions, and mechanisms. A Spec Kit `spec.md` must avoid
implementation detail entirely.

The measurability half transfers cleanly. Asking an author to name one observable difference that the
change is meant to produce costs nothing extra at authoring time, because the author already knows why
the change matters. The sentence gives every later reader a concrete fact to check the claim against,
instead of the bare say-so of the prose. The later readers are:

- a reviewer
- a future contributor revisiting the item
- the author, checking a prediction after the implementing PR lands

This item states its own verifiable outcome, to meet the norm it proposes. A reader of any
later-merged item can hold the Motivation of that item against the shipped change and say whether the
predicted effect arrived. The two predictions of BE-0347 above leave a reader nothing to hold them
against.

## Detailed design

1. **[`document-writing`][document-writing]: add a verifiable-outcome bullet.** In the
   *Language-agnostic principles* section, add a bullet next to "State the contribution up front".
   The bullet says that the Motivation of a BE item names one observable difference the change is
   meant to produce: a change in behavior, in output, or in a quantity a reader could check. The
   outcome is distinct from the *Progress* checklist, which tracks completed work, not delivered
   effect.
   - The outcome does not need a numeric target. "The local self-review pass converges before a push,
     instead of the live bot's cycle restarting on unrelated findings" is as valid as a number would
     be, as long as a later reader could tell whether it held.
   - State the new bullet as a BE-specific instance of the existing principle, not as a new, separate
     principle. The existing "State the contribution up front" bullet already carries a BE-specific
     clause ("A BE item's Introduction and Motivation state the problem..."). BE-specific guidance is
     already at home there.
2. **[`ideation`][ideation] step 2: ask for the verifiable outcome during the conversation.** The step
   already asks "what's the machine-checkable outcome". That is the deterministic assertion a feature
   is gated on, which is a different question from this one. Add one clause, worded so the two do not
   collide. The clause also asks what the author would point to, after the change ships, to say it
   worked. Step 4 then expects Motivation to answer the same question in prose. The clause keeps the
   question inside the existing back-and-forth, without adding a new step. The norm of unit 1 makes the
   answer mandatory in the drafted prose, not this step.
3. **[`ideation`][ideation] step 4: carry the answer into the draft.** The step already instructs the
   author to invoke `document-writing` "before you draft that prose". Add one sentence: when filling
   the `TBD` of Motivation, include the verifiable outcome from the conversation in step 2. This is the
   only change needed here. Unit 1 supplies the standing rule, and unit 2 supplies the conversation
   that produces the answer. This unit points the drafting step at using the answer.

No change reaches [`propose-and-build`][propose-and-build] or
[`.github/claude-review-prompt.md`][claude-review-prompt].

- `propose-and-build` Phase A already invokes `document-writing` by reference (per [BE-0278][]). It
  inherits unit 1 with no separate edit.
- The severity floor of the review contract is deliberately functional-impact-only (see *Alternatives
  considered*). This item leaves the contract untouched.

## Alternatives considered

**Add a "success criteria are unmeasurable" check to the prose-quality lens of
`.github/claude-review-prompt.md`.** Rejected.

- The severity floor of the contract was deliberately narrowed twice, to functional impact only, after
  the prose lenses proved too strict in practice.
- Whether Motivation names a verifiable outcome is a question about content, not wording. It fits
  neither of the two prose lenses that the contract keeps. "Japanese prose quality" and "English
  documentation and roadmap prose quality" both judge how something is phrased, not what claim it
  makes.
- Widening the contract to catch this would reopen the exact churn that [BE-0347][] narrowed the
  contract to avoid. This item can instead enforce the norm for free, in the same authoring pass that
  already invokes `document-writing`.

**Adopt the `[NEEDS CLARIFICATION]` marker of Spec Kit for an unresolved design point.** Rejected.

- The marker exists because the specification-writing step of Spec Kit runs without the stakeholder
  present. An unresolved question has nowhere to go until a later, separate review step reads the
  marker.
- In `ideation`, the author and the stakeholder are in the same conversation. Step 2 resolves an
  unformed point through the back-and-forth itself, before step 4 ever drafts prose.
- A marker meant to survive until a later reader has no gap here to fill.

**Adopt the three-document split of Spec Kit (`spec.md` / `plan.md` / `tasks.md`), or its
`constitution.md` gate.** Rejected for both.

- A BE item is a single file. Its Detailed design is expected to carry implementation detail: file
  names, functions, and mechanisms. That is exactly what a Spec Kit `spec.md` must exclude by design.
  Splitting the "what and why" of Motivation from the "how" of Detailed design into separate documents
  would fight the format that every existing BE item already uses.
- A `constitution.md`-style gate already exists in a different shape: the prime directives
  [step](../../.apm/skills/ideation/SKILL.md#prime-directives-these-bound-every-idea) of `ideation`,
  backed by `CLAUDE.md` and `DESIGN.md`. Adding a second, Spec-Kit-shaped gate on top would duplicate
  the existing gate, not close a gap.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — add the verifiable-outcome bullet to `document-writing`.
- [x] Unit 2 — `ideation` step 2 asks for the verifiable outcome during the conversation.
- [x] Unit 3 — `ideation` step 4 carries the answer into the drafted Motivation.

## References

- [GitHub Spec Kit][spec-kit] and its [`spec-driven.md`](https://github.com/github/spec-kit/blob/main/spec-driven.md)
  — the source of the measurable-success-criterion practice this item imports one piece of.
- [`document-writing`][document-writing] — the norm this item adds a bullet to.
- [`ideation`][ideation] — the skill whose steps 2 and 4 this item updates.
- [BE-0278][] — the item that created the unified writing norm this one extends.
- [BE-0347][] — the item whose own Motivation this item's *Motivation* section uses as a worked
  example of an unverified claim.

[document-writing]: ../../.apm/skills/document-writing/SKILL.md
[ideation]: ../../.apm/skills/ideation/SKILL.md
[propose-and-build]: ../../.apm/skills/propose-and-build/SKILL.md
[claude-review-prompt]: ../../.github/claude-review-prompt.md
[spec-kit]: https://github.com/github/spec-kit
[BE-0278]: ../BE-0278-tech-writing-skill/BE-0278-tech-writing-skill.md
[BE-0347]: ../BE-0347-bounded-ci-review-cycle/BE-0347-bounded-ci-review-cycle.md
