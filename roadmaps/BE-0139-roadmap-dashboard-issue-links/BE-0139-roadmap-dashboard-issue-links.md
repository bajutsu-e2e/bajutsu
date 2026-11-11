**English** · [日本語](BE-0139-roadmap-dashboard-issue-links-ja.md)

# BE-0139 — Link the roadmap dashboard and item files to their tracking issue

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0139](BE-0139-roadmap-dashboard-issue-links.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0139") |
| Implementing PR | [#597](https://github.com/bajutsu-e2e/bajutsu/pull/597) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

This item adds a link to the item's GitHub tracking issue (BE-0109), where ownership (Assignees) and
discussion live. The link goes in both places where a reader looks at a roadmap item:

- a second, small link on each card of the
  [roadmap status dashboard](https://bajutsu-e2e.github.io/bajutsu/api/roadmap.html) (BE-0094)
- a new row in the item's own metadata block (its `.md` file under `roadmaps/`)

Both links are the same deterministic GitHub issue *search* URL. The URL is built from the item's id
alone and never looks up a specific issue number. Neither the dashboard build nor the item's own file
depends on network access, a GitHub token, or state that changes after the file is written.

## Motivation

BE-0094 already renders one link per dashboard card, to the item's Markdown proposal on GitHub. BE-0109
separately opens a GitHub issue titled `[BE-NNNN] <title>` for every item that is or was `Approved` or
`In progress`. The issue's Assignees are the sole source of truth for who, if anyone, is working on the
item.

Nothing today points from an item to that issue. A reader has to leave the current page and open the
repository's Issues tab. The reader then needs to know the `roadmap-tracking` label and the
`in:title BE-NNNN` search convention, or reconstruct them by hand.

The gap matters because a reader asks two questions of the same item back to back: "what is this item"
(the proposal text) and "is anyone already on it" (the issue and its Assignees). Today, only the first
question is answered where the reader is looking.

The gap also appears in two distinct places, not one. A reader reaches an item's own file directly just
as often as through the dashboard. The routes include a link from another document, a PR description, a
GitHub code search, and the plain-text [`roadmaps/README.md`](../README.md) index. None of those routes
passes through the dashboard. A link on the derived HTML rendering alone would leave every one of them
without it.

Closing the gap at the source, the item's own file, reaches every reader regardless of how they got
there. The dashboard link is then the same fact surfaced a second time, next to the proposal link that
the card already shows.

## Detailed design

1. **A deterministic search URL, not a live issue lookup, used by both surfaces.** BE-0109's issue
   titles are always `[BE-NNNN] <item title>`, and the issues always carry the `roadmap-tracking` label.
   The item's id alone therefore builds a GitHub issue search URL that finds the issue without knowing
   its number:
   `https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-NNNN"`.
   The query has no `is:open` filter, so it finds the issue whether it is still open or was later
   closed. An item that shipped after being `Approved` or `In progress` keeps its issue, closed.
   Building the URL needs the id string and nothing else: no `gh` call, no GitHub token, and no network
   access, at build time or at authoring time.
2. **Dashboard: a card change.** `_card()` in `scripts/build_roadmap_dashboard.py` gains a second,
   smaller link next to the existing proposal-file link (for example, a compact "Issue" pill). The link
   points at the search URL above. The card's primary click target, the proposal file, is unchanged. The
   new link is additive.
3. **Item file: a new `Tracking issue` metadata row.** Every numbered item's own `.md` file gets a new
   row in its fenced metadata block, `Tracking issue`. The value is the same search-URL link
   (`| Tracking issue | [Search](…) |`).

   Unlike `Implementing PR`, which is filled in by hand once a PR ships, this row needs no author
   judgment at all.
   Its value is a pure function of the file's own `BE-NNNN` id, so it is as mechanical as the header
   link or the `# BE-NNNN — …` title that the format already requires.

   The row is *not* the "write the issue number back" idea that BE-0109 already rejected (see
   *Alternatives considered*). The value never changes once written, whether the issue is later opened,
   assigned, or closed. It needs no upkeep and no write-back tied to the issue's lifecycle.
4. **Canonical schema, scaffold, and backfill.** `Tracking issue` joins the canonical field order that
   `tests/test_roadmap_format.py` pins (`ORDER_EN` / `ORDER_JA`). It sits right after `Status`, before
   `Implementing PR`. It becomes a required field alongside `Proposal`, `Author`, `Status`, and `Topic`.
   - The scaffold in `scripts/new_roadmap_item.py` gains the row for every newly created item. The
     scaffold computes it from the literal `BE-0139` placeholder. The row reads `…in:title+"BE-0139"…`
     until allocation, exactly like the header link and the `Proposal` row.
   - A one-time mechanical backfill script adds the row to every existing numbered item, in both
     language files. It computes each value from the file's own already-known id. That also needs no
     author judgment, so the backfill is a single script run, not ~120 hand edits.
   - CLAUDE.md's canonical-field-order sentence is updated to name the new field.
5. **`BE-0139` placeholders resolve for free.** Until allocation, the row's value is literal text that
   contains `BE-0139`. The existing whole-file `text.replace(old_token, new_token)` in
   `scripts/allocate_roadmap_ids.py` (BE-0089) already rewrites it to the real `BE-NNNN`. The same pass
   fixes the header link and the title, so the allocator needs no new logic.
6. **Validation.** Beyond the existing field-order and required-field checks,
   `tests/test_roadmap_format.py` gains a check that every numbered item's `Tracking issue` value is
   *exactly* the search URL its own id predicts. The check catches a stale value left over from
   copy-pasting another item's file, the one failure mode that a purely mechanical field can have.
7. **No change to BE-0109's sync, label, or workflow.** The search URL derives from the id and from the
   fixed title and label convention that BE-0109 already guarantees. Nothing needs to be read back from
   GitHub, so `sync_roadmap_tracking_issues.py` and `roadmap-tracking-issues.yml` are untouched.
8. **The link can legitimately return zero results.** BE-0109 opens an issue only for an item that was
   `Approved` or `In progress` at some point when the sync ran. An item can ship as `Implemented` in the
   same PR that introduced it ("born implemented"). It never passes through an open status, so it never
   gets an issue, and its search link shows no results.

   Neither the dashboard nor the item file can know this in advance without querying GitHub. A query
   would reintroduce the network dependency that this design avoids (point 1). Both links are labelled
   and documented as a search, not a guaranteed issue, so a reader who lands on an empty results page
   understands why.
9. **Docs.** The docs change in three places:
   - This item's own file.
   - A short cross-reference in BE-0109's References list that points at the shipped design. The
     reference supersedes the "could render item → issue links from a read-only `gh issue list`" line in
     BE-0109's Alternatives considered. This item's design deliberately does not follow that line (see
     below).
   - The CLAUDE.md field-order update from point 4.

## Alternatives considered

- **A live `gh issue list` lookup**, at dashboard build time or when the `Tracking issue` row is
  authored, resolving each item's real issue number instead of a search URL. BE-0109's own References
  anticipated this for the dashboard: "the generated dashboard (BE-0094) can render item → issue links
  from a read-only `gh issue list`". Rejected for both surfaces.
  - `make docs` and `make docs-serve` regenerate the dashboard today with "only stdlib", with no network
    and no `gh auth`. The `docs` GitHub Actions workflow builds it with no `issues:` permission at all.
    A live lookup would require both a local `gh` session and a new `issues: read` grant in CI.
  - Authoring the item file's row from a live lookup is worse. The real issue number would not exist yet
    for a brand-new proposal, because BE-0109 creates the issue once the id is allocated on `main`. The
    field could not even be filled at scaffold time.
  - A search URL needs neither dependency. It is fillable from the moment the placeholder id exists.
- **The dashboard alone, not the item's own file.** This was this item's original scope. Rejected. An
  item's file is reached directly at least as often as through the dashboard, for example from another
  document, a PR description, a code search, or the plain-text index. None of those routes render the
  dashboard. A link on the derived HTML view alone would leave the source file, and every reader who
  reaches it directly, without the link.
- **Writing the actual issue *number* back into the item's metadata block** (for example, a
  `Tracking issue` row whose value is the real, resolved issue link). BE-0109 itself already rejected
  this once. The write-back would force a commit to protected `main` through the bypass App. The state
  involved (which issue is open, who is assigned) changes far more often than the rest of an item's
  metadata. That would undermine the "GitHub is the sole source of truth" design that BE-0109 is built
  around.

  This item's `Tracking issue` row looks similar but carries different content. It holds a URL computed
  once from the immutable `BE-NNNN` id. The URL is identical for every reader and never touched again,
  whatever happens to the issue behind it. It needs no live lookup and no write-back tied to the issue's
  lifecycle, and it does not reopen the question that BE-0109 already settled.
- **Replacing the proposal-file link (dashboard) or another existing row (item file) with the issue
  link.** Rejected. A reader wants the proposal file and the item's existing metadata first. The issue is
  a thin, secondary pointer to ownership and discussion. Adding it as an additional link or row keeps
  every existing click-through and row unchanged.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] 1. Add the deterministic tracking-issue search-URL helper and the card's secondary link in
  `scripts/build_roadmap_dashboard.py`; update `tests/test_roadmap_dashboard.py` to cover it.
- [x] 2. Add the `Tracking issue` field to the canonical schema (`tests/test_roadmap_format.py`'s
  `ORDER_EN` / `ORDER_JA` and required-field sets, plus the value-matches-its-own-id check) and to
  `scripts/new_roadmap_item.py`'s scaffold; update CLAUDE.md's field-order sentence.
- [x] 3. Run the one-time backfill script adding the `Tracking issue` row to every existing numbered
  item (both language files).
- [x] 4. Cross-reference this item from BE-0109's References (superseding its "could render item →
  issue links from a read-only `gh issue list`" note).

Log:

- Shipped the whole item in one change:
  - the `tracking_issue_url` helper in `scripts/build_roadmap_index.py` (the shared source of truth)
  - the additive "Issue" pill on each dashboard card
  - the required `Tracking issue` metadata field, with its value-matches-own-id check
  - the scaffold row
  - the one-time backfill of all existing items
  - the CLAUDE.md and BE-0109 doc updates
- Filed the item under `implemented/`.

## References

- [BE-0094 — Generated roadmap status dashboard on GitHub Pages](../BE-0094-roadmap-status-dashboard/BE-0094-roadmap-status-dashboard.md) —
  the dashboard this item adds a link to.
- [BE-0109 — GitHub Issues as the ownership tracker for open roadmap items](../BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md) —
  the tracking-issue title/label convention this item's search URL relies on, and the item whose
  *Alternatives considered* already rejected writing a live issue number back into the metadata block.
- [`scripts/build_roadmap_dashboard.py`](../../scripts/build_roadmap_dashboard.py) — the
  dashboard generator this item changes.
- [`scripts/new_roadmap_item.py`](../../scripts/new_roadmap_item.py) — the scaffold this item
  extends so every new item gets the field automatically.
- [`tests/test_roadmap_format.py`](../../tests/test_roadmap_format.py) — the per-item format
  check this item extends with the new field and its value check.
- [BE-0074 — Standardize the BE item template (EN / JA)](../BE-0074-be-template-standardization/BE-0074-be-template-standardization.md) —
  the format-pinning precedent `test_roadmap_format.py` follows, and the item whose canonical field
  order this item extends with `Tracking issue`.
