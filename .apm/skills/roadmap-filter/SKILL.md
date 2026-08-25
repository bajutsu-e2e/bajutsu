---
name: roadmap-filter
model: haiku
description: Find Bajutsu roadmap items by status, keyword, topic, or id. Use for read-only roadmap surveys, "is there already an item about X", and path lookup.
---

# Roadmap filter

Survey the roadmap without reading it. This skill is **read-only**: it prints one table
so you can pick the items to open in full — it never authors, implements, or edits an item.

## What it does

The [roadmap dashboard](https://bajutsu-e2e.github.io/bajutsu/api/roadmap.html) renders every item
as HTML, across five status buckets and two dozen topics. A session that needs a handful of items
should not page through the whole page. Close to 400 items live under `roadmaps/`, and their files
run past 127,000 lines. Grepping the item prose is no cheaper. Run the deterministic query instead,
with the filter that matches your question:

```bash
make roadmap-status STATUS="Proposal"                        # one status
make roadmap-find ARGS="--grep scroll"                       # is there already an item about X?
make roadmap-find ARGS="--status Implemented --topic driver" # one topic within one status
make roadmap-find ARGS="--id BE-0349"                        # one item, by id
```

`STATUS` is one of — matched case-insensitively:

- `Proposal` — open, not yet started
- `In progress` — being built
- `Implemented` — shipped
- `Deferred` — deliberately parked
- `Rejected` — decided against, with no condition expected to reopen it

An unknown status prints the valid values and exits non-zero, rather than an empty table.

### The filters

| Filter | Matches |
|---|---|
| `--status` | the item's `Status`, case-insensitively, against the five values above |
| `--grep` | a word, case-insensitively, against the id, title, `Topic`, and `## Introduction` excerpt |
| `--topic` | a substring of the item's `Topic`, case-insensitively |
| `--id` | one item, written as `BE-0349`, `0349`, `349`, or `BE-XXXX` for an in-flight proposal |

The filters compose, so `--status Proposal --topic driver` returns the intersection. Give at least
one filter. An unfiltered dump of every item is the cost the query exists to avoid.

The query stays pure and offline. It reads each item's own metadata under `roadmaps/`, the source
the index generator reads. No `gh`, no network, and no large language model (LLM) takes part.

The script also runs directly: `python scripts/roadmap_query.py --status "Proposal"`.
A keyword search takes `python scripts/roadmap_query.py --grep scroll`.

## Output

A Markdown table:

| Column | Meaning |
|---|---|
| `ID` | the item's `BE-NNNN` (or the `BE-XXXX` placeholder for an in-flight item) |
| `Item` | the item's title |
| `Status` | the item's `Status`; present unless `--status` already fixed it |
| `Topic` | the item's Topic (the index's secondary grouping) |
| `Path` | the relative path to the item's English `.md` file |

Rows are sorted by `Topic`, then `ID`.

## How to use it

1. Pick the filter that matches the question: a status survey takes `make roadmap-status`, and a
   keyword, topic, or id lookup takes `make roadmap-find`.
2. Read the table to find the item(s) relevant to the task.
3. **Open the file at the `Path`** of an item to get its full proposal text — that column is exactly what to
   open next. For the Japanese mirror, swap the `.md` suffix for `-ja.md`.

Keep the survey narrow: pull only the rows you need, then open only the items that matter — that
is the whole point of the filter over reading the roadmap wholesale.
