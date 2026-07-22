**English** · [日本語](BE-0074-be-template-standardization-ja.md)

# BE-0074 — Standardize the BE item template (EN / JA)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0074](BE-0074-be-template-standardization.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0074") |
| Implementing PR | [#197](https://github.com/bajutsu-e2e/bajutsu/pull/197) |
| Topic | Development infrastructure (contributor workflow) |
| Related | [BE-0100](../BE-0100-roadmap-progress-tracking-template/BE-0100-roadmap-progress-tracking-template.md) |
<!-- /BE-METADATA -->

## Introduction

Every roadmap item is a pair of files — an English `BE-NNNN-<slug>.md` and a Japanese
`BE-NNNN-<slug>-ja.md` — that share one fixed shape: a bilingual header link, an H1 title, a
metadata block, and the five Swift-Evolution sections (`Introduction` / `Motivation` /
`Detailed design` / `Alternatives considered` / `References`). That shape is described in prose
across [`CLAUDE.md`](../../CLAUDE.md), [`roadmaps/README.md`](../README.md), and the
[`ideation`](../../.claude/skills/ideation/SKILL.md) skill, and the index tables are already
generated and gate-checked from each item's metadata
([`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py),
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
([BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md))
already established the project's answer to this class of problem — make the invariant machine-checked
so "green locally predicts green in CI". The roadmap *index* is guarded that way; the item *body* is
not. This item extends the same treatment to the body.

## Detailed design

### Canonical metadata

The metadata block is a `| Field | Value |` table fenced by a marker pair,
`<!-- BE-METADATA -->` … `<!-- /BE-METADATA -->`, that mirrors the index's `<!-- GENERATED:* -->`
regions. It opens with a `| Field | Value |` header row (`| 項目 | 値 |` on the Japanese side) and
its `|---|---|` delimiter, so the block renders as a real table; the fence — not the header — is
still what marks where the metadata is. The fence is the load-bearing part: it lets the
parser read exactly these rows and never a same-shaped row elsewhere in the body, and it is what
the bullet-list form could not offer (a bare `* Key: value` line has no boundary). One row per
field also holds its shape better than free bullets.

The block carries the fields below, in this order. `Proposal`, `Author`, `Status`, `Track`, and
`Topic` are mandatory; the `Implementing PR` row appears once an item ships; the `Origin` row
appears only on items that came from competitive or dogfood research, and is always last.

| Order | English field | Japanese field | Required |
|---|---|---|---|
| 1 | `Proposal` | `提案` | always |
| 2 | `Author` | `提案者` | always |
| 3 | `Status` | `状態` | always |
| 4 | `Implementing PR` | `実装 PR` | once shipped |
| 5 | `Track` | `トラック` | always |
| 6 | `Topic` | `トピック` | always |
| 7 | `Origin` | `由来` | research-sourced only |

Two field decisions are settled here. `Implementing PR` is **singular regardless of PR count** —
the value cell is a comma-separated list of links, the field name does not pluralize (resolving
`BE-0051`). The Japanese author field is **`提案者`** — translating the one field that stayed
English, and reading naturally alongside `提案` (the proposal link) without colliding with it.

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

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-NNNN](BE-NNNN-<slug>.md) |
| Author | [@handle](https://github.com/handle) |
| Status | **Proposal** |
| Topic | <one of the index topics> |
<!-- /BE-METADATA -->

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
**日本語**`), the Japanese field names (`提案` / `提案者` / `状態` / `トラック` / `トピック`) and Status
value, and the Japanese section headings. The title line keeps the `BE-NNNN — <タイトル>` form (the em
dash here is part of the fixed title format, not Japanese running text).

The H1 title line is `# BE-NNNN — <Title>` in both files: the ID, a space, an em dash (`—`,
U+2014) flanked by spaces, then the title. A brand-new item leaves the number undetermined and is
authored under the literal placeholder directory the `ideation` skill and
[`README.md`](../README.md#adding-a-roadmap-item--be-ids) describe, which CI
([`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py)) rewrites to the real
number; this template governs everything else about the file.

### The metadata parser

The metadata block is read by `parse_metadata` in
[`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py) (the index generator)
and, for the `Status` field, by `read_status` in
[`scripts/promote_roadmap_items.py`](../../scripts/promote_roadmap_items.py). Their contract is:

1. **Scope to the fence.** Read only the text between `<!-- BE-METADATA -->` and
   `<!-- /BE-METADATA -->`. This is the reason for the markers: without them a parser keys on a
   shape (`* Key:` lines, or `| a | b |` rows) that also occurs in the body, so it can latch onto
   the wrong row. The fence makes the metadata region explicit — the same way the index tables are
   fenced by `<!-- GENERATED:* -->`.
2. **Read one field per row.** Inside the fence, each `| field | value |` row maps `field` to
   `value`, with `**` emphasis stripped. The `| Field | Value |` header row (`| 項目 | 値 |` in
   Japanese) and its `|---|---|` delimiter are skipped: the header is excluded by key, and the
   delimiter never matches the row pattern (no space after its leading pipe).
3. **Fall back for unmigrated items.** A file with no fence is read by the legacy `* Field: value`
   bullet rule, so the items not yet converted keep parsing during the migration.

Both parsers follow that contract (the fenced rows are parsed; the bullet form still works as a
fallback), so a hand-edited item that slips back to bullets still parses rather than breaking the
build.

### The deterministic check

A new test, `tests/test_roadmap_format.py`, runs under `make test` beside the existing index test.
For every `roadmaps/{implemented,proposals}/BE-*/` item it asserts, per language:

1. the first line is the exact bilingual header link for that file's language and slug;
2. the H1 is `# BE-NNNN — …` with the em dash;
3. the metadata block is fenced by `<!-- BE-METADATA -->` … `<!-- /BE-METADATA -->`, opens with the
   `| Field | Value |` header row (`| 項目 | 値 |` in Japanese) and its delimiter, and lists the
   required `| field | value |` rows for that language in the canonical order, with no unknown
   field rows;
4. `Status` is one of the four canonical values, and the English/Japanese values agree across the
   pair;
5. the five H2 headings are present, in order, with the exact canonical wording.

The check is a **gate, not a formatter**: it reports the offending file and line and fails, leaving
the fix to the author — mirroring `format-check` / `lint`, which report rather than rewrite. The two
files of a pair are validated together so an EN/JA Status mismatch is caught.

### What shipped

This item landed as one change: all roadmap items moved to the fenced metadata block, the two
parsers re-specified around it (with the legacy `* Field: value` form kept as a fallback so a
hand-edited item still parses), and `tests/test_roadmap_format.py` wired into `make test`. The
drift listed under *Motivation* is fixed in the same change — the two `## 代替案の検討` headings,
`BE-0044-ja`'s untranslated headings, the missing `## Motivation` in `BE-0017`, the missing author
field in `BE-0064`, the plural `Implementing PRs` in `BE-0051`, and the English author field across
the Japanese files (now `提案者`). The prose in `CLAUDE.md` and `roadmaps/README.md` (and its
Japanese mirror) is updated to describe the fenced block. The tree is green under the new check.

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
- **Translate the Japanese author field to `著者` rather than `提案者`.** Rejected: `提案者`
  (the one who proposed) reads naturally beside `提案` (the proposal) and matches the item's framing,
  whereas `著者` (writer) is a looser fit for an authorship-of-record field.
- **Keep the metadata as a `* Field: value` bullet list** (the current form, and the literal shape
  `CLAUDE.md` / `README.md` show as the "Swift-Evolution proposal format"). This item diverges from
  that on purpose: a bullet line has no boundary, so a parser scoped to it can stray onto a body
  list, and bullets drift more freely than one row per field. The fenced block closes both gaps and
  matches the index's own `<!-- GENERATED:* -->` convention. The divergence is small and documented
  here; the prose in `CLAUDE.md` / `README.md` is updated to the fenced block in the implementation
  phase.

## Progress

- [x] Shipped — see the *Implementing PR* above. (The template was later extended from five sections
  to six by [BE-0100](../BE-0100-roadmap-progress-tracking-template/BE-0100-roadmap-progress-tracking-template.md),
  which added this `Progress` section and the `Related` / `Superseded by` fields.)
- **Historical note:** the index-table mechanism this item describes in present tense —
  `scripts/build_roadmap_index.py` as "the index generator", the generated index tables it names in
  the Introduction, and the `<!-- GENERATED:* -->` marker convention the metadata fence is compared
  to — was retired by [#1257](https://github.com/bajutsu-e2e/bajutsu/pull/1257): the roadmap
  dashboard now covers what those tables did, and `build_roadmap_index.py` is purely a metadata
  parser today. Read those mentions below as the design context this item was written against, not
  current behavior.

## References

- [`CLAUDE.md`](../../CLAUDE.md) — the roadmap-item rules this item makes machine-checkable.
- [`roadmaps/README.md`](../README.md#adding-a-roadmap-item--be-ids) — the
  prose description of the BE format and ID rules.
- [`.claude/skills/ideation/SKILL.md`](../../.claude/skills/ideation/SKILL.md) — the skill that
  drafts new items from this shape.
- [`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py),
  [`scripts/promote_roadmap_items.py`](../../scripts/promote_roadmap_items.py),
  `tests/test_roadmap_index.py` — the metadata parsers this item re-specifies around the fenced
  table, and the existing generate-and-gate treatment of the *index* it mirrors for the *body*.
- [BE-0043 — Conflict-resistant file flow](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md)
  — the contributor-workflow sibling that established "make the invariant machine-checked".
- [BE-0069 — Executable contributor guardrails](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails.md)
  — the related procedures-as-commands item, kept distinct.
