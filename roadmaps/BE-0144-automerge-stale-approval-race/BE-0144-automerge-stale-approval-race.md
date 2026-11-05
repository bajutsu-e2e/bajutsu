**English** · [日本語](BE-0144-automerge-stale-approval-race-ja.md)

# BE-0144 — Close the auto-merge stale-approval race

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0144](BE-0144-automerge-stale-approval-race.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0144") |
| Implementing PR | [#691](https://github.com/bajutsu-e2e/bajutsu/pull/691) |
| Topic | Security hardening |
<!-- /BE-METADATA -->

## Introduction

The codebase-analysis report flags a possible auto-merge stale-approval race. A PR could merge on an
approval given before a later, unreviewed commit. Whether the race is possible depends on the exact
configuration of the `main` branch ruleset, and the report did not confirm that configuration.

This finding is **unconfirmed**. The first task is to check the ruleset, before deciding whether a
fix is needed at all.

## Motivation

The report describes the following race:

1. A reviewer approves a PR.
2. The author pushes a further commit.
3. The platform does not dismiss the earlier approval.
4. An auto-merge lands the unreviewed commit under the strength of the stale approval. The
   auto-merge could be BE-0061's atomic claim-refs automation or GitHub's native auto-merge.

Whether the race can happen in this repository depends entirely on the review requirements of the
`main` branch protection ruleset. No bajutsu-specific code is involved. This proposal exists to
record that dependency and pin down the answer. It does not assert that the race is real.

A read of the repository's "Require code review" ruleset (`pull_request` rule) shows
`dismiss_stale_reviews_on_push: true` and `require_last_push_approval: true` already configured.
Suppose this ruleset governs `main`, and these fields behave as GitHub documents. The race would
then already be closed. Any new commit after approval invalidates prior approvals, and a merge
additionally requires an approval on the exact last pushed commit.

This proposal does not treat that reading as the final word. The actual first step has two parts:

- Confirm the configuration against the live ruleset, not a point-in-time read.
- Confirm how BE-0061's automation and native auto-merge interact with dismissed approvals.

## Detailed design

1. **Confirm the live configuration of the `main` ruleset.** Use `gh api
   repos/{owner}/{repo}/rulesets` or the Settings → Rules UI. Check the `pull_request` rule's
   `dismiss_stale_reviews_on_push` and `require_last_push_approval` fields. Check them on the
   ruleset that actually governs the branch protection enforced on `main` merges.
2. **If both fields are already enabled and cover every merge path, close this proposal as already
   mitigated by existing configuration.** The merge paths include BE-0061's automated claim+push
   re-allocation flow and GitHub's native auto-merge. No code or workflow change is needed, merely
   a documented confirmation.
3. **If either field is missing, or a merge path bypasses them, enable and enforce both fields on
   the ruleset covering `main`.** An example of a bypass is an automation-driven push that
   re-triggers merge without re-evaluating the ruleset. After the change, re-verify that BE-0061's
   automation still functions under the constraint. Its claim-ref push flow re-pushes to the PR
   branch, which is exactly the kind of push that a stale-approval dismissal policy applies to.

## Alternatives considered

- **Assume the race is real and ship a fix without confirming the ruleset first.** Rejected. The
  report explicitly flags the finding as needs-confirmation, and the ruleset read already suggests
  that the relevant settings exist. A fix for an already-mitigated issue would be wasted work. It
  also risks conflicting with BE-0061's existing automation.
- **Ignore the finding since severity is unclear.** Rejected. Even a small chance of an unreviewed
  commit merging under a stale approval is worth a one-time confirmation. The confirmation is cheap
  relative to the risk.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Confirm the live `main` ruleset's `dismiss_stale_reviews_on_push` and
      `require_last_push_approval` settings.
- [x] Confirm these settings apply to every merge path, including BE-0061's automation and native
      auto-merge.
- [x] ~~If a gap is found, enable/enforce the missing setting(s) and re-verify BE-0061's
      automation.~~ — N/A (no gap found).
- [x] If no gap is found, close this proposal recording the confirmation (no code change).

### Confirmation log

**2026-07-05** — queried the live ruleset via `gh api repos/bajutsu-e2e/bajutsu/rulesets/17735477`.

**Ruleset "Require code review" (ID 17735477)**

| Setting | Value |
|---|---|
| Enforcement | active |
| Scope | `~DEFAULT_BRANCH` (main) |
| `dismiss_stale_reviews_on_push` | **true** |
| `require_last_push_approval` | **true** |
| `required_approving_review_count` | 1 |
| `required_review_thread_resolution` | true |

**Merge-path analysis**

1. **Human merge** — the ruleset applies directly. Any push after approval dismisses the approval,
   and merge requires approval on the last pushed commit. Race closed.
2. **Native auto-merge** (`auto-merge.yml`) — `bajutsu-automation-bot` (Integration 4178537)
   enables it. That App is a bypass actor (`bypass_mode: always`) on this ruleset. Despite the
   bypass status, PRs #677 and #678 (both merged by the App via auto-merge) show that auto-merge
   waited for the review approval before merging:
   - PR #677: approved 04:49:50, merged 04:49:51.
   - PR #678: approved 04:20:53, merged 04:21:04.

   `gh pr merge --auto` voluntarily waits for all branch-protection requirements, regardless of the
   actor's bypass privileges. The bypass path is a direct `gh pr merge` (without `--auto`), and the
   automation uses it nowhere. Race closed.
3. **roadmap-id.yml** (BE-0089) — a post-merge push directly to `main` that allocates BE IDs. It is
   not a PR merge path, so it is irrelevant to the stale-approval race.

**Verdict**: the live configuration already mitigates the stale-approval race described in the
codebase-analysis report. No code, workflow, or ruleset change is needed.

## References

- GitHub repository ruleset "Require code review" (`pull_request` rule:
  `dismiss_stale_reviews_on_push`, `require_last_push_approval`)
- Related: BE-0089 (merge-time BE-id allocation), BE-0061 (BE-id allocation hardening), BE-0069
  (executable contributor guardrails)
- Originates from the 2026-07-02 codebase-analysis report (security).
