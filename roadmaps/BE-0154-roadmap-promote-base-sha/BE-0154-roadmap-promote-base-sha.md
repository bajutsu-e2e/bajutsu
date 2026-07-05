**English** · [日本語](BE-0154-roadmap-promote-base-sha-ja.md)

# BE-0154 — Run roadmap-promote from the base SHA

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0154](BE-0154-roadmap-promote-base-sha.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal (deferred)** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0154") |
| Topic | Security hardening |
| Related | [BE-0159](../BE-0159-flatten-roadmap-status-folders/BE-0159-flatten-roadmap-status-folders.md) |
| Superseded by | [BE-0159](../BE-0159-flatten-roadmap-status-folders/BE-0159-flatten-roadmap-status-folders.md) |
<!-- /BE-METADATA -->

## Introduction

> **Deferred — premise retired by [BE-0159](../BE-0159-flatten-roadmap-status-folders/BE-0159-flatten-roadmap-status-folders.md).**
> This item hardens `roadmap-promote.yml`, which BE-0159 deleted along with
> `scripts/promote_roadmap_items.py` (the flat layout leaves nothing to promote). The
> `contents: write`, PR-influenced workflow this item guards no longer exists, and a review of the
> remaining automation found nothing to re-target: the roadmap automation that still holds
> `contents: write` (`roadmap-id.yml`, `roadmap-drift-check.yml`) is triggered by `push` to `main`
> and runs its script from the trusted `main` checkout, while `auto-merge.yml` checks out no
> repository code at all — so the PR-head-script-under-`contents: write` pattern this item guards
> against no longer occurs anywhere. The item is therefore deferred and superseded by BE-0159,
> which designed the hazard out rather than hardening it in place. Kept for the historical record.

`roadmap-promote.yml` checks out and runs a script from the PR's own head ref while holding
`contents: write`. This proposal moves the executed script to the trusted base SHA so a PR
cannot influence the code that runs with that permission.

## Motivation

`.github/workflows/roadmap-promote.yml` triggers on `pull_request` with:

    permissions:
      contents: write

It checks out `ref: ${{ github.head_ref }}` (the PR branch itself), and then runs
`python3 scripts/promote_roadmap_items.py` from that checkout before committing and pushing back
to the same branch. Because the script that executes comes from the PR head, a PR that edits
`scripts/promote_roadmap_items.py` controls the code the workflow runs with write access to the
repository. The workflow does have a fork guard (`if: github.event.pull_request.head.repo.full_name == github.repository`), which keeps external fork
PRs from reaching this step at all — so today the risk is scoped to a same-repository branch (an
internal contributor or an already-compromised account), not an arbitrary external contributor.

Severity is Low given the fork guard, but the pattern — running a PR-head script under
`contents: write` — is the general shape CI hardening otherwise avoids elsewhere in this repo
(e.g. the pinned-by-SHA third-party actions), so it is worth closing even though the guard
already limits who can reach it.

## Detailed design

1. **Check out the base branch ref (`github.base_ref`) / base SHA (`github.event.pull_request.base.sha`), not `github.head_ref`,** for
   the step that runs `scripts/promote_roadmap_items.py`. The script's job — moving a proposal's
   directory to match its `Status:` field and regenerating the index — reads the PR's changed
   roadmap files from the working tree, so the checkout still needs the PR's content; the fix is
   to run the *script itself* from the trusted base commit while operating on the merged/head
   tree, e.g. via `actions/checkout` with `ref: ${{ github.event.pull_request.base.sha }}`
   followed by a merge of the head, or by fetching the base-ref copy of the script file only and
   invoking that copy against the head checkout.
2. **Keep the existing fork guard** as defense-in-depth — the base-SHA fix removes the head-script
   trust issue outright, so the guard becomes a second, redundant layer rather than the only one.
3. **Add a regression note in the workflow's comments** (matching the existing inline rationale
   comments in `roadmap-promote.yml`) explaining why the script is sourced from the base ref, so a
   future edit doesn't silently reintroduce head-ref execution.

## Alternatives considered

- **Rely solely on the fork guard.** Rejected as the status quo being hardened against: the guard
  protects against external forks but not a same-repo branch running an edited script, which is
  exactly the gap this proposal closes.
- **Drop `contents: write` and require a maintainer to run `make roadmap-promote` manually.**
  Rejected: it reintroduces the manual step BE-0078/BE-0089 automated away, trading a
  low-severity, already-guarded risk for a recurring manual chore on every roadmap PR.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Source `scripts/promote_roadmap_items.py` from the base SHA rather than the PR head when
      the workflow runs it.
- [ ] Keep the fork guard as defense-in-depth and document the base-SHA rationale inline.

No PR has landed yet.

## References

`.github/workflows/roadmap-promote.yml`. Related: BE-0069 (executable contributor guardrails),
BE-0089 (merge-time BE-id allocation), BE-0078 (roadmap status folders). Originates from the
2026-07-02 codebase-analysis report (security).
