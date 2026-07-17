---
name: tech-writing
model: sonnet
description: >-
  The authoritative prose norm for Bajutsu's writing — the language-agnostic writing technique both
  languages share (top-down drafting, stating the contribution up front, sentence stress,
  subject–verb proximity, active voice, cutting filler). Use it whenever you write or revise a BE
  roadmap item (`*.md` / `*-ja.md`) or a prose document under `docs/`, in either language. It is the
  umbrella above two language layers: apply `english-tech-writing` with it for English prose, and
  `japanese-tech-writing` for Japanese prose.
---

<!--
The language-agnostic principles below are distilled from Jeffrey Scott Vitter,
"Structure + Style = Communication" (The University of Kansas, 2011). The English-specific
mechanics from the same source now live in the `english-tech-writing` skill.
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
  [`english-tech-writing`](../english-tech-writing/SKILL.md) (serial comma, *that* / *which*,
  dashes, numbers, and the rest of the English mechanics); for **Japanese**,
  [`japanese-tech-writing`](../japanese-tech-writing/SKILL.md). Each language layer holds only the
  guidance its own grammar and typography need, and never applies to the other language. Where a
  language layer overlaps these principles (filler, redundancy, restraint of rhetorical flourish),
  that layer's specific wording governs for its language.
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

## Verify your work

After drafting, reread once against this list: does the piece lead with its contribution, does each
sentence end on its most important element, is the verb near its subject, is every sentence free of
filler? Then run the same reread under the layer for your language:
[`english-tech-writing`](../english-tech-writing/SKILL.md) for English,
[`japanese-tech-writing`](../japanese-tech-writing/SKILL.md) for Japanese.

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
SKILL_DIR=.claude/skills/tech-writing
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
  `english-tech-writing`).
- [`english-tech-writing`](../english-tech-writing/SKILL.md) — the English layer beneath this norm.
- [`japanese-tech-writing`](../japanese-tech-writing/SKILL.md) — the Japanese layer beneath this
  norm.
- [`docs/ai-development.md`](../../../docs/ai-development.md) — the *Documentation style* section
  this skill is the authoritative reference for.
