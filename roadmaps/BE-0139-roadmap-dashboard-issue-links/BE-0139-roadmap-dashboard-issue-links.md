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

Add a link to the item's GitHub tracking issue (BE-0109) — the place ownership (Assignees) and
discussion actually live — in both places a reader looks at a roadmap item: a second, small link
on each card of the [roadmap status dashboard](https://bajutsu-e2e.github.io/bajutsu/api/roadmap.html)
(BE-0094), and a new row in the item's own metadata block (its `.md` file under `roadmaps/`). Both
links are the same deterministic GitHub issue *search* URL, built from the item's id alone, never a
live lookup of a specific issue number — so neither the dashboard build nor the item's own file ever
depends on network access, a GitHub token, or state that changes after the file is written.

## Motivation

BE-0094 already renders one link per dashboard card, to the item's Markdown proposal on GitHub.
BE-0109 separately opens a GitHub issue titled `[BE-NNNN] <title>` for every item that is or was
`Proposal` / `In progress`, and makes that issue's Assignees the sole source of truth for who, if
anyone, is working on it. Nothing today points from an item to that issue: a reader has to leave
wherever they're looking, open the repository's Issues tab, and either already know the
`roadmap-tracking` label and the `in:title BE-NNNN` search convention, or reconstruct it by hand.

That gap matters because a reader asks two questions of the same item back to back — "what is this
item" (the proposal text) and "is anyone already on it" (the issue, its Assignees) — and today only
the first is answered where the reader is looking. It also matters in two distinct places, not one:
a reader reaches an item's own file directly just as often as through the dashboard — a link from
another document, a PR description, a GitHub code search, or the plain-text
[`roadmaps/README.md`](../README.md) index — and none of those paths pass through the dashboard at
all. Putting the link only on the derived HTML rendering would leave every one of those paths
without it. Closing the gap at the source, the item's own file, is what makes it reach every reader
regardless of how they got there; the dashboard link is then the same fact surfaced a second time,
next to the proposal link it already shows.

## Detailed design

1. **A deterministic search URL, not a live issue lookup, used by both surfaces.** BE-0109's issue
   titles are always `[BE-NNNN] <item title>` and always carry the `roadmap-tracking` label, so the
   item's id alone is enough to build a GitHub issue search URL that finds it without knowing its
   issue number:
   `https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-NNNN"`.
   The query has no `is:open` filter, so it finds the issue whether it is still open or was later
   closed (an item that shipped after being `Proposal` / `In progress` keeps its issue, closed).
   Building the URL needs only the id string — no `gh` call, no GitHub token, no network access, at
   build time or at authoring time.
2. **Dashboard: a card change.** `scripts/build_roadmap_dashboard.py`'s `_card()` gains a second,
   smaller link next to the existing proposal-file link (e.g. a compact "Issue" pill), pointing at
   the search URL above. The card's primary click target — the proposal file — is unchanged; the
   new link is additive.
3. **Item file: a new `Tracking issue` metadata row.** Every numbered item's own `.md` file gets a
   new row in its fenced metadata block, `Tracking issue`, whose value is the same search-URL link
   (`| Tracking issue | [Search](…) |`). Unlike `Implementing PR` — filled in by hand once a PR
   ships — this field needs no author judgment at all: its value is a pure function of the file's
   own `BE-NNNN` id, so it is exactly as mechanical as the header link or the `# BE-NNNN — …` title
   the format already requires. This is *not* the "write the issue number back" idea BE-0109 already
   rejected (see *Alternatives considered*) — the value never changes once written, whether the
   issue is later opened, assigned, or closed, so it needs no upkeep and no write-back tied to the
   issue's lifecycle.
4. **Canonical schema, scaffold, and backfill.** `Tracking issue` joins the canonical field order
   `tests/test_roadmap_format.py` (`ORDER_EN` / `ORDER_JA`) pins, placed right after `Status` (before
   `Implementing PR`), and becomes a required field alongside `Proposal` / `Author` / `Status` /
   `Topic`. `scripts/new_roadmap_item.py`'s scaffold gains it for every newly created item — computed
   at scaffold time from the literal `BE-0139` placeholder, so it reads `…in:title+"BE-0139"…` until
   allocation, exactly like the header link and the `Proposal` row already do. A one-time mechanical
   backfill script adds the row to every existing numbered item (both language files), computing each
   value from the file's own already-known id — no author judgment needed there either, so it is a
   single script run, not ~120 hand edits. CLAUDE.md's canonical-field-order sentence is updated to
   name the new field.
5. **`BE-0139` placeholders resolve for free.** Because the row's value is literal text containing
   `BE-0139` until allocation, `scripts/allocate_roadmap_ids.py`'s existing whole-file
   `text.replace(old_token, new_token)` (BE-0089) already rewrites it to the real `BE-NNNN` in the
   same pass that fixes the header link and the title — no new allocator logic needed.
6. **Validation.** Beyond the existing field-order and required-field checks,
   `tests/test_roadmap_format.py` gains a check that every numbered item's `Tracking issue` value is
   *exactly* the search URL its own id predicts — catching a stale value left over from copy-pasting
   another item's file, the one failure mode a purely mechanical field can have.
7. **No change to BE-0109's sync, label, or workflow.** The search URL is derived purely from the id
   and the fixed title/label convention BE-0109 already guarantees; nothing needs to be read back
   from GitHub, so `sync_roadmap_tracking_issues.py` and `roadmap-tracking-issues.yml` are untouched.
8. **The link can legitimately return zero results.** BE-0109 only opens an issue for an item that
   was, at some point, `Proposal` or `In progress` when the sync ran. An item shipped as
   `Implemented` in the same PR that introduced it — "born implemented" — never passes through an
   open status, so it never gets an issue, and its search link shows no results. Neither the
   dashboard nor the item file can know this in advance without querying GitHub, which would
   reintroduce the network dependency this design avoids (point 1). Both links are therefore
   labelled and documented as a search, not a guaranteed issue, so a reader who lands on an empty
   results page understands why.
9. **Docs.** This item's own file; a short cross-reference added to BE-0109's References list
   pointing at the shipped design (superseding the "could render item → issue links from a
   read-only `gh issue list`" line in its Alternatives considered, which this item's design
   deliberately does not follow — see below); and the CLAUDE.md field-order update from point 4.

## Alternatives considered

- **A live `gh issue list` lookup**, at dashboard build time or when the `Tracking issue` row is
  authored, resolving each item's real issue number instead of a search URL. This is what BE-0109's
  own References anticipated for the dashboard ("the generated dashboard (BE-0094) can render item →
  issue links from a read-only `gh issue list`"). Rejected for both surfaces: `make docs` and
  `make docs-serve` regenerate the dashboard today with "only stdlib" — no network, no `gh auth` —
  and the `docs` GitHub Actions workflow builds it with no `issues:` permission at all; a live lookup
  would require both a local `gh` session and a new `issues: read` grant in CI. Authoring the item
  file's row from a live lookup is worse: the real issue number wouldn't exist yet for a brand-new
  proposal (BE-0109 only creates it once the id is allocated on `main`), so the field couldn't even
  be filled at scaffold time. A search URL needs neither dependency and is fillable from the moment
  the placeholder id exists.
- **Only the dashboard, not the item's own file** — this item's original scope. Rejected: an item's
  file is reached directly at least as often as through the dashboard (a link from another document,
  a PR description, a code search, the plain-text index), and none of those paths render the
  dashboard at all. Putting the link only on the derived HTML view would leave the source file, and
  every reader who reaches it directly, without it.
- **Writing the actual issue *number* back into the item's metadata block** (e.g. a `Tracking issue`
  row whose value is the real, resolved issue link). Already rejected once, by BE-0109 itself: it
  would force a commit to protected `main` through the bypass App for state — which issue is open,
  who is assigned — that changes far more often than the rest of an item's metadata, undermining the
  "GitHub is the sole source of truth" design BE-0109 is built around. This item's `Tracking issue`
  row looks similar but carries different content: a URL computed once from the immutable `BE-NNNN`
  id, identical for every reader and never touched again regardless of what happens to the issue
  behind it — so it needs no live lookup and no write-back tied to the issue's lifecycle, and doesn't
  reopen the question BE-0109 already settled.
- **Replacing the proposal-file link (dashboard) or another existing row (item file) with the issue
  link.** Rejected: the proposal file, and the item's existing metadata, are what a reader wants
  first; the issue is a thin, secondary pointer to ownership and discussion. Adding it as an
  additional link/row preserves every existing click-through and row unchanged.

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

- Shipped the whole item in one change: the `tracking_issue_url` helper in
  `scripts/build_roadmap_index.py` (the shared source of truth), the additive "Issue" pill on each
  dashboard card, the required `Tracking issue` metadata field with its value-matches-own-id check,
  the scaffold row, the one-time backfill of all existing items, and the CLAUDE.md / BE-0109 doc
  updates. Filed the item under `implemented/`.

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
