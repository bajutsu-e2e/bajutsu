---
name: english-tech-writing
model: sonnet
description: >-
  English-specific prose mechanics for Bajutsu's technical writing — formal word choice, the serial
  (Oxford) comma, restrictive that vs. non-restrictive which, no bare this/that as a noun, no
  singular they for one unspecified person, dashes, numbers, and colons. The English layer beneath
  the language-agnostic `tech-writing` skill. Use it whenever you write, translate into, or revise
  English prose in a BE roadmap item (`*.md`) or a document under `docs/`; apply it together with
  `tech-writing`, which carries the writing technique both languages share.
---

<!--
The English mechanics below are distilled from Jeffrey Scott Vitter,
"Structure + Style = Communication" (The University of Kansas, 2011).
-->

# Technical-writing norm for English prose

This skill is the English layer of Bajutsu's prose norm. It holds the mechanics that only English
grammar and typography need — the choices that have a single right answer in English and no
counterpart in Japanese.

It sits beneath the language-agnostic [`tech-writing`](../tech-writing/SKILL.md) skill. When you
write or revise English prose, apply **both**: `tech-writing` for the writing technique both
languages share (top-down drafting, sentence stress, subject–verb proximity, active voice, cutting
filler), and this skill for the English mechanics below. Invoke both **before** you write, not
after. These mechanics never apply to Japanese — Japanese has its own dash and punctuation rules,
in [`japanese-tech-writing`](../japanese-tech-writing/SKILL.md), the sibling layer beneath the same
umbrella.

Like the rest of the prose norm, this is a review-time expectation checked by people, not a CI gate
(prime directive 1 keeps an LLM off the `run` / CI verdict path).

## English-specific mechanics

- **Formal word choice.** Use "many" or "much", not "a lot of". Spell out contractions in formal
  text. Reserve symbols and abbreviations for formulas; in prose, spell out the word (write "for
  all", not "∀"; "such that", not "s.t.").
- **Serial (Oxford) comma.** In a list of three or more items, put a comma before the final "and"
  or "or": "the driver, the runner, and the reporter".
- **That vs. which.** Use *that* (no comma) for a restrictive clause that specifies which thing you
  mean. Use *which* (set off by commas) for a non-restrictive clause that adds information not
  needed to identify the thing.
- **No bare "this" / "that" as a noun.** Name what it points to: "this behavior", "that
  constraint", not a lone "this".
- **No singular "they" for an unspecified single person.** Reword to the plural ("reviewers raise
  their hands") or a role, rather than "each reviewer raised their hand".
- **Dashes.** A hyphen joins a compound adjective ("capability-aware selection"). An en-dash marks a
  range ("steps 3–7") with no surrounding spaces. An em-dash sets off an aside for emphasis — with a
  space on each side, per this repo's own convention (Chicago itself specifies no space).
- **Numbers.** Spell out a positive whole number below ten used as an adjective ("three drivers");
  use the numeral when it is a noun or ten or above.
- **Colon.** Introduce a list or an elaboration with a colon only after a complete sentence.

## Verify your work

Reread the English draft once against the mechanics above, alongside the language-agnostic reread in
[`tech-writing`](../tech-writing/SKILL.md#verify-your-work). Then run the mandatory
[textlint](https://github.com/textlint/textlint) check — the runtime and rules live under
`tech-writing`, cover English prose too, and are documented in
[`tech-writing`'s textlint section](../tech-writing/SKILL.md#mandatory-textlint-verification-after-drafting).
Keep revising and rerunning until every finding is gone; textlint is a mechanical floor, not a
substitute for the norms above.

## References

- Jeffrey Scott Vitter, *Structure + Style = Communication* (The University of Kansas, 2011) — the
  source of the English mechanics above.
- [`tech-writing`](../tech-writing/SKILL.md) — the language-agnostic umbrella above this layer.
- [`japanese-tech-writing`](../japanese-tech-writing/SKILL.md) — the Japanese sibling layer.
