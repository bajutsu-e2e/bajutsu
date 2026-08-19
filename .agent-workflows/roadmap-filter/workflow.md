# Roadmap status filter

Survey the roadmap by `Status`. This skill is **read-only**: it prints one table
so you can pick the items to open in full — it never authors, implements, or edits an item.

## What it does

The [roadmap dashboard](https://bajutsu-e2e.github.io/bajutsu/api/roadmap.html) lists every item
across five status buckets and many topics, as rendered HTML — more than a session that only needs
one status wants to page through. When you only need the items in one status, run the deterministic
query instead:

```bash
make roadmap-status STATUS="Proposal"
```

`STATUS` is one of — matched case-insensitively:

- `Proposal` — open, not yet started
- `In progress` — being built
- `Implemented` — shipped
- `Deferred` — deliberately parked
- `Rejected` — decided against, with no condition expected to reopen it

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
3. **Open the file at the `Path`** of an item to get its full proposal text — that column is exactly what to
   open next. For the Japanese mirror, swap the `.md` suffix for `-ja.md`.

Keep the survey narrow: pull only the status you need, then open only the items that matter — that
is the whole point of the filter over reading the index wholesale.
