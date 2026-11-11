**English** · [日本語](BE-0154-roadmap-promote-base-sha-ja.md)

# BE-0154 — Run roadmap-promote from the base SHA

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0154](BE-0154-roadmap-promote-base-sha.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Rejected** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0154") |
| Topic | Security hardening |
| Superseded by | [BE-0159](../BE-0159-flatten-roadmap-status-folders/BE-0159-flatten-roadmap-status-folders.md) |
<!-- /BE-METADATA -->

## Introduction

> **Deferred — premise retired by [BE-0159](../BE-0159-flatten-roadmap-status-folders/BE-0159-flatten-roadmap-status-folders.md).**
> This item hardens `roadmap-promote.yml`. BE-0159 deleted that workflow along with
> `scripts/promote_roadmap_items.py`, because the flat layout leaves nothing to promote. The
> `contents: write`, PR-influenced workflow this item guards no longer exists. A review of the
> remaining automation found nothing to re-target:
>
> - The roadmap automation that still holds `contents: write` (`roadmap-id.yml`,
>   `roadmap-drift-check.yml`) triggers on `push` to `main`. It runs its script from the trusted
>   `main` checkout.
> - `auto-merge.yml` checks out no repository code at all.
>
> The pattern this item guards against, a PR-head script running under `contents: write`, no
> longer occurs anywhere. BE-0159 designed the hazard out instead of hardening it in place. This
> item is deferred and superseded by BE-0159. It stays for the historical record.

`roadmap-promote.yml` checks out and runs a script from the PR's own head ref while holding
`contents: write`. This proposal moves the executed script to the trusted base SHA. A PR then
cannot influence the code that runs with that permission.

## Motivation

`.github/workflows/roadmap-promote.yml` triggers on `pull_request` with:

    permissions:
      contents: write

The workflow checks out `ref: ${{ github.head_ref }}` (the PR branch itself). It runs
`python3 scripts/promote_roadmap_items.py` from that checkout, then commits and pushes back to the
same branch. The executing script comes from the PR head. A PR that edits
`scripts/promote_roadmap_items.py` therefore controls the code the workflow runs with write access
to the repository.

The workflow does have a fork guard
(`if: github.event.pull_request.head.repo.full_name == github.repository`). The guard keeps
external fork PRs from reaching this step at all. Today the risk is scoped to a same-repository
branch (an internal contributor or an already-compromised account). An arbitrary external
contributor cannot reach it.

Severity is Low given the fork guard. But running a PR-head script under `contents: write` is the
general shape that CI hardening avoids elsewhere in this repo (e.g. the pinned-by-SHA third-party
actions). The guard already limits who can reach the step, and closing the gap is still worth it.

## Detailed design

1. **Check out the base branch ref (`github.base_ref`) / base SHA
   (`github.event.pull_request.base.sha`), not `github.head_ref`,** for the step that runs
   `scripts/promote_roadmap_items.py`. The script moves a proposal's directory to match its
   `Status:` field and regenerates the index. It reads the PR's changed roadmap files from the
   working tree, so the checkout still needs the PR's content. The fix is to run the *script
   itself* from the trusted base commit while operating on the merged/head tree. Two ways to do
   that:
   - Use `actions/checkout` with `ref: ${{ github.event.pull_request.base.sha }}`, then merge the
     head.
   - Fetch the base-ref copy of the script file alone, and invoke that copy against the head
     checkout.
2. **Keep the existing fork guard** as defense-in-depth. The base-SHA fix removes the head-script
   trust issue outright, so the guard becomes a second, redundant layer instead of the sole one.
3. **Add a regression note in the workflow's comments**, matching the existing inline rationale
   comments in `roadmap-promote.yml`. The note explains why the script is sourced from the base
   ref, so a future edit does not silently reintroduce head-ref execution.

## Alternatives considered

- **Rely on the fork guard alone.** Rejected as the status quo being hardened against. The guard
  protects against external forks. It does not stop a same-repo branch from running an edited
  script, which is exactly the gap this proposal closes.
- **Drop `contents: write` and require a maintainer to run `make roadmap-promote` manually.**
  Rejected. It reintroduces the manual step that BE-0078 and BE-0089 automated away. It trades a
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
