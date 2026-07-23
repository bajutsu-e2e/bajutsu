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

Open a GitHub Issue for every **open** roadmap item — one whose `Status` is `Proposal` or `In
progress`, i.e. everything not yet shipped or shelved — and let that issue's native **Assignees**
be the single source of truth for "who, if anyone, is working on this." Because an item gets its
issue the moment it exists as a proposal, an issue with **no** assignee is exactly the signal the
roadmap lacks today: *nobody has picked this up yet*. The issues are created and closed
automatically, driven only by the item's `Status` and whether a `Tracking issue` is already
recorded — no new custom field, dashboard, or process to maintain by hand.

## Motivation

Several people and agents work this repo at once (CLAUDE.md's "Working in parallel without
breaking each other"), and the existing guidance is "stay in your lane" — but nothing today makes
it visible, at a glance, which open items already have someone on them and which are still up for
grabs. A contributor (human or AI session) picking the next item to work on has to grep open PRs
or ask around to avoid duplicating effort, and there is no single place that answers "what is
unclaimed?"

The roadmap's `Author` metadata field doesn't answer this either — it records who *proposed* the
item, which is frequently a different person from whoever ends up *building* it, and it never
changes once set. An item can sit `In progress`, or a proposal can wait for a taker, with no
visible claim on it at all.

GitHub already ships a first-class ownership primitive for exactly this — Issue **Assignees**,
filterable by `assignee:` and `no:assignee`, surfaced in the Issues list, notifications, and `gh
issue list --assignee <user>`. With an issue behind every open item, the Issues list becomes the board
the roadmap needs: assigned issues show who is on what, and the `no:assignee` filter is the
backlog of unclaimed work. There's no need to invent a bespoke "owner" field or a status
dashboard; the actual gap is that nothing links a BE item to an issue.

## Detailed design

The design keeps **GitHub itself as the source of truth** for both facts it tracks: who owns an
item (the issue's Assignees) *and* whether a tracking issue already exists (a labelled issue whose
title carries the item's BE ID). Nothing is written back into the repo, so the sync needs neither
a commit to `main` nor the bypass App that `roadmap-id`'s main-push requires — only `issues:
write` on the default token.

1. **Issue existence is keyed off GitHub, not a repo field.** A tracking issue for `BE-NNNN` is
   identified as an open issue carrying the `roadmap-tracking` label with `BE-NNNN` in its title.
   The sync asks GitHub whether that issue exists (`gh issue list --label roadmap-tracking --state
   open --search "BE-NNNN in:title"`) rather than reading a row out of the BE file. This is what lets
   the design avoid writing anything back to the repo — and it means the assignee, the one piece of
   state that changes most often, lives only where GitHub already manages it.
2. **Lifecycle rule — a pure function of an item's current `Status`.** For every numbered item:
   - `Status` is `Proposal` or `In progress` (an *open* item) and no matching open issue exists →
     create one.
   - A matching open issue exists but the item's `Status` is `Implemented` or `Proposal (deferred)`
     (shipped, or shelved) → close it. Un-shelving (`Proposal (deferred)` → `Proposal`) re-opens
     the item, so the next run re-creates the issue.
   Deriving existence from current `Status` alone — never from a PR diff — makes the sync
   self-healing and idempotent, matching the rest of the roadmap tooling (BE-0043, BE-0061):
   running it twice, or against an already-consistent set, is a no-op, and a merge race can't leave
   two tracking issues for one item, because the second run sees the first run's issue.
3. **Runs on `main`, after IDs are final — skips `BE-0109`.** A new proposal carries the literal
   `BE-0109` placeholder through its PR; its real `BE-NNNN` is allocated by `roadmap-id` on `main`
   after the merge (BE-0089). An issue titled with a real number therefore can't be created on the
   PR. So the sync runs on `push: main` (paths `roadmaps/**`) and simply **skips any `BE-0109`
   item**: the allocation commit that renames the placeholder is itself a `roadmaps/**` change on
   `main`, which re-triggers this workflow, and the now-numbered item is picked up on that second
   pass. No ordering dependency on `roadmap-id`, no placeholder issues to rename.
4. **New script — `scripts/sync_roadmap_tracking_issues.py`.** Shaped like
   `promote_roadmap_items.py`: scans every numbered item across all four folders (reusing that
   script's `read_status`), applies the lifecycle rule via the `gh` CLI (`gh issue create` / `gh
   issue close`). A `--check` mode reports drift (open items missing an issue, or issues that
   should be closed) using only reads (`gh issue list`), mutating nothing — the audit a maintainer
   or the dashboard can run.
5. **Issue shape.** Title `[BE-NNNN] <item title>`; body links back to the item's English file and
   quotes its `Introduction`; label `roadmap-tracking` (a new repo label) so these filter
   separately from `bug` / `enhancement`, and so the existence query is unambiguous. Created
   **unassigned** — whoever picks up the work self-assigns, exactly as on any other GitHub issue.
   The Issues list is then the board the roadmap wanted: `label:roadmap-tracking assignee:<user>`
   is one person's plate, and `label:roadmap-tracking no:assignee` is the unclaimed backlog.
6. **New workflow — `roadmap-tracking-issues.yml`.** On `push: main` (paths `roadmaps/**`), one
   job with `issues: write` (plus `contents: read` to check out) running the sync script against
   the checked-out tree. No branch push-back, no commit to `main`, no bypass App — a strict subset
   of what `roadmap-promote` / `roadmap-id` already do. It stays green as a no-op when the set is
   already consistent.
7. **One-time backfill.** The roadmap already has a backlog of open proposals and in-progress
   items with real IDs. The first run of the sync (or a manual `gh workflow run`) creates their
   issues in one pass; the lifecycle rule is idempotent, so this needs no special-casing.
8. **Not part of `make check`.** The script calls the network (`gh`), so it never runs inside the
   deterministic gate. `--check` only *reads* to report drift and stays an opt-in command — it
   needs `gh auth` and network access, which the gate deliberately never requires.
9. **Documentation.** `docs/ai-development.md` gets a short subsection (alongside the existing
   `roadmap-id` explanation) describing the lifecycle rule and the two saved filters, plus a
   pointer for contributors: before starting an item, check its tracking issue for an assignee, and
   self-assign when you pick it up.

## Alternatives considered

- **Restrict tracking issues to `In progress` items only.** An earlier shape of this proposal
  opened an issue only once work started, reasoning that most proposals are never picked up and an
  issue per proposal is noise. But that inverts the actual goal: the point is to see *which
  proposals are unclaimed*, and an item with no issue at all is invisible, not "available." Opening
  an issue for every open item (proposal or in-progress) makes the unclaimed backlog a first-class,
  filterable list (`no:assignee`) instead of something a contributor has to reconstruct. The noise
  concern is answered by the `roadmap-tracking` label and the assignee filters, not by withholding
  issues.
- **An `Assignee` field in the BE metadata block instead of an Issue.** Rejected: who's assigned
  changes far more often than the rest of an item's metadata (its proposal text, its topic), so
  every claim or hand-off would be a file edit — a review, a diff, a merge-conflict opportunity —
  for information GitHub already tracks natively and for free. Keying ownership *and* issue
  existence off GitHub is what lets the sync avoid touching the repo at all.
- **A `Tracking issue` back-reference row in the BE metadata.** Tempting as a convenience (a
  human reading the file sees the issue link), but writing the resolved issue *number* back would
  force a commit to protected `main` and thus the bypass App, re-introducing exactly the dependency
  the GitHub-as-source-of-truth design removes. The backlink is better carried the other direction —
  from the issue body to the file. BE-0139 later added a `Tracking issue` row that sidesteps this: its
  value is a GitHub issue-*search* URL computed from the item's immutable id, so it needs no live
  lookup and never changes as the issue is opened, assigned, or closed — and the same search URL is
  what the generated dashboard (BE-0094) surfaces per card, without the read-only `gh issue list`
  once anticipated here.
- **A GitHub Projects board instead of Issues.** A project board gives a kanban view "for free,"
  but needs its own item ↔ project sync, the heavier Projects v2 GraphQL API, and a `read:project`
  OAuth scope this environment doesn't currently have (`gh project list` fails on a missing scope,
  confirmed while researching this proposal). Plain Issues are simpler, already used in this repo
  (the `bug_report` / `feature_request` templates), and sufficient for the stated need. A Projects
  board isn't precluded later — it could layer on top of the same Issues without changing this
  design.
- **Run the sync on the PR (`roadmap-promote`) rather than on `main`.** Rejected because a new
  proposal still carries the `BE-0109` placeholder on its PR, so no real-numbered issue can be
  created there. Running on `main` after allocation, and skipping `BE-0109`, is what keeps every
  issue titled with a permanent ID.

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
  pure-function-of-current-state pattern this item's sync script follows.
- [`.github/workflows/roadmap-id.yml`](../../.github/workflows/roadmap-id.yml) — the
  `push: main` roadmap workflow this new one sits beside; allocation runs here, so the sync's
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
