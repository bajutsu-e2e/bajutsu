**English** · [日本語](BE-0109-roadmap-tracking-issues-ja.md)

# BE-0109 — GitHub Issues as the ownership tracker for open roadmap items

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0109](BE-0109-roadmap-tracking-issues.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0109") |
| Implementing PR | [#490](https://github.com/bajutsu-e2e/bajutsu/pull/490) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

Every **open** roadmap item gets a GitHub Issue. An open item is one whose `Status` is `Approved` or
`In progress`, meaning it is neither shipped nor shelved. The issue's native **Assignees** field
then becomes the single source of truth for who, if anyone, is working on the item.

An item gets its issue as soon as it exists as a proposal. An issue with **no** assignee is
therefore exactly the signal the roadmap lacks today: nobody has picked the item up yet.

The issues are created and closed automatically. Two inputs decide: the item's `Status`, and
whether a `Tracking issue` is already recorded. The design adds no custom field, dashboard, or
manual process.

## Motivation

Several people and agents work in this repo at once (CLAUDE.md's "Working in parallel without
breaking each other"). The existing guidance is "stay in your lane", but nothing shows at a glance
which open items already have someone on them and which are still up for grabs.

A contributor (human or AI session) choosing the next item must grep open PRs or ask around to
avoid duplicate effort. No single place answers "what is unclaimed?"

The roadmap's `Author` field does not answer the question either. It records who *proposed* the
item, who is frequently not the person who ends up *building* it, and it never changes once set.
An item can sit `In progress`, or a proposal can wait for a taker, with no visible claim on it.

GitHub already ships an ownership primitive for exactly this: Issue **Assignees**. They are
filterable by `assignee:` and `no:assignee`, and they surface in the Issues list, in notifications,
and in `gh issue list --assignee <user>`.

With an issue behind every open item, the Issues list becomes the board the roadmap needs. Assigned
issues show who is on what, and the `no:assignee` filter lists the unclaimed backlog. The design
needs no bespoke "owner" field and no status dashboard. The actual gap is that nothing links a BE
item to an issue.

## Detailed design

The design keeps **GitHub itself as the source of truth** for both facts it tracks. The first fact
is who owns an item (the issue's Assignees). The second is whether a tracking issue already exists
(a labelled issue whose title carries the item's BE ID).

Nothing is written back into the repo. The sync therefore needs neither a commit to `main` nor the
bypass App that the main-push of `roadmap-id` requires. The default token's `issues: write` is
enough.

1. **Issue existence is keyed off GitHub, not a repo field.** A tracking issue for `BE-NNNN` is an
   open issue that carries the `roadmap-tracking` label and has `BE-NNNN` in its title. The sync
   asks GitHub whether that issue exists (`gh issue list --label roadmap-tracking --state
   open --search "BE-NNNN in:title"`) instead of reading a row out of the BE file.

   This is what lets the design avoid writing anything back to the repo. It also keeps the
   assignee, the state that changes most often, only where GitHub already manages it.
2. **Lifecycle rule: a pure function of an item's current `Status`.** For every numbered item:
   - If `Status` is `Approved` or `In progress` (an *open* item) and no matching open issue exists,
     the sync creates one.
   - If a matching open issue exists but `Status` is `Implemented`, `Deferred`, or `Rejected`
     (shipped or shelved), the sync closes it. Un-shelving (`Deferred` → `Approved`) re-opens the
     item, so the next run re-creates the issue.

   Existence derives from the current `Status` alone, never from a PR diff. That makes the sync
   self-healing and idempotent, like the rest of the roadmap tooling (BE-0043, BE-0061). Running it
   twice, or against an already-consistent set, is a no-op. A merge race cannot leave two tracking
   issues for one item, because the second run sees the issue the first run created.
3. **Runs on `main`, after IDs are final, and skips `BE-0109`.** A new proposal carries the literal
   `BE-0109` placeholder through its PR. `roadmap-id` allocates the real `BE-NNNN` on `main` after
   the merge (BE-0089), so an issue titled with a real number cannot be created on the PR.

   The sync therefore runs on `push: main` (paths `roadmaps/**`) and skips any `BE-0109` item. The
   allocation commit that renames the placeholder is itself a `roadmaps/**` change on `main`. It
   re-triggers this workflow, and the second pass picks up the now-numbered item. The design has no
   ordering dependency on `roadmap-id` and no placeholder issues to rename.
4. **New script `scripts/sync_roadmap_tracking_issues.py`.** The script is shaped like
   `promote_roadmap_items.py`. It scans every numbered item across all four folders, reusing that
   script's `read_status`, and applies the lifecycle rule through the `gh` CLI (`gh issue create`
   and `gh issue close`).

   A `--check` mode reports drift: open items missing an issue, or issues that should be closed. It
   uses only reads (`gh issue list`) and mutates nothing. A maintainer or the dashboard can run it
   as an audit.
5. **Issue shape.** The title is `[BE-NNNN] <item title>`. The body links back to the item's English
   file and quotes its `Introduction`. The label is `roadmap-tracking`, a new repo label. It keeps
   these issues apart from `bug` / `enhancement` and makes the existence query unambiguous.

   The sync creates each issue **unassigned**. Whoever picks up the work self-assigns, as on any
   other GitHub issue. The Issues list is then the board the roadmap wanted:
   `label:roadmap-tracking assignee:<user>` is one person's plate, and
   `label:roadmap-tracking no:assignee` is the unclaimed backlog.
6. **New workflow `roadmap-tracking-issues.yml`.** On `push: main` (paths `roadmaps/**`), one job
   runs the sync script against the checked-out tree. The job holds `issues: write`, plus
   `contents: read` to check out.

   The workflow pushes no branch back, commits nothing to `main`, and needs no bypass App. It is a
   strict subset of what `roadmap-promote` and `roadmap-id` already do. When the set is already
   consistent, the job stays green as a no-op.
7. **One-time backfill.** The roadmap already has a backlog of open proposals and in-progress items
   with real IDs. The first run of the sync (or a manual `gh workflow run`) creates their issues in
   one pass. The lifecycle rule is idempotent, so the backfill needs no special-casing.
8. **Not part of `make check`.** The script calls the network (`gh`), so it never runs inside the
   deterministic gate. `--check` only *reads* to report drift, and it stays an opt-in command. It
   needs `gh auth` and network access, which the gate deliberately never requires.
9. **Documentation.** `docs/ai-development.md` gets a short subsection alongside the existing
   `roadmap-id` explanation. The subsection describes the lifecycle rule and the two saved filters.
   It also tells contributors what to do: before starting an item, check its tracking issue for an
   assignee, and self-assign when you pick the item up.

## Alternatives considered

- **Restrict tracking issues to `In progress` items.** An earlier shape of this proposal opened an
  issue only once work started. Its reasoning: most proposals are never picked up, so an issue per
  proposal is noise.

  That reasoning inverts the actual goal. The point is to see *which proposals are unclaimed*, and
  an item with no issue at all is invisible, not "available". An issue for every open item
  (proposal or in progress) makes the unclaimed backlog a first-class, filterable list
  (`no:assignee`). Contributors no longer have to reconstruct it. The `roadmap-tracking` label and
  the assignee filters answer the noise concern; withholding issues does not.
- **An `Assignee` field in the BE metadata block instead of an Issue.** Rejected. Who is assigned
  changes far more often than the rest of an item's metadata (its proposal text, its topic). Every
  claim or hand-off would be a file edit, which means a review, a diff, and a chance of merge
  conflict, all for information GitHub already tracks natively and for free. Keying ownership *and*
  issue existence off GitHub is what lets the sync avoid touching the repo at all.
- **A `Tracking issue` back-reference row in the BE metadata.** The row is tempting as a
  convenience, because a human reading the file sees the issue link. But writing the resolved issue
  *number* back would force a commit to protected `main`, and therefore the bypass App. That
  re-introduces the dependency the GitHub-as-source-of-truth design removes. The backlink is better
  carried the other way, from the issue body to the file.

  BE-0139 later added a `Tracking issue` row that sidesteps this problem. Its value is a GitHub
  issue-*search* URL computed from the item's immutable id. The row needs no live lookup, and it
  never changes as the issue is opened, assigned, or closed. The generated dashboard (BE-0094)
  surfaces the same search URL per card, without the read-only `gh issue list` once anticipated
  here.
- **A GitHub Projects board instead of Issues.** A project board gives a kanban view "for free",
  but it needs its own item-to-project sync and the heavier Projects v2 GraphQL API. It also needs
  a `read:project` OAuth scope that this environment lacks: `gh project list` fails on the missing
  scope, confirmed while researching this proposal.

  Plain Issues are simpler, the repo already uses them (the `bug_report` / `feature_request`
  templates), and they suffice for the stated need. A Projects board is not precluded later. It
  could layer on top of the same Issues without changing this design.
- **Run the sync on the PR (`roadmap-promote`) rather than on `main`.** Rejected, because a new
  proposal still carries the `BE-0109` placeholder on its PR, so no real-numbered issue can be
  created there. Running on `main` after allocation and skipping `BE-0109` keeps every issue
  titled with a permanent ID.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] 1. `scripts/sync_roadmap_tracking_issues.py` — GitHub-keyed existence check, create/close
  lifecycle, `BE-XXXX`-placeholder skip, and `--check`.
- [x] 2. `roadmap-tracking` label (created on demand by the sync), and the issue title/body shape.
- [x] 3. New workflow `roadmap-tracking-issues.yml` on `push: main` (`issues: write`).
- [x] 4. One-time backfill of the existing open backlog (runs on the first `push: main`, or a manual
  `gh workflow run roadmap-tracking-issues.yml`, once the workflow is on `main`).
- [x] 5. Document the lifecycle rule and the assignee filters in `docs/ai-development.md`
  (+ Japanese mirror).

Log:

- Implemented the sync script, the `push: main` workflow, its tests, and the docs (both languages);
  filed the item under `implemented/`. The one-time backfill (box 4) happens automatically on the
  first `roadmaps/**` push to `main` after this merges, since the lifecycle rule is idempotent.
- Confirmed the one-time backfill ran: tracking issues now exist for the open backlog.

## References

- [`roadmaps/README.md`](../README.md) — the BE ID / metadata conventions this extends.
- [`scripts/promote_roadmap_items.py`](../../scripts/promote_roadmap_items.py) — the
  pure-function-of-current-state pattern that this item's sync script follows.
- [`.github/workflows/roadmap-id.yml`](../../.github/workflows/roadmap-id.yml) — the
  `push: main` roadmap workflow that this new one sits beside. Allocation runs there, so the sync's
  `BE-0109` skip depends on it.
- [BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md) —
  merge-time ID allocation on `main`, which is why the sync runs on `main` and skips `BE-0109`.
- [BE-0094](../BE-0094-roadmap-status-dashboard/BE-0094-roadmap-status-dashboard.md) —
  the generated dashboard that could render item → tracking-issue links.
- [BE-0139](../BE-0139-roadmap-dashboard-issue-links/BE-0139-roadmap-dashboard-issue-links.md) —
  the item that links back to these tracking issues from every item file and dashboard card, using a
  deterministic issue-search URL built from the id (no live `gh issue list`).
- [BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md) —
  the conflict-resistant / self-healing file-flow precedent this design follows.
- [BE-0061](../BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md) —
  the atomic-allocation precedent for CI mutating roadmap state idempotently.
