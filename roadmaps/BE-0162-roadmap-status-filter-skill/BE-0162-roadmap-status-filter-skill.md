**English** · [日本語](BE-0162-roadmap-status-filter-skill-ja.md)

# BE-0162 — Roadmap status-filter skill for AI sessions

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0162](BE-0162-roadmap-status-filter-skill.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0162") |
| Implementing PR | [#650](https://github.com/bajutsu-e2e/bajutsu/pull/650) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

This item adds a Claude Code skill that lists roadmap (BE) items filtered by `Status`. An AI
session can then survey "everything that is `Approved`" (or `In progress`, `Implemented`,
`Deferred`, `Rejected`) without reading the full `roadmaps/README.md`.

The skill takes a status argument and prints one table with the `ID`, the title, the `Topic`, and
the item's file path. Claude can then open the items it needs and no others.

## Motivation

`roadmaps/README.md` is the index of every BE item. It has grown past 700 lines, with well over a
hundred items across four status buckets and many topics. A session that needs the open
proposals alone has two poor options today:

- **Read the whole index.** Hundreds of lines of generated tables land in context. Most of them are
  irrelevant to the task, so they spend tokens and bury the few rows that matter.
- **`grep` the README by hand.** This works, but every session re-invents the incantation. The
  README's per-bucket and per-topic table layout also makes a clean projection awkward to assemble
  from `grep` alone (the proposals, with their file paths).

[`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py) generates the index from
each item's own metadata (`Status`, `Topic`, and title) (BE-0043 / BE-0078). That same metadata is
the authoritative, machine-readable source a status filter needs. The projection an AI session
wants ("give me the `Approved` rows with paths") is a small, deterministic query over
data the repo already parses, not a new source of truth.

A focused, argument-driven skill gives every session one consistent, low-token way to ask that
question. The session gets back the rows it needs, with the file path to read next.

## Detailed design

The work breaks down into three mutually exclusive, collectively exhaustive units.

### 1. A deterministic status-filter query

The query reuses the metadata parsing the index generator already owns, instead of adding a second
parser. `scripts/build_roadmap_index.py` (via `roadmap_ids.iter_item_dirs` and `metadata_fields`)
already reads the fenced `| Field | Value |` block of every `roadmaps/BE-*/` item. From that block
it takes the `Status`, the `Topic`, and the H1 title. The skill calls this as a small query, in
one of two forms:

- a thin CLI entry point (e.g. `python scripts/roadmap_query.py --status Approved`) that factors
  the shared parsing out of `build_roadmap_index.py` into an importable helper, or
- a new `make` target that wraps it.

The query behaves as follows:

- It takes one `Status` value: `Approved`, `In progress`, `Implemented`, `Deferred`, or
  `Rejected`. It matches case-insensitively and validates the value against the known set. An
  unknown status prints the valid values and exits non-zero, rather than printing an empty table.
- It emits a Markdown table with columns `ID`, `Item` (title), `Topic`, and `Path` (the relative
  path to the item's English `.md` file). Rows are sorted by `Topic` and then `ID`, so the output
  is stable.
- The query is pure and offline: no `gh`, no network, no LLM. It reads files under `roadmaps/` and nothing
  else.

Placeholder (`BE-0162`) items are read for their `Status` and `Topic` like any other. An in-flight
proposal shows up in the filter, with the placeholder in its `ID` cell.

### 2. The skill definition

The skill lives in `.claude/skills/roadmap-status/` (name TBD), alongside the existing `ideation`,
`implement-be`, and `japanese-tech-writing` skills. It has three properties:

- The skill is **read-only and Claude-facing**. It surveys the roadmap for the session. It never authors,
  implements, or modifies an item, and it never edits files.
- It takes the status as its argument, invokes the query from unit 1, and returns the table to the
  session. Claude then picks the items to open in full.
- Its frontmatter and body document the valid statuses. They also state that the emitted `Path`
  column is what to `Read` next for the full proposal text.

### 3. Tests and docs

- A test over the query asserts two things. Filtering by each status returns exactly the items
  whose metadata carries that status, and an unknown status fails cleanly. The test mirrors the
  existing roadmap-format and index tests, and `tests/test_roadmap_*` is its natural home.
- A short mention in [`docs/ai-development.md`](../../docs/ai-development.md) tells sessions the
  skill exists. It sits next to the model/effort-tiering guidance (BE-0103).

**Prime directives.** This is contributor tooling over docs, and no LLM enters the Tier-2
`run`/CI gate (directive 1). The query is deterministic and offline (directive 2). The tooling is
app-agnostic and touches `roadmaps/` metadata alone (directive 3).

## Alternatives considered

- **Tell sessions to `grep` the README.** This needs no new code. It leaves every session to
  re-derive the command and re-assemble the README's per-bucket tables into the projection it
  wants, which is inconsistent and error-prone. A named skill over the metadata is the reusable,
  low-token form.
- **Query the generated index tables instead of the item metadata.** The README tables are a
  rendering, not the source of truth. Querying them would tie the filter to a freshly regenerated
  index. Reading each item's own metadata, as the generator does, is the authoritative path and
  stays correct even mid-edit.
- **Extend the GitHub Pages roadmap dashboard (BE-0094) instead.** That dashboard serves humans in
  a browser. It does not help an AI session that needs the rows in its context without a network
  fetch. The audience and the surface differ, so the dashboard complements this skill and does not
  duplicate it.
- **A general roadmap-search skill (filter by topic, text, etc.).** This is broader, but out of
  scope for this item, which is deliberately narrow: filter by `Status`. Richer filters can follow
  as a separate item once this one proves useful.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Deterministic status-filter query (shared metadata parsing, validated status, table output)
- [x] Skill definition (`.claude/skills/…`, read-only, argument-driven)
- [x] Tests and a docs mention in `docs/ai-development.md`

Log:

- `scripts/roadmap_query.py` adds the status filter, reusing `metadata_fields` / `iter_item_dirs`
  from the index generator; `make roadmap-status STATUS="…"` wraps it. The read-only
  `roadmap-filter` skill (`haiku`) surveys the roadmap by status and returns each item's path to
  open next. `tests/test_roadmap_query.py` pins status resolution, the filter, table rendering, and
  the CLI exit codes; `docs/ai-development.md` (both languages) documents the skill next to the
  model/effort tiering (BE-0103).

## References

- [`roadmaps/README.md`](../README.md) — the full index this filter projects from.
- [`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py) — the existing
  metadata parser to reuse.
- [BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md)
  / [BE-0078](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md) — the
  generated-index and status-as-source-of-truth design this builds on.
- [BE-0094](../BE-0094-roadmap-status-dashboard/BE-0094-roadmap-status-dashboard.md) — the
  human-facing dashboard, the complementary surface.
- [BE-0103](../BE-0103-dev-model-effort-tiering/BE-0103-dev-model-effort-tiering.md) — the
  AI-session guidance in `docs/ai-development.md` this skill sits beside.
