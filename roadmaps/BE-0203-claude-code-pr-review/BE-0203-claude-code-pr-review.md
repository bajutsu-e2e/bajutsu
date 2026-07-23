**English** · [日本語](BE-0203-claude-code-pr-review-ja.md)

# BE-0203 — Claude Code as the automated PR code reviewer

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0203](BE-0203-claude-code-pr-review.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0203") |
| Implementing PR | [#807](https://github.com/bajutsu-e2e/bajutsu/pull/807), [#915](https://github.com/bajutsu-e2e/bajutsu/pull/915), [#916](https://github.com/bajutsu-e2e/bajutsu/pull/916), [#1160](https://github.com/bajutsu-e2e/bajutsu/pull/1160) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

Every pull request to this repository is reviewed automatically by **GitHub Copilot** today —
it posts inline comments when a PR opens and re-reviews on each push. This item replaces that
reviewer with **Claude Code**, running from a GitHub Actions workflow on every PR: auto-triggered
on open and on each push, posting **inline line-level comments** via the action's native
inline-comment tool — including GitHub *suggested-change* blocks. (It initially also posted a
top-level summary; that was later dropped — see the Progress log — because a fresh summary on every
re-run left stale, contradictory overviews on the PR.) The gain over Copilot is that Claude Code
reviews against **this repository's own contract** — the three [prime
directives](../../CLAUDE.md#prime-directives-do-not-violate), the docstring standard, the
bilingual-docs rule, the BE-ID lifecycle — which a generic reviewer cannot know.

The one hard boundary this item never crosses is **prime directive 1**: the automated review is
**advisory**. It posts comments a human weighs, exactly as any bot comment; it is **never a
required status check** and never sits on the `run` / CI **verdict**. The deterministic `check`
and `E2E` gates remain the only arbiters of whether a PR can merge — this item adds a reviewer,
not a judge.

## Motivation

- **The user wants Claude Code, not Copilot, as the PR reviewer.** This is the direct request:
  move the automated-review surface from Copilot to Claude Code while keeping the experience a
  contributor already relies on (auto-review on open, re-review on push, inline comments).
- **A repo-aware reviewer catches what a generic one cannot.** Copilot reviews against general
  code-quality heuristics; it has no knowledge of Bajutsu's prime directives, so it cannot flag
  the mistakes this project cares about most — an LLM call creeping onto the `run` / CI verdict
  path, a fixed `sleep` instead of a condition wait, an ambiguous selector that "taps whatever
  matched first", a per-app difference hardcoded instead of living in `targets.<name>` config, a
  documented behavior changed on only one language side, or a roadmap PR that doesn't link its BE
  item. Claude Code reviewing with a repo-authored prompt pulls the same way the runner does.
- **The building blocks already exist; this item wires them to the PR.** Claude Code's built-in
  `code-review` skill already produces findings and, with `--comment`,
  **posts them as inline PR comments**. [`implement-be`](../../.claude/skills/implement-be/SKILL.md)
  already codifies the review lenses this repo trusts (silent-failure → *fail loudly*,
  type-design under strict `mypy`, test-coverage of new logic). Today those run only **author-side,
  inside a session before the PR exists** — they never reach a human-authored PR, an externally
  authored one, or a re-push. This item takes the same review and runs it *on the PR*, where a
  reviewer is expected.
- **The AI provider is already vendor-neutral.** [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md)
  gave the Tier-1 AI paths a vendor-neutral backend, with [BE-0053](../BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider.md)
  (Amazon Bedrock) and [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md)
  (OAuth) as alternatives to the direct Anthropic application programming interface (API). The
  review workflow authenticates through the same provider choice rather than hardcoding one
  vendor, so no new credential model is introduced.

## Detailed design

The work is a new advisory Actions workflow plus its review prompt, a documented migration off
Copilot, and the bilingual docs — no change to the deterministic gate, the drivers, or the
runner.

1. **The review workflow** — a new `.github/workflows/claude-review.yml`. It triggers on
   `pull_request` with `types: [opened, synchronize, reopened]` (auto-review on open, re-review on every push,
   matching Copilot), runs the official Anthropic `claude-code-action` (pinned to a full commit
   SHA, like every other action in this repo), and invokes the built-in **`/code-review
   --comment`** so the findings land as **inline PR comments**. Permissions are the minimum the
   surface needs: `pull-requests: write` (post review comments) and `contents: read` (read the
   diff) — and nothing else. It carries a `concurrency` block (`group: claude-review-${{ github.ref }}`,
   `cancel-in-progress: true`), mirroring `ci.yml`, so a rapid push sequence supersedes a stale
   review instead of stacking several.

2. **Advisory, never a gate — the prime-directive guardrail.** Two properties keep the review off
   the verdict path, and both are explicit:
   - **Not a required status check.** The job's `name:` is *not* added to `main`'s
     branch-protection `required_status_checks` (the ruleset that pins `check` / `E2E` / `require
     two approvals for BE proposals`). A PR merges on the deterministic gates alone; the review
     never blocks it.
   - **The workflow's own result is decoupled from the findings.** A review that *found issues* is
     a successful review, not a failed check — the step exits `0` whether or not it posted
     comments. The only way the job goes red is an infrastructure failure (the action itself
     erred), never "the reviewer disliked the code". This is what makes it a comment surface and
     not a smuggled LLM verdict.

3. **Feature parity with Copilot.** Each Copilot review capability maps to a concrete piece here:

   | Copilot capability | This item |
   |---|---|
   | Auto-review when a PR opens | `pull_request` with `types: [opened]` |
   | Re-review on each new push | `pull_request` with `types: [synchronize]` |
   | Inline line-level comments | `/code-review --comment` (posts inline PR comments) |
   | Suggested changes (one-click apply) | the review prompt asks for a GitHub ```` ```suggestion ```` block wherever a concrete, mechanical fix fits |
   | A summary of the review | shipped as a top-level summary comment alongside the inline notes, then later dropped (see the Progress log) — a fresh summary on every re-run left stale, contradictory overviews |
   | On-demand re-review | an opt-in `@claude review` mention path (below) *in addition to* the auto-trigger |

4. **On-demand re-review (opt-in, additive).** Beyond the auto-trigger, an `issue_comment` /
   `pull_request_review_comment` trigger lets a contributor write `@claude review` (or reply to a
   thread) to request a fresh pass or a follow-up on a specific comment — the same affordance the
   [PR-review-comment reply rule](../../docs/ai-development.md#responding-to-pr-review-comments)
   already assumes for AI reviewers. This is purely additive to item 1; the auto-review is the
   default and needs no mention.

5. **The repo-flavored review prompt.** A committed prompt (e.g. `.github/claude-review-prompt.md`,
   or the `claude_args` the action passes to `/code-review`) points the review at *this
   repository's* contract, so it catches what a generic reviewer misses:
   - the three **prime directives** — flag any LLM call reaching the `run` / CI verdict, any fixed
     `sleep` where a condition wait belongs, any ambiguous-selector "tap the first match", any
     per-app knob hardcoded outside `targets.<name>` config;
   - the **review lenses** `implement-be` already trusts — swallowed errors / weak fallbacks
     (determinism = fail loudly), type invariants under strict `mypy`, whether new logic is
     actually covered by a test;
   - the **house conventions** the gate can't judge — bilingual docs updated on both language
     sides, the [docstring standard](../../docs/ai-development.md#code-documentation-comments-docstrings--be-0065),
     a roadmap PR that links its BE item both ways, `## Progress` kept current.

   The prompt stays a review *aid*: it shapes what the reviewer looks at, never what merges.

6. **Credential scope and fork safety.** The workflow authenticates through the provider
   [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md) already
   selects (`ANTHROPIC_API_KEY`, the OAuth token, or Amazon Web Services (AWS) credentials for
   Bedrock), stored as an Actions secret scoped via an Environment so an unrelated job can't read
   it. Because a plain `pull_request` event from a **fork** does not expose secrets (by GitHub's
   design), auto-review covers same-repo `claude/<topic>` / `<user>/<topic>` branches — the model
   [CLAUDE.md](../../CLAUDE.md) already assumes — and a fork PR is reviewed on demand by a
   maintainer instead. This item deliberately does **not** use `pull_request_target` (which would
   run with secrets against untrusted fork code); that trade-off is recorded under *Alternatives*.

7. **Migration off Copilot — parallel, then switch (a documented manual cut-over).** The cut-over
   is staged so quality is proven before Copilot is retired:
   - **Phase A — parallel.** Land this workflow with Copilot review still on. Both reviewers
     comment on every PR; neither gates.
   - **Phase B — compare.** Over a handful of PRs, judge Claude Code's review against Copilot's —
     signal, noise, false positives, whether the repo-aware checks earn their keep.
   - **Phase C — switch.** A maintainer **disables Copilot's automatic review in the repository /
     organization settings**. This is **out-of-repo admin state a normal PR cannot carry** — the
     same shape as the branch-protection ruleset edits BE-0122 and BE-0089 already call out — so it
     is an explicit, documented manual step, not something this item's diff can perform. The docs
     (item 8) record it so the cut-over isn't half-done (both reviewers left on forever).

8. **Document the reviewer (bilingual).** Update the *Responding to PR review comments* section of
   [`docs/ai-development.md`](../../docs/ai-development.md) — which already names "GitHub Copilot
   and other AI reviewers" — to name **Claude Code** as *the* automated reviewer, and add a short
   subsection describing the automated-review workflow: that it is advisory (never a required
   check), what it comments on, the `@claude review` on-demand path, the fork limitation, and the
   manual Copilot-disable step from item 7. Mirror it in [`docs/ja/ai-development.md`](../../docs/ja/ai-development.md)
   under the [`japanese-tech-writing`](../../.claude/skills/japanese-tech-writing/) skill.

9. **Verification.** `actionlint` (already in `make check`) validates the new workflow's YAML, so
   the gate stays green with no new automated test to add — the review *behavior* can't be
   unit-tested, since it needs a live PR and a provider call. Verification is therefore the same
   manual shape BE-0122 used: open a test PR, confirm the review auto-posts inline comments and a
   summary on open, re-posts on a follow-up push, that a ```` ```suggestion ```` block renders as a
   one-click apply, and that the review's check is **not** listed among the required checks (it
   cannot block the merge).

## Alternatives considered

- **Keep Copilot (do nothing).** Rejected: it's the explicit request to move to Claude Code, and
  Copilot structurally cannot review against the prime directives — the mistakes this repo most
  wants caught (an LLM on the verdict path, a fixed `sleep`, a one-language-only doc change) are
  invisible to a reviewer that doesn't know the contract.
- **Make the Claude review a required status check.** Rejected outright — it violates prime
  directive 1 by putting an LLM on the merge verdict. The review must stay advisory; the
  deterministic `check` / `E2E` gates remain the only arbiters. This is the single boundary the
  whole item is shaped around.
- **Leave review author-side only (the existing `implement-be` step 7).** Rejected as
  insufficient: the in-session `simplify` / `code-review` / pr-review-toolkit pass runs *before*
  the PR exists and only for an agent-driven change. It never reaches a human-authored PR, an
  externally-authored one, or a re-push — exactly the cases a PR reviewer exists for. This item
  complements that author-side pass rather than replacing it.
- **`pull_request_target` so fork PRs are auto-reviewed too.** Rejected (deferred): it runs the
  workflow with repository secrets in the context of untrusted fork code, a well-known
  token-exfiltration risk not worth it for a repo whose contribution model is same-repo topic
  branches. Fork PRs get on-demand maintainer-triggered review instead; revisit only if external
  fork contributions become common.
- **Hand-roll a reviewer (call the provider API from a bespoke script).** Rejected: the official
  `claude-code-action` plus the repo's own `/code-review --comment` skill already do this;
  a bespoke script duplicates the skill and drifts from it. Reuse the skill so the CI reviewer and
  the author-side reviewer stay one implementation.
- **A single summary comment instead of inline comments.** Rejected as a downgrade from Copilot:
  inline line-level comments (and suggestion blocks) are the affordance contributors rely on, and
  `code-review --comment` already supports them, so there's no reason to ship less.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add the advisory review workflow `.github/workflows/claude-review.yml` (auto-trigger, inline
      comments via the action's native tool against the repo contract, minimal permissions,
      concurrency) (item 1)
- [x] Enforce the advisory guardrail — not a required check, result decoupled from findings (item 2)
- [x] Reach Copilot parity — inline comments, suggestion blocks, summary (item 3) — the summary was
      later dropped (see the log below), leaving inline comments and suggestion blocks
- [x] Add the opt-in `@claude review` on-demand path (item 4)
- [x] Commit the repo-flavored review prompt (prime directives, lenses, house conventions) (item 5)
- [x] Scope the credential via an Environment; document the fork limitation (item 6)
- [x] Document the manual Copilot-disable step for the parallel-then-switch migration (item 7,
      docs) — executing the migration (parallel → compare → switch) is a post-merge operational task
- [x] Document the reviewer in `docs/ai-development.md` (EN + JA) (item 8)
- [x] Verify on a live PR (item 9) — done on #807 with the subscription (OAuth) provider: the
      auto-review posts inline comments on push and the `claude review` check is advisory
      (non-required). The review behavior needs a live PR and a provider call, so it stays out of the
      deterministic gate (the BE-0122 shape)

Log:

- Proposal authored.
- Shipped the advisory review workflow, the repo-flavored review prompt, and the bilingual docs
  (items 1–8). Authenticates via a Claude Code subscription (OAuth) or Amazon Bedrock via GitHub OIDC,
  staying a green no-op until a provider credential is provisioned (`CLAUDE_CODE_OAUTH_TOKEN` or
  `AWS_BEDROCK_ROLE_ARN` + `BEDROCK_MODEL_ID`). Item 7's migration execution (disabling Copilot in
  repo/org settings) is a post-merge operational task. Implementing PR:
  [#807](https://github.com/bajutsu-e2e/bajutsu/pull/807).
- Added the Claude Code subscription (OAuth) provider alongside Bedrock (item 6): the workflow
  prefers the `CLAUDE_CODE_OAUTH_TOKEN` secret when set, else Bedrock via OIDC, else stays a green
  no-op. Exactly one provider is active per run.
- Hardened after review (#807): the on-demand comment path now requires the comment to contain
  `@claude review` *and* a trusted actor (OWNER/MEMBER/COLLABORATOR), closing a fork-PR secret-exposure gap;
  the Bedrock gate now requires both `AWS_BEDROCK_ROLE_ARN` and `BEDROCK_MODEL_ID` (a half-configured
  Environment stays a no-op instead of failing red); the docs now state the dormant-until-configured
  behavior.
- Live verification (item 9) surfaced a mechanism mismatch and fixed it: the built-in
  `/code-review --comment` skill posts through unrestricted `gh` (Bash), which `claude-code-action`
  sandboxes out (the first live run authenticated and reviewed fine but posted nothing —
  21 permission denials, "No buffered inline comments"). The workflow now posts inline findings via
  the action's native `mcp__github_inline_comment__create_inline_comment` tool and the summary via a
  narrowly-scoped `gh pr comment`, with a matching `claude_args --allowedTools` allowlist (the same
  shape as the action's own review workflow). The repo-flavored *contract*
  (`.github/claude-review-prompt.md`) is unchanged and still reused — only the posting mechanism
  moved off the gh-based skill. Verified on a live run: OAuth (subscription) auth succeeds and the
  review posts.
- Fixed a concurrency flaw the live runs exposed: with one shared group and `cancel-in-progress:
  true`, a comment posted mid-review (a bot reply, a reviewer note) cancelled the running auto-review
  and surfaced a red "cancelled" check. The group is now split by event type and only `pull_request`
  events cancel-in-progress, so pushes still supersede each other while comment-triggered runs stay
  independent.
- More review hardening: corrected the on-demand checkout `ref` (only `issue_comment` needs
  `refs/pull/N/head`; the other events carry a top-level `pull_request.head.sha`), and made the
  review self-identify as Claude Code in its comments (it posts under `github-actions[bot]`).
- Redesigned the checkout to clear the CodeQL "untrusted checkout in a privileged context" / TOCTOU
  alerts: the privileged job now checks out the **default branch** — the canonical, trusted home of
  the review contract — with `persist-credentials: false` and reviews the change set with
  `gh pr diff`, instead of checking out the untrusted PR head. So the review contract
  (`.github/claude-review-prompt.md`) is always read from the default branch (same for every PR,
  and it resolves on comment events too), and a PR cannot rewrite the rules it is reviewed under.
- Fixed a gap the automation-bot PRs exposed: `claude-code-action` rejects any non-human (Bot/App)
  actor unless it is allow-listed, so PRs opened by our own `bajutsu-automation-bot` (roadmap-refresh,
  docs-refresh, etc.) failed the review with "Workflow initiated by non-human actor" instead of being
  reviewed. The workflow now sets `allowed_bots: "bajutsu-automation-bot"` — allow-listing exactly that
  trusted internal bot rather than `'*'` (which the action warns can let external Apps invoke it with
  attacker-controlled prompts on public repos); the match is case-insensitive and strips a trailing
  `[bot]`, so it covers both actor spellings. Implementing PR:
  [#915](https://github.com/bajutsu-e2e/bajutsu/pull/915).
- Broadened the review prompt's own lenses (item 5): added a security lens for semantic /
  data-flow vulnerabilities the gate's pattern-level `ruff` checks can't follow, a strengthened
  design-and-technical-debt lens, a discussion-awareness lens (read `gh pr view --comments` first;
  never repeat a point another reviewer already raised), a requirement that every actionable
  finding name a concrete change, and a Japanese-prose-quality convention tied to the
  `japanese-tech-writing` skill. Implementing PR:
  [#916](https://github.com/bajutsu-e2e/bajutsu/pull/916).
- Dropped the top-level summary comment: the review now posts inline findings only. Because the job
  re-runs on every push, a fresh summary each time piled up stale, contradictory overviews on the PR.
  The workflow prompt and the `.github/claude-review-prompt.md` contract no longer ask for a summary,
  and `Bash(gh pr comment:*)` is removed from the action's `--allowedTools` so withholding the tool
  enforces the rule. The same change steers the reviewer to raise every finding in one exhaustive pass
  rather than dribbling new findings across re-runs (and to leave unchanged pre-existing lines settled
  by omission), cutting the fix-and-wait round-trips an author pays. Implementing PR:
  [#1160](https://github.com/bajutsu-e2e/bajutsu/pull/1160).

## References

- Claude Code's built-in `code-review` skill — the author-side review pass this item complements.
  It is a built-in skill, not defined under [`.claude/skills`](../../.claude/skills) (which holds
  only this repo's own skills); the repo's [`implement-be`](../../.claude/skills/implement-be/SKILL.md)
  already uses it author-side (its review step, with the lenses and pr-review-toolkit). The CI
  workflow does **not** invoke that skill directly — it posts inline findings via the action's
  native `mcp__github_inline_comment__create_inline_comment` tool against the same repo contract.
- [`docs/ai-development.md`](../../docs/ai-development.md) — the *Responding to PR review comments*
  rules (already naming AI reviewers) this item updates, and the required-status-check /
  admin-state constraints it mirrors.
- [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) — the deterministic gate that stays
  the only merge arbiter; the `concurrency` shape this workflow mirrors.
- [`CLAUDE.md`](../../CLAUDE.md) — the three prime directives the review prompt (item 5) encodes.
- [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md),
  [BE-0053](../BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider.md),
  [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md) — the
  vendor-neutral AI provider the workflow authenticates through.
- [BE-0122](../BE-0122-workflow-name-legibility/BE-0122-workflow-name-legibility.md),
  [BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation.md) — prior
  items whose out-of-repo admin-state pattern (ruleset / settings edits a PR can't carry) the
  Copilot-disable step (item 7) follows.
