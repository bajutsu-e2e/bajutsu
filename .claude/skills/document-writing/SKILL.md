---
name: document-writing
model: sonnet
description: >-
  The authoritative prose norm for Bajutsu's writing — the language-agnostic writing technique both
  languages share (top-down drafting, stating the contribution up front, sentence stress,
  subject–verb proximity, active voice, cutting filler, paragraph-level argument structure —
  paragraph writing — self-contained prose that never assumes prior reading, and minimizing anaphora
  that forces the reader to backtrack). Use it whenever you write or revise a BE roadmap item (`*.md`
  / `*-ja.md`) or a prose document under `docs/`, in either language. It is the umbrella above two
  language layers: apply `english-document-writing` with it for English prose, and
  `japanese-document-writing` for Japanese prose.
---

<!--
The language-agnostic principles below are distilled from Jeffrey Scott Vitter,
"Structure + Style = Communication" (The University of Kansas, 2011). The English-specific
mechanics from the same source now live in the `english-document-writing` skill.
-->

# Technical-writing norm

Bajutsu's writing is argued prose: BE items and the documents under `docs/` make a case a reader
must be able to follow. This skill is the authoritative norm for that prose, in both languages.
Invoke it **before** you write or revise, not after — it shapes the draft, it is not a proofreading
pass.

- **Scope.** Every BE roadmap item (`roadmaps/BE-*/*.md` and its `*-ja.md` mirror) and every
  document under `docs/` (and the `docs/ja/` mirror), in both languages. It does **not** govern
  code docstrings, which keep their own standard ([BE-0065](../../../roadmaps/BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference.md)).
- **An umbrella above two language layers.** The *language-agnostic principles* below hold for both
  languages. On top of them, apply the layer for the language you are writing: for **English**,
  [`english-document-writing`](../english-document-writing/SKILL.md) (serial comma, *that* / *which*,
  dashes, numbers, and the rest of the English mechanics); for **Japanese**,
  [`japanese-document-writing`](../japanese-document-writing/SKILL.md). Each language layer holds only the
  guidance its own grammar and typography need, and never applies to the other language. Where a
  language layer overlaps these principles (filler, redundancy, restraint of rhetorical flourish,
  paragraph and argument structure, self-containment), that layer's specific wording governs for its
  language.
- **Enforcement stays a review-time norm, not a CI gate.** Judging clarity, emphasis, and argument order
  needs semantic judgment; putting that on the gate would put an LLM on the `run` / CI verdict path,
  which prime directive 1 forbids. It holds the same way the bilingual-docs and DESIGN.md-alignment
  ([BE-0113](../../../roadmaps/BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md))
  norms hold: a contributor and reviewer expectation, checked by people.

## Language-agnostic principles (both languages)

- **Draft top-down, version by version.** Do not write a long argued section linearly from its
  first sentence. Build it in passes: first a rough outline of the points in the order you will
  treat them; then the section titles and each section's main idea; then subideas and a story line;
  then the near-final prose. The outline forces the organization the reader will need.
- **Go for clarity and simplicity.** On review, cut every word, phrase, and sentence that carries
  no meaning. Streamlining is a required pass, not a nicety.
- **State the contribution up front.** A BE item's Introduction and Motivation state the problem,
  the background, the contribution, and why it matters — before the detail. A reader who stops after
  the first two paragraphs should still come away with the point.
- **The two stress points of a sentence are its beginning and its end.** The end carries the most
  emphasis, so reserve it for the most important element. Move from the known to the unknown, from
  the given to the new, from background to result. Put transitional and background words
  ("Therefore", "However", "In the last section") at the front, saving the content for the end.
- **Keep the verb close to the subject.** A long clause wedged between a subject and its verb makes
  a sentence hard to parse. Rewrite so the verb arrives early, then let the qualifications follow.
- **Prefer the active voice, and write as "we".** The active voice is clearer and shorter; the
  first-person plural keeps it active without the "I" of a personal essay.
- **Cut filler.** Drop words and phrases that add no meaning — "actually", "basically",
  "essentially", "it is important to note that", "note that", a bare "now". Keep "thus" /
  "therefore" only where they genuinely signal a conclusion.

## Paragraph structure (both languages)

Paragraph writing: treat the paragraph, not only the sentence, as the unit of argument. A reader
must be able to follow the case being made paragraph by paragraph, without re-reading to find where
one point ends and the next begins.

- **One topic per paragraph.** A paragraph that mixes several beats of a narrative (investigating,
  reporting, verifying, evaluating) is really several paragraphs. Split it so each paragraph carries
  exactly one step.
- **Front-load the topic.** A reader who reads only a paragraph's first sentence should know what
  the paragraph is about. Do not bury the topic after a run-up.
- **Open on the logical relation to what came before.** Start the paragraph with the connective that
  states how it relates to the previous one ("therefore", "in fact", "however", "even this example
  alone shows"), not partway through.
- **Argue in one direction.** Reach a conclusion, then dispose of objections, then stop — do not
  restate the conclusion afterward. State it once.
- **Do not let an aside interrupt the climax.** Handle an anticipated objection (an example that
  might look contrived, a preemptive caveat) at a paragraph or section boundary, not right after the
  moment it would otherwise interrupt.
- **Name a misreading before correcting it.** When a reader is likely to draw the wrong conclusion,
  state that wrong conclusion explicitly, then give the real one — do not only assert the correct
  account and leave the misreading unaddressed.
- **Justify a denial in the same sentence.** When writing "A, not B", give the reason for ruling out
  B in that sentence rather than leaving it implicit.
- **Do not preview the payoff.** A number or fact meant to land with impact belongs in the paragraph
  where it lands, not foreshadowed in an earlier one.
- **Place forward references at a boundary.** "Covered in a later section" belongs at the end of a
  paragraph or section, once the point in hand is settled — not dropped mid-argument, where it
  breaks the reader's flow.
- **Quote the exact claim you are denying.** When you deny or qualify something, name the specific
  proposition you are ruling out — a vague denial ("not everything is solved") tells the reader
  nothing; naming the precise claim that is false does.

## Self-contained prose (both languages)

A document must be understandable on its own. A reader who has not read anything else in the
repository — no other BE item, no other page under `docs/`, no earlier commit — must still be able
to follow it start to finish.

- **Never assume prior reading.** Do not write as though the reader already read a linked document, an
  earlier roadmap item, or an earlier section of the same document. Give each piece the background it
  needs in place.
- **Spell out an abbreviation or acronym on first use.** Write the full term, with the acronym in
  parentheses right after — "role-based access control (RBAC)" — then the acronym alone for the rest
  of the piece. This holds everywhere a term appears, in a roadmap item as much as in `docs/`.
  Re-expand it if a long document's later section could plausibly be read on its own.
- **Define a term where you introduce it, and let a cross-reference only send the reader further.**
  A link is a supplement for a reader who wants depth — for elaboration, the canonical wording of a
  cross-cutting rule, or the record — never a substitute for the context this document needs at
  first use, and never the only place a fact the current sentence depends on is stated.
- **No omissions.** Do not skip a step, a definition, or a piece of context because it "should be
  obvious" or "is covered elsewhere". If a reader would have to guess or go looking, the document is
  not yet self-contained.

## Minimize anaphora (both languages)

An anaphor — a pronoun or demonstrative that points back to something named earlier ("it", "this",
"that", "the former") instead of renaming it — costs the reader a lookup: the reader must hold the
earlier antecedent in memory, then resolve the pointer before the sentence means anything. Reserve it
for an antecedent that is unmistakable and one sentence back; beyond one sentence back, repeat the
noun.

- **Repeat the noun once the antecedent is more than one sentence back, or crosses a paragraph, a
  list, or a heading.** By the time the reader reaches a boundary like that, an antecedent introduced
  before it is no longer active in working memory, so pointing back to it forces a return trip. Name
  the thing again instead.
- **Repeat the noun when more than one candidate antecedent is nearby.** A pronoun that could resolve
  to either of two nouns mentioned in the same stretch of prose forces the reader to guess, then
  double back to confirm the match. Name the one intended.
- **Do not chain an anaphor onto another anaphor.** A "this" pointing to a sentence that itself
  used "it" makes the reader resolve two pointers instead of one. Resolve each reference to the
  concrete noun, not to the previous pronoun.
- **Disambiguate in place instead of sending the reader back.** When an abstract phrase's referent is
  not obvious from the immediate context, name it with a parenthetical aside at the point of use
  rather than leaving the reader to recover it from an earlier passage.

## Verify your work

After drafting, reread once against sentence-level technique: does the piece lead with its
contribution, does each sentence end on its most important element, is the verb near its subject, and
is every sentence free of filler?

Reread again against paragraph structure: does every paragraph carry exactly one topic announced in
its first sentence, and does the argument move in one direction without restating its conclusion?

Reread a third time against self-containment: could a reader who has not read anything else in the
repository follow this piece start to finish, with every acronym expanded and every term defined
where it is first used?

Reread a fourth time against anaphora: does every pronoun or demonstrative resolve to a single,
nearby, unambiguous antecedent, with the noun repeated everywhere it does not? Then run the same four
rereads under the layer for your language: [`english-document-writing`](../english-document-writing/SKILL.md)
for English, [`japanese-document-writing`](../japanese-document-writing/SKILL.md) for Japanese.

This reread is a human-judgment pass. It does not replace the mechanical textlint check below —
run both.

## Mandatory textlint verification after drafting

The norms above guide human judgment; they are not machine-checkable. Once a piece is drafted, run
it through [textlint](https://github.com/textlint/textlint) and **keep revising and rerunning until
every finding is gone**. Do not call a piece done with findings still outstanding. textlint is a
mechanical floor, not a substitute for the norms above — passing it does not mean the piece
satisfies them. This applies to English and Japanese prose alike; the same config and runtime cover
both. When a norm here conflicts with a textlint rule, textlint takes priority: clear the finding by
revising the prose, not by loosening the config to dodge it.

### Running it

The config and runtime live in [`textlint/`](textlint/). It needs node and npm (the same
prerequisite as this repo's other JS checks). From the skill's directory, pass the Markdown file(s)
you wrote or edited.

```bash
SKILL_DIR=.claude/skills/document-writing
# Fetch dependencies once. npm ci honors package-lock.json exactly and verifies each
# package's integrity hash (sha512). --ignore-scripts blocks install-time lifecycle
# scripts (both are supply-chain defenses; see textlint/README.md for why)
npm --prefix "$SKILL_DIR/textlint" ci --ignore-scripts
# Verify (pass as many target files as you like)
npx --prefix "$SKILL_DIR/textlint" textlint \
  --config "$SKILL_DIR/textlint/.textlintrc.json" \
  path/to/edited.md
```

Fix auto-fixable findings with `--fix`, then revise away the rest by hand. Always eyeball the
result of `--fix` and revert any rewrite that violates the norms above.

### Changing the rules

Edit [`textlint/.textlintrc.json`](textlint/.textlintrc.json) to change what's enforced. It
currently enables `textlint-rule-preset-ja-technical-writing` (the standard Japanese
technical-writing preset) plus a set of individual rules for English prose and for Japanese prose
beyond the preset. The full list and why each rule was chosen live in the "The rules enabled today"
section of [`textlint/README.md`](textlint/README.md); the same file covers how to add or retire a
rule. Change the config only for structural reasons — adopting or retiring a rule, or stopping a
rule from double-reporting what another already covers. Do not loosen a rule or raise a threshold to
dodge a finding on prose you have written; per the priority above, fix the prose instead.

## References

- Jeffrey Scott Vitter, *Structure + Style = Communication* (The University of Kansas, 2011) — the
  source of the language-agnostic principles above (and of the English mechanics now in
  `english-document-writing`).
- [`english-document-writing`](../english-document-writing/SKILL.md) — the English layer beneath this norm.
- [`japanese-document-writing`](../japanese-document-writing/SKILL.md) — the Japanese layer beneath this
  norm.
- [`docs/ai-development.md`](../../../docs/ai-development.md) — the *Documentation style* section
  this skill is the authoritative reference for.
