---
name: roadmap-filter
model: haiku
description: >-
  List roadmap (BE) items filtered by their Status, so a session can survey everything that is a
  Proposal (or In progress / Implemented / Proposal (deferred)) without reading the 700+-line
  roadmaps/README.md. Use when you need an overview of the roadmap in one status — e.g. "what
  proposals are open?", "which items are in progress?", "list the deferred ones" — or to find the
  file path of items in a status before opening them. Takes one status argument and returns a table
  of ID / Item / Topic / Path. Read-only: it surveys the roadmap, it never authors, implements, or
  edits any item (ideation authors proposals; implement-be ships them).
---

# Roadmap status filter

Survey the roadmap by `Status`. This skill is **read-only and Claude-facing**: it prints one table
so you can pick the items to open in full — it never authors, implements, or edits an item.

## What it does

`roadmaps/README.md` is the full index (700+ lines across four status buckets and many topics).
When you only need the items in one status, run the deterministic query instead of reading the
whole index into context:

```bash
make roadmap-status STATUS="Proposal"
```

`STATUS` is one of — matched case-insensitively:

- `Proposal` — open, not yet started
- `In progress` — being built
- `Implemented` — shipped
- `Proposal (deferred)` — deliberately parked

An unknown status prints the valid values and exits non-zero, rather than an empty table.

The query is pure and offline — it reads each item's own metadata under `roadmaps/` (the same
authoritative source the index generator reads), with no `gh`, no network, and no LLM. Equivalent
direct form: `python scripts/roadmap_query.py --status "Proposal"`.

## Output

A Markdown table with four columns:

| Column | Meaning |
|---|---|
| `ID` | the item's `BE-NNNN` (or the `BE-XXXX` placeholder for an in-flight item) |
| `Item` | the item's title |
| `Topic` | the item's Topic (the index's secondary grouping) |
| `Path` | the relative path to the item's English `.md` file |

Rows are sorted by `Topic`, then `ID`.

## How to use it

1. Run `make roadmap-status STATUS="<status>"` for the status you care about.
2. Read the table to find the item(s) relevant to the task.
3. **`Read` the `Path`** of an item to get its full proposal text — that column is exactly what to
   open next. For the Japanese mirror, swap the `.md` suffix for `-ja.md`.

Keep the survey narrow: pull only the status you need, then open only the items that matter — that
is the whole point of the filter over reading the index wholesale.
