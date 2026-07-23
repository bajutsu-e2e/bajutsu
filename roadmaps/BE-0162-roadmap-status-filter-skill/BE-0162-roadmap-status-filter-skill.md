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

Add a Claude Code skill that lists roadmap (BE) items filtered by `Status`, so an AI
session can survey "everything that is a `Proposal`" (or `In progress`, `Implemented`,
`Proposal (deferred)`) without reading the full `roadmaps/README.md`. The skill takes a
status argument and prints one table — `ID`, title, `Topic`, and the item's file path — so
Claude can then open only the items it actually needs.

## Motivation

`roadmaps/README.md` is the index of every BE item, and it has grown past 700 lines with
well over a hundred items across four status buckets and many topics. A session that only
needs, say, the open proposals still has two poor options today:

- **Read the whole index** — hundreds of lines of generated tables land in context, most of
  them irrelevant to the task, spending tokens and burying the few rows that matter.
- **`grep` the README by hand** — works, but every session re-invents the incantation, and
  the README's per-bucket / per-topic table layout makes a clean "just the proposals, with
  their file paths" projection awkward to assemble from `grep` alone.

The index is *generated from each item's own metadata* (`Status` / `Topic` / title) by
[`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py) (BE-0043 / BE-0078).
That same metadata is the authoritative, machine-readable source a status filter needs — so
the projection an AI session wants ("give me the `Proposal` rows with paths") is a small,
deterministic query over data the repo already parses, not a new source of truth.

A focused, argument-driven skill gives every session one consistent, low-token way to ask
that question and get back exactly the rows it needs, with the file path to read next.

## Detailed design

The work breaks down into three mutually exclusive, collectively exhaustive units.

### 1. A deterministic status-filter query

Reuse the metadata parsing the index generator already owns rather than adding a second
parser. `scripts/build_roadmap_index.py` (via `roadmap_ids.iter_item_dirs` +
`metadata_fields`) already reads every `roadmaps/BE-*/` item's fenced `| Field | Value |`
block for its `Status` and `Topic` and its H1 title. Expose that as a small query the skill
can call — either:

- a thin CLI entry point (e.g. `python scripts/roadmap_query.py --status Proposal`) that
  factors the shared parsing out of `build_roadmap_index.py` into an importable helper, or
- a new `make` target that wraps it.

The query:

- Takes one `Status` value — `Proposal` / `In progress` / `Implemented` /
  `Proposal (deferred)` — matched case-insensitively, and validates it against the known set
  (an unknown status prints the valid values and exits non-zero, rather than an empty table).
- Emits a Markdown table with columns `ID`, `Item` (title), `Topic`, and `Path` (the
  relative path to the item's English `.md` file), sorted by `Topic` then `ID` for stable
  output.
- Is pure and offline — no `gh`, no network, no LLM. It only reads files under `roadmaps/`.

Placeholder (`BE-0162`) items are read for their `Status`/`Topic` like any other, so an
in-flight proposal shows up in the filter (its `ID` cell shows the placeholder).

### 2. The skill definition

Add `.claude/skills/roadmap-status/` (name TBD) alongside the existing `ideation` /
`implement-be` / `japanese-tech-writing` skills. The skill:

- Is **read-only and Claude-facing** — it surveys the roadmap for the session; it does not
  author, implement, or modify any item. It never edits files.
- Takes the status as its argument, invokes the query from unit 1, and returns the table
  to the session so Claude can pick the items to open in full.
- Documents in its frontmatter/body the valid statuses and that the emitted `Path` column
  is what to `Read` next for the full proposal text.

### 3. Tests and docs

- A test over the query asserting that filtering by each status returns exactly the items
  whose metadata carries that status, and that an unknown status fails cleanly (this
  mirrors the existing roadmap-format / index tests, and is a natural home in
  `tests/test_roadmap_*`).
- A short mention in [`docs/ai-development.md`](../../docs/ai-development.md) so sessions
  know the skill exists, next to the model/effort-tiering guidance (BE-0103).

**Prime directives.** This is contributor tooling over docs — no LLM enters the Tier-2
`run`/CI gate (directive 1); the query is deterministic and offline (directive 2); it is
app-agnostic, touching only `roadmaps/` metadata (directive 3).

## Alternatives considered

- **Just tell sessions to `grep` the README.** No new code, but leaves every session to
  re-derive the command and re-assemble the README's per-bucket tables into the projection
  it wants — inconsistent and error-prone. A named skill over the metadata is the reusable,
  low-token form.
- **Query the generated index tables instead of the item metadata.** The README tables are
  a rendering, not the source of truth, and would tie the filter to the index being freshly
  regenerated. Reading each item's own metadata (as the generator does) is the authoritative
  path and stays correct even mid-edit.
- **Extend the GitHub Pages roadmap dashboard (BE-0094) instead.** That dashboard serves
  humans in a browser; it does not help an AI session that needs the rows in its context
  without a network fetch. Distinct audience, distinct surface — this is complementary, not
  a duplicate.
- **A general roadmap-search skill (filter by topic, text, etc.).** Broader, but out of
  scope for this item, which is deliberately narrow: filter by `Status`. Richer filters can
  follow as a separate item once this proves useful.

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
