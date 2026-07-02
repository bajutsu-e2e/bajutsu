**English** · [日本語](BE-XXXX-automerge-stale-approval-race-ja.md)

# BE-XXXX — Close the auto-merge stale-approval race

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-automerge-stale-approval-race.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Topic | Security hardening |
<!-- /BE-METADATA -->

## Introduction

The codebase-analysis report flags a possible auto-merge stale-approval race — a PR could
merge on an approval given before a later, unreviewed commit — but whether this is actually
possible depends on the `main` branch ruleset's exact configuration, which the report did not
confirm. This finding is **unconfirmed**; the first task is to check the ruleset before deciding
whether a fix is needed at all.

## Motivation

The race the report describes is: a reviewer approves a PR, the author pushes a further commit,
and if the platform doesn't dismiss the earlier approval, an auto-merge (BE-0061's atomic
claim-refs automation, or GitHub's native auto-merge) could land the unreviewed commit under the
strength of a stale approval. Whether this can happen on this repo depends entirely on the
`main` branch protection ruleset's review requirements, not on any bajutsu-specific code — this
proposal exists to record that dependency and pin down the answer, not to assert the race is
real.

A read of the repository's "Require code review" ruleset (`pull_request` rule) shows
`dismiss_stale_reviews_on_push: true` and `require_last_push_approval: true` already configured,
which — if this is in fact the ruleset governing `main` and these fields behave as GitHub
documents — would already close the race: any new commit after approval invalidates prior
approvals, and merge additionally requires an approval on the exact last pushed commit. But this
proposal does not treat that reading as the final word: confirming it against the live ruleset
(not a point-in-time read) and against how BE-0061's automation and native auto-merge interact
with dismissed approvals is the actual first step.

## Detailed design

1. **Confirm the `main` ruleset's live configuration** — via `gh api
   repos/{owner}/{repo}/rulesets` (or the Settings → Rules UI) — for the `pull_request` rule's
   `dismiss_stale_reviews_on_push` and `require_last_push_approval` fields, on the ruleset that
   actually governs the branch protection enforced on `main` merges.
2. **If both are already enabled and cover every merge path** (including BE-0061's automated
   claim+push re-allocation flow and GitHub's native auto-merge), close this proposal as already
   mitigated by existing configuration — no code or workflow change needed, just a documented
   confirmation.
3. **If either is missing or a merge path bypasses them** (e.g. an automation-driven push that
   re-triggers merge without re-evaluating the ruleset), enable/enforce
   `dismiss_stale_reviews_on_push` and `require_last_push_approval` on the ruleset covering
   `main`, and re-verify BE-0061's automation still functions under that constraint (its
   claim-ref push flow re-pushes to the PR branch, which is exactly the kind of push a
   stale-approval dismissal policy would apply to).

## Alternatives considered

- **Assume the race is real and ship a fix without confirming the ruleset first.** Rejected: the
  report explicitly flags this as needs-confirmation, and the ruleset read already suggests the
  relevant settings exist — building a fix for an already-mitigated issue would be wasted work
  and risks conflicting with BE-0061's existing automation.
- **Ignore the finding since severity is unclear.** Rejected: even a small chance of an
  unreviewed commit merging under a stale approval is worth a one-time confirmation, given how
  cheap that confirmation is relative to the risk.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Confirm the live `main` ruleset's `dismiss_stale_reviews_on_push` and
      `require_last_push_approval` settings.
- [ ] Confirm these settings apply to every merge path, including BE-0061's automation and native
      auto-merge.
- [ ] If a gap is found, enable/enforce the missing setting(s) and re-verify BE-0061's automation.
- [ ] If no gap is found, close this proposal recording the confirmation (no code change).

No PR has landed yet.

## References

GitHub repository ruleset "Require code review" (`pull_request` rule:
`dismiss_stale_reviews_on_push`, `require_last_push_approval`). Related: BE-0089 (merge-time
BE-id allocation), BE-0061 (BE-id allocation hardening), BE-0069 (executable contributor
guardrails). Originates from the 2026-07-02 codebase-analysis report (security).
