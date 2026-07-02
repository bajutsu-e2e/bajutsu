**English** · [日本語](BE-XXXX-roadmap-dashboard-issue-links-ja.md)

# BE-XXXX — Link roadmap dashboard cards to their tracking issue

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-roadmap-dashboard-issue-links.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Topic | Development infrastructure (contributor workflow) |
<!-- /BE-METADATA -->

## Introduction

Add a second, small link on each card of the [roadmap status dashboard](https://bajutsu-e2e.github.io/bajutsu/api/roadmap.html)
(BE-0094) that points to the item's GitHub tracking issue (BE-0109) — the place ownership
(Assignees) and discussion actually live. The link is a deterministic GitHub issue *search* URL
built from the item's id, not a live lookup of a specific issue number, so the dashboard build
stays free of network calls and a GitHub token, exactly as it is today.

## Motivation

BE-0094 already renders one link per card, to the item's Markdown proposal on GitHub. BE-0109
separately opens a GitHub issue titled `[BE-NNNN] <title>` for every item that is or was `Proposal`
/ `In progress`, and makes that issue's Assignees the sole source of truth for who, if anyone, is
working on it. Nothing on the dashboard points from a card to that issue: a reader has to leave
the dashboard, open the repository's Issues tab, and either already know the `roadmap-tracking`
label and the `in:title BE-NNNN` search convention, or reconstruct it by hand.

That gap matters because the two pages answer different questions the same reader asks back to
back — "what is this item" (the card, the proposal) and "is anyone already on it" (the issue,
its Assignees) — and today only the first is one click away. Closing the second hop turns the
dashboard into the single starting point for both questions, without introducing a new source of
truth: the card still links to the proposal, and the new link only routes to state GitHub already
owns.

## Detailed design

1. **A deterministic search URL, not a live issue lookup.** BE-0109's issue titles are always
   `[BE-NNNN] <item title>` and always carry the `roadmap-tracking` label, so the item's id alone
   is enough to build a GitHub issue search URL that finds it without knowing its issue number:
   `https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-NNNN"`.
   The query has no `is:open` filter, so it finds the issue whether it is still open or was later
   closed (an item that shipped after being `Proposal` / `In progress` keeps its issue, closed).
   Building the URL needs only the id string — no `gh` call, no GitHub token, no network access at
   build time.
2. **Card change only.** `scripts/build_roadmap_dashboard.py`'s `_card()` gains a second, smaller
   link next to the existing proposal-file link (e.g. a compact "Issue" pill), pointing at the
   search URL above. The card's primary click target — the proposal file — is unchanged; the new
   link is additive.
3. **No change to BE-0109's sync, label, or workflow.** The search URL is derived purely from the
   id and the fixed title/label convention BE-0109 already guarantees; nothing needs to be read
   back from GitHub, so `sync_roadmap_tracking_issues.py` and `roadmap-tracking-issues.yml` are
   untouched.
4. **The link can legitimately return zero results.** BE-0109 only opens an issue for an item that
   was, at some point, `Proposal` or `In progress` when the sync ran. An item shipped as
   `Implemented` in the same PR that introduced it — "born implemented" — never passes through an
   open status, so it never gets an issue, and its search link shows no results. The dashboard
   cannot know this in advance without querying GitHub, which would reintroduce the network
   dependency this design avoids (point 1). The link is therefore labelled and documented as a
   search, not a guaranteed issue, so a reader who lands on an empty results page understands why.
5. **Docs.** This item's own file, and a short cross-reference added to BE-0109's References list
   pointing at the shipped design (superseding the "could render item → issue links from a
   read-only `gh issue list`" line in its Alternatives considered, which this item's design
   deliberately does not follow — see below).

## Alternatives considered

- **A live `gh issue list` lookup at dashboard build time**, resolving each item's real issue
  number instead of a search URL. This is what BE-0109's own References anticipated ("the
  generated dashboard (BE-0094) can render item → issue links from a read-only `gh issue list`").
  Rejected: `make docs` and `make docs-serve` regenerate the dashboard today with "only stdlib" —
  no network, no `gh auth` — and the `docs` GitHub Actions workflow builds it with no `issues:`
  permission at all. A live lookup would require both a local `gh` session and a new
  `issues: read` grant in CI, turning a page whose entire premise (BE-0094) is "derived purely
  from committed metadata" into one that also depends on live external state at build time. A
  search URL gets the same destination without either dependency.
- **Writing the issue number back into the item's metadata block** (e.g. a `Tracking issue` row).
  Already rejected once, by BE-0109 itself: it would force a commit to protected `main` through the
  bypass App for state — which issue is open, who is assigned — that changes far more often than
  the rest of an item's metadata, undermining the "GitHub is the sole source of truth" design BE-0109
  is built around. This item doesn't reopen that question; it only adds a link computed at render
  time, which needs no such write-back.
- **Replacing the proposal-file link with the issue link.** Rejected: the proposal file is the
  actual specification a reader wants first; the issue is a thin, secondary pointer to ownership
  and discussion. Keeping both as separate links preserves the card's existing primary
  click-through.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] 1. Add the deterministic tracking-issue search-URL helper and the card's secondary link in
  `scripts/build_roadmap_dashboard.py`.
- [ ] 2. Update `tests/test_roadmap_dashboard.py` to cover the new link.
- [ ] 3. Cross-reference this item from BE-0109's References (superseding its "could render item →
  issue links from a read-only `gh issue list`" note).

## References

- [BE-0094 — Generated roadmap status dashboard on GitHub Pages](../../implemented/BE-0094-roadmap-status-dashboard/BE-0094-roadmap-status-dashboard.md) —
  the dashboard this item adds a link to.
- [BE-0109 — GitHub Issues as the ownership tracker for open roadmap items](../../implemented/BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md) —
  the tracking-issue title/label convention this item's search URL relies on.
- [`scripts/build_roadmap_dashboard.py`](../../../scripts/build_roadmap_dashboard.py) — the
  generator this item changes.
