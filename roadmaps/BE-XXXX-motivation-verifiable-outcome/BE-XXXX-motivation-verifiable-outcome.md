**English** · [日本語](BE-XXXX-motivation-verifiable-outcome-ja.md)

# BE-XXXX — Require a verifiable outcome in every BE item's Motivation

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-motivation-verifiable-outcome.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

This item extends [`document-writing`][document-writing]'s contribution-first principle so a BE
item's Motivation names not only what should change but how a reader would later judge whether the
change delivered. Today the norm requires stating the contribution up front; it does not require
stating a way to check it. GitHub's [Spec Kit][spec-kit] closes exactly this gap for its own
specifications by requiring a measurable success criterion, and this item imports one piece of that
practice: a verifiable-outcome sentence in Motivation. It imports nothing else — *Alternatives
considered* records why the rest of Spec Kit does not fit Bajutsu's roadmap format or its review
contract.

## Motivation

A BE item is argued prose: Motivation makes the case for a change, and `document-writing` already
requires that case to state its contribution before its detail. What it does not require is a way to
tell, once the change ships, whether that contribution actually arrived. [BE-0347][] argues that
splitting a review pass into two roles "should let each round close a finding instead of deferring
it" and that doing so "should cut down how often the live bot's own re-review cycle even starts" —
plausible claims, but neither Motivation nor the item's *Progress* checklist states what a reader
could check, after the fact, to confirm either one happened. *Progress* tracks whether the units of
work landed, not whether the change produced the effect Motivation predicted; the two are different
questions, and only the first has a place to live today.

GitHub's Spec Kit is built around closing this same gap for its own specifications: a `spec.md`'s
success criteria must be "measurable" and "technology-agnostic", so a requirement such as "checkout
completes in under three minutes" gives every later reader a fact they can check against the shipped
result. Bajutsu cannot import that requirement's technology-agnostic half. A BE item's Detailed
design is expected to name concrete files, functions, and mechanisms; a Spec Kit `spec.md` must avoid
implementation detail entirely. But the measurability half transfers cleanly: asking
an author to name one observable difference the change is meant to produce costs nothing extra at
authoring time, when the author already knows why the change matters, and gives every later reader
— a reviewer, a future contributor revisiting the item, or the author checking their own prediction
after the implementing PR lands — a concrete fact to check the claim against, instead of only the
prose's own say-so. This item's own verifiable outcome, stated to meet the norm it proposes: a
reader of any later-merged item can hold that item's Motivation against the shipped change and say
whether the predicted effect arrived — where BE-0347's two predictions above leave nothing to hold
them against.

## Detailed design

1. **[`document-writing`][document-writing] — add a verifiable-outcome bullet.** In the
   *Language-agnostic principles* section, add a bullet next to "State the contribution up front":
   a BE item's Motivation names one observable difference the change is meant to produce — a change
   in behavior, output, or a quantity a reader could check — distinct from the *Progress* checklist,
   which tracks completed work rather than delivered effect. The outcome does not need a numeric
   target; "the local self-review pass converges before a push, instead of the live bot's cycle
   restarting on unrelated findings" is as valid as a number would be, as long as a later reader could
   tell whether it held. State the new bullet as a BE-specific instance of the existing principle,
   not a new, separate principle — the existing "State the contribution up front" bullet already
   carries a BE-specific clause ("A BE item's Introduction and Motivation state the problem..."), so
   BE-specific guidance is already at home there.
2. **[`ideation`][ideation] step 2 — ask for the verifiable outcome during the conversation.** The
   step already asks "what's the machine-checkable outcome" — the deterministic assertion a feature
   is gated on, which is a different question from this one. Add one clause, worded so the two do not
   collide: also ask what the author would point to, after the change ships, to say it worked — the
   same question step 4 then expects Motivation to answer in prose. This keeps the question inside the
   existing back-and-forth rather than adding a new step; unit 1's norm is what makes the answer
   mandatory in the drafted prose, not this step.
3. **[`ideation`][ideation] step 4 — carry the answer into the draft.** The step already instructs
   invoking `document-writing` "before you draft that prose". Add one sentence: when filling
   Motivation's `TBD`, include the verifiable outcome from step 2's conversation. This is the only
   change needed here — unit 1 supplies the standing rule, unit 2 supplies the conversation that
   produces the answer, and this unit only points the drafting step at using it.

No change reaches [`propose-and-build`][propose-and-build] or [`.github/claude-review-prompt.md`][claude-review-prompt]:
`propose-and-build` Phase A already invokes `document-writing` by reference (per [BE-0278][]), so it
inherits unit 1 with no separate edit, and the review contract's severity floor is deliberately
functional-impact-only (see *Alternatives considered*), so this item leaves it untouched.

## Alternatives considered

**Add a "success criteria are unmeasurable" check to `.github/claude-review-prompt.md`'s prose-quality
lens.** Rejected. The contract's severity floor was deliberately narrowed, twice, to functional impact
only, after the prose lenses proved too strict in practice; whether Motivation names a verifiable
outcome is a question about content rather than wording, and does not fit either of the two prose lenses the
contract keeps ("Japanese prose quality" and "English documentation and roadmap prose quality" both
judge how something is phrased, not what claim it makes). Widening the contract to catch this would
re-open the exact churn [BE-0347][] narrowed the contract to avoid, for a norm this item can instead
enforce for free, in the same authoring pass that already invokes `document-writing`.

**Adopt Spec Kit's `[NEEDS CLARIFICATION]` marker for an unresolved design point.** Rejected. The
marker exists because Spec Kit's specification-writing step runs without the stakeholder present, so
an unresolved question has nowhere to go until a later, separate review step reads the marker. In
`ideation`, the author and the stakeholder are in the same conversation: step 2 resolves an unformed
point through the back-and-forth itself, before step 4 ever drafts prose, so a marker meant to survive
until a later reader has no gap here to fill.

**Adopt Spec Kit's `spec.md` / `plan.md` / `tasks.md` three-document split, or its `constitution.md`
gate.** Rejected for both. A BE item is a single file whose Detailed design is expected to carry
implementation detail — file names, functions, mechanisms — which is exactly what Spec Kit's
`spec.md` must exclude by design; splitting Motivation's "what and why" from Detailed design's "how"
into separate documents would fight the format every existing BE item already uses. A
`constitution.md`-style gate already exists in a different shape: `ideation`'s own prime directives
[step](../../.agent-workflows/ideation/workflow.md#prime-directives-these-bound-every-idea), backed
by `CLAUDE.md` and `DESIGN.md`, and adding a second, Spec-Kit-shaped gate on top would duplicate it
rather than close a gap.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — add the verifiable-outcome bullet to `document-writing`.
- [ ] Unit 2 — `ideation` step 2 asks for the verifiable outcome during the conversation.
- [ ] Unit 3 — `ideation` step 4 carries the answer into the drafted Motivation.

## References

- [GitHub Spec Kit][spec-kit] and its [`spec-driven.md`](https://github.com/github/spec-kit/blob/main/spec-driven.md)
  — the source of the measurable-success-criterion practice this item imports one piece of.
- [`document-writing`][document-writing] — the norm this item adds a bullet to.
- [`ideation`][ideation] — the skill whose steps 2 and 4 this item updates.
- [BE-0278][] — the item that created the unified writing norm this one extends.
- [BE-0347][] — the item whose own Motivation this item's *Motivation* section uses as a worked
  example of an unverified claim.

[document-writing]: ../../.agent-workflows/document-writing/workflow.md
[ideation]: ../../.agent-workflows/ideation/workflow.md
[propose-and-build]: ../../.agent-workflows/propose-and-build/workflow.md
[claude-review-prompt]: ../../.github/claude-review-prompt.md
[spec-kit]: https://github.com/github/spec-kit
[BE-0278]: ../BE-0278-tech-writing-skill/BE-0278-tech-writing-skill.md
[BE-0347]: ../BE-0347-bounded-ci-review-cycle/BE-0347-bounded-ci-review-cycle.md
