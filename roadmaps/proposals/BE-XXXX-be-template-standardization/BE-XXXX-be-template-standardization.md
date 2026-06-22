**English** · [日本語](BE-XXXX-be-template-standardization-ja.md)

# BE-XXXX — Standardize the BE item template (EN / JA)

* Proposal: [BE-XXXX](BE-XXXX-be-template-standardization.md)
* Author: [@0x0c](https://github.com/0x0c)
* Status: **Proposal**
* Track: [Proposals](../../README.md#proposals)
* Topic: Development infrastructure (contributor workflow)

## Introduction

Every roadmap item is a pair of files — an English `BE-NNNN-<slug>.md` and a Japanese
`BE-NNNN-<slug>-ja.md` — that share one fixed shape: a bilingual header link, an H1 title, a
metadata block, and the five Swift-Evolution sections (`Introduction` / `Motivation` /
`Detailed design` / `Alternatives considered` / `References`). That shape is described in prose
across [`CLAUDE.md`](../../../CLAUDE.md), [`roadmaps/README.md`](../../README.md), and the
[`ideation`](../../../.claude/skills/ideation/SKILL.md) skill, and the index tables are already
generated and gate-checked from each item's metadata
([`scripts/build_roadmap_index.py`](../../../scripts/build_roadmap_index.py),
`tests/test_roadmap_index.py`). What is *not* yet pinned is the body of each item file: the exact
metadata labels, the heading wording, and which sections are mandatory. Authors — human and agent —
reconstruct the shape from neighbouring files each time, so small variations have crept in.

This item pins that shape. It writes down one canonical skeleton per language, names the normative
field set and section headings, and proposes a deterministic check — run by `make check`, like the
index check beside it — that fails when a `BE-NNNN-*` pair drifts from the skeleton. The whole
surface is documentation and a test; it changes no tool behaviour and adds no LLM to any path.

## Motivation

A sweep of the 69 items in both languages shows the shape is *almost* uniform: the header link, the
title line, the metadata field order (`Proposal` → `Author` → `Status` → optional
`Implementing PR` → `Track` → `Topic` → optional `Origin`, with `Origin` always last), and the
Status vocabulary are consistent everywhere. The drift is confined to a handful of spots that no
generator or test currently guards:

- **A Japanese heading is translated two ways.** `## 検討した代替案` appears in 66 files, but
  `## 代替案の検討` — the same words reordered — appears in two (`BE-0064-ja`, `BE-0066-ja`).
- **One Japanese file never got its headings translated.** `BE-0044-ja` still carries the English
  `## Introduction` / `## Motivation` / … instead of the Japanese wording its siblings use.
- **One item dropped a mandatory section.** `BE-0017` (both languages) has no `## Motivation` /
  `## 動機`.
- **One item dropped a metadata field.** `BE-0064` (both languages) has no `Author` / author line.
- **One metadata label is singular in 68 files and plural in one.** `* Implementing PR:` everywhere
  except `BE-0051`, which writes `* Implementing PRs:` for its two-PR list.
- **The author label is the one Japanese field still in English.** Every other metadata label is
  translated on the Japanese side (`提案`, `状態`, `トラック`, `トピック`, `実装 PR`, `由来`), but
  the author line stays `* Author:`. It is consistent today, but undocumented, so it reads as an
  oversight rather than a decision.

None of these is large on its own. Together they are the predictable result of "copy a neighbour and
match it by eye": prose guidance does not prevent drift, because nothing rereads the prose at commit
time. The conflict-resistant file-flow work
([BE-0043](../../implemented/BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md))
already established the project's answer to this class of problem — make the invariant machine-checked
so "green locally predicts green in CI". The roadmap *index* is guarded that way; the item *body* is
not. This item extends the same treatment to the body.

## Detailed design

### Canonical metadata

The metadata block carries the fields below, in this order. `Proposal`, `Author`, `Status`,
`Track`, and `Topic` are mandatory; `Implementing PR` appears once an item ships; `Origin` appears
only on items that came from competitive or dogfood research, and is always last.

| Order | English label | Japanese label | Required |
|---|---|---|---|
| 1 | `* Proposal:` | `* 提案:` | always |
| 2 | `* Author:` | `* 提案者:` | always |
| 3 | `* Status:` | `* 状態:` | always |
| 4 | `* Implementing PR:` | `* 実装 PR:` | once shipped |
| 5 | `* Track:` | `* トラック:` | always |
| 6 | `* Topic:` | `* トピック:` | always |
| 7 | `* Origin:` | `* 由来:` | research-sourced only |

Two label decisions are settled here. `Implementing PR:` is **singular regardless of PR count** —
the value is a comma-separated list of links, the label does not pluralize (resolving `BE-0051`).
The Japanese author label is **`提案者:`** — translating the one field that stayed English, and
reading naturally alongside `提案:` (the proposal link) without colliding with it.

`Status` takes one value from a fixed set, paired across languages:

| English | Japanese |
|---|---|
| `**Implemented**` | `**実装済み**` |
| `**Accepted, in progress**` | `**可決・実装中**` |
| `**Proposal**` | `**提案**` |
| `**Proposal (deferred)**` | `**提案（保留）**` |

### Canonical sections

Every item has the same five H2 sections, in order, with these exact headings:

| English | Japanese |
|---|---|
| `## Introduction` | `## はじめに` |
| `## Motivation` | `## 動機` |
| `## Detailed design` | `## 詳細設計` |
| `## Alternatives considered` | `## 検討した代替案` |
| `## References` | `## 参考` |

`## 検討した代替案` is the canonical Japanese heading (the 66-file majority); `## 代替案の検討` is
not used. All five sections are mandatory — a section with nothing yet to say carries a one-line
`TBD` rather than being omitted (resolving the missing `Motivation` in `BE-0017`).

### Canonical skeleton

The English file:

```markdown
**English** · [日本語](BE-NNNN-<slug>-ja.md)

# BE-NNNN — <Title>

* Proposal: [BE-NNNN](BE-NNNN-<slug>.md)
* Author: [@handle](https://github.com/handle)
* Status: **Proposal**
* Track: [Proposals](../../README.md#proposals)
* Topic: <one of the index topics>

## Introduction

<one or two paragraphs>

## Motivation

<why this is worth doing>

## Detailed design

<the concrete design; mark unknowns TBD>

## Alternatives considered

<options weighed and why this one>

## References

<links to DESIGN.md / docs / code / related BE items>
```

The Japanese file is the same shape with the Japanese header link (`[English](BE-NNNN-<slug>.md) ·
**日本語**`), the Japanese metadata labels and Status value, and the Japanese section headings. The
title line keeps the `BE-NNNN — <タイトル>` form (the em dash here is part of the fixed title
format, not Japanese running text).

The H1 title line is `# BE-NNNN — <Title>` in both files: the ID, a space, an em dash (`—`,
U+2014) flanked by spaces, then the title. A brand-new item leaves the number undetermined and is
authored under the literal placeholder directory the `ideation` skill and
[`README.md`](../../README.md#adding-a-roadmap-item--be-ids-agents-must-follow) describe, which CI
([`scripts/allocate_roadmap_ids.py`](../../../scripts/allocate_roadmap_ids.py)) rewrites to the real
number; this template governs everything else about the file.

### The deterministic check

A new test, `tests/test_roadmap_format.py`, runs under `make test` beside the existing index test.
For every `roadmaps/{implemented,proposals}/BE-*/` item it asserts, per language:

1. the first line is the exact bilingual header link for that file's language and slug;
2. the H1 is `# BE-NNNN — …` with the em dash;
3. the metadata block contains the required labels for that language, in the canonical order, with
   no unknown `* `-prefixed labels above the first H2;
4. `Status` is one of the four canonical values, and the English/Japanese values agree across the
   pair;
5. the five H2 headings are present, in order, with the exact canonical wording.

The check is a **gate, not a formatter**: it reports the offending file and line and fails, leaving
the fix to the author — mirroring `format-check` / `lint`, which report rather than rewrite. The two
files of a pair are validated together so an EN/JA Status mismatch is caught.

### Rollout

This proposal pins the shape and ships the skeleton; it does not by itself renumber or rewrite the
existing tree. The implementation phase wires the check into `make test` and, in the same change,
normalizes the handful of drifting files named in *Motivation* so the tree is green when the check
lands. (This item's own Japanese file already uses `提案者:`, demonstrating the target state.)

## Alternatives considered

- **Leave it to prose in CLAUDE.md / README.** This is the status quo, and the drift inventory is
  what it produced: guidance no one rereads at commit time does not hold a format. Rejected for the
  same reason the index is generated rather than hand-maintained.
- **A formatter that rewrites files into shape** (the `make format` model) instead of a checker that
  fails. Rejected as the first step: rewriting prose-bearing files risks touching content, and the
  drift is small enough to fix by hand. An auto-fixer could be a later convenience once the checker
  exists.
- **Fold into [BE-0069](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md)**
  (executable contributor guardrails). Related — both make a contributor procedure machine-checked —
  but distinct surfaces: BE-0069 turns multi-step *procedures* into commands, this pins one *file
  format* and its validator. Kept separate; cross-referenced.
- **Translate the Japanese author label to `著者:`** rather than `提案者:`. Rejected: `提案者:`
  (the one who proposed) reads naturally beside `提案:` (the proposal) and matches the item's framing,
  whereas `著者:` (writer) is a looser fit for an authorship-of-record field.

## References

- [`CLAUDE.md`](../../../CLAUDE.md) — the roadmap-item rules this item makes machine-checkable.
- [`roadmaps/README.md`](../../README.md#adding-a-roadmap-item--be-ids-agents-must-follow) — the
  prose description of the BE format and ID rules.
- [`.claude/skills/ideation/SKILL.md`](../../../.claude/skills/ideation/SKILL.md) — the skill that
  drafts new items from this shape.
- [`scripts/build_roadmap_index.py`](../../../scripts/build_roadmap_index.py),
  `tests/test_roadmap_index.py` — the existing generate-and-gate treatment of the *index* this item
  mirrors for the *body*.
- [BE-0043 — Conflict-resistant file flow](../../implemented/BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md)
  — the contributor-workflow sibling that established "make the invariant machine-checked".
- [BE-0069 — Executable contributor guardrails](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md)
  — the related procedures-as-commands item, kept distinct.
