**English** · [日本語](BE-0222-daily-doc-freshness-pr-ja.md)

# BE-0222 — Scheduled daily workflows that refresh the roadmap and docs separately, each opening its own review PR

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0222](BE-0222-daily-doc-freshness-pr.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0222") |
| Implementing PR | [#882](https://github.com/bajutsu-e2e/bajutsu/pull/882) |
| Topic | Contributor workflow |
| Related | [BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review.md) |
<!-- /BE-METADATA -->

## Introduction

Add **two** scheduled (daily) GitHub Actions workflows that reconcile the human-maintained parts of
the repository with what has actually shipped, and — when they find drift — each opens its **own**
**Draft** pull request for a human to review and merge:

- a **roadmap-refresh** workflow that keeps BE items (`Status` / `Progress` / `Implementing PR`)
  current, and
- a **docs-refresh** workflow that keeps the prose docs (`docs/` and `DESIGN.md`) in step with
  behavior.

The two share one design (the credential shape, the contract-file pattern, the in-job gate, the
rolling Draft PR) but run and ship independently. Both use the Claude Code action — the same AI
provider the advisory review already uses ([BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review.md))
— to *author* the updates; a human is the only one who merges. No LLM ever touches the `run`/CI
verdict, and each generated PR is still gated by the deterministic `make check` like any other.

## Motivation

The repository already keeps the *mechanically derivable* parts of its documents honest: `make
roadmap-index` regenerates the index and `make test` fails if the committed tables drift; [BE-0096](../BE-0096-docs-roadmap-link-integrity/BE-0096-docs-roadmap-link-integrity.md)
makes a broken `docs/` → roadmap link a gate failure; `roadmap-drift-check` ([BE-0149](../BE-0149-roadmap-placeholder-format-guardrail/BE-0149-roadmap-placeholder-format-guardrail.md))
re-checks open PRs against the template. What none of these can cover is the content that requires
*semantic judgment* to keep current, and which therefore rots silently:

- **BE item `Status` / `Progress` / `Implementing PR`.** The working agreement says a PR that
  advances an item ticks its `Progress` boxes and fills `Implementing PR` in the same change, and
  that `Status` is flipped when work starts or ships. In practice a PR sometimes lands code without
  updating the item, so an item stays `Proposal` after its code merged, or its `Progress` checklist
  lags the real state. Nothing catches this — it is prose-and-state reconciliation, not a format
  check.
- **`docs/architecture.md#implementation-status`** — the stated source of truth for "what already
  exists". It only stays accurate if a contributor remembers to update it when a capability lands.
- **`DESIGN.md` / `docs/architecture.md` in step with behavior ([BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md)).**
  That rule is deliberately a review-time norm, not a CI gate, precisely because checking that a
  paragraph of prose still matches the code needs semantic judgment — which would put an LLM on the
  `run`/CI verdict path if it were a gate (prime directive 1). So it is enforced by attention, and
  attention lapses.

The common thread: these gaps are exactly the ones a deterministic gate *cannot* close without
violating prime directive 1, and so they accumulate between the moments a human happens to notice.
A scheduled agent that drafts the reconciliation — and leaves the merge decision to a human — closes
the latency gap without crossing that line. It is the authoring counterpart to the advisory reviewer:
BE-0203 added an AI *reviewer* that never gates a merge; this adds an AI *author* that never merges.

**Why two workflows, not one.** The two kinds of drift are different work and are best reviewed
apart. Roadmap reconciliation is small and near-mechanical — flip a `Status`, tick `Progress`, add
an `Implementing PR` row, regenerate the index — and a reviewer can confirm it against the merged PRs
in a minute. Docs reconciliation is heavier: rewriting prose to match behavior across two languages,
where the reviewer must actually read the paragraphs. Bundling both into one PR forces the fast,
low-risk roadmap fixes to wait behind the slow, careful docs review (and vice versa), and mixes two
diffs a reviewer would rather judge separately. Splitting them also isolates failure and noise: a
day when only the docs drift produces one focused docs PR, not a combined PR whose roadmap half is
empty, and a problem in one refresh never blocks the other. They can even run on different cadences
later (e.g. roadmap daily, docs weekly) without disturbing each other.

## Detailed design

Two scheduled workflows — `.github/workflows/roadmap-refresh.yml` and
`.github/workflows/docs-refresh.yml` — built on one **shared shape**, differing only in *what* each
reconciles and *which* files each may touch. Every AI-authored change flows through a human-reviewed,
gate-checked PR; nothing lands on `main` directly.

**Keep the shared shape shared in code, not just in this doc.** The commonality below is a design
commitment, but two independent YAML files can drift apart as each is edited (a later tweak to the
two-credential gate landing in one and not the other). The implementation must therefore factor the
shared shape — the dormancy gate, concurrency/timeout, in-job `make check`, the rolling-branch clobber
guard, and PR open/update — into a single reusable unit (a `workflow_call` reusable workflow, or a
composite action) that both thin workflow files call with only their differing parameters (branch,
contract path, allowlist). That way the two stay in lockstep by construction rather than by
remembering to edit both identically.

### The shared shape (both workflows)

1. **Triggers, concurrency, and timeout.** `schedule` (a daily cron) and `workflow_dispatch`
   (on-demand). Give the two workflows slightly offset cron times so they never contend for a runner.
   Each job carries a `concurrency` group (`cancel-in-progress: false`) so a manual dispatch never
   races that workflow's own nightly run, and a `timeout-minutes` fail-fast cap so a hung provider
   call is killed rather than tying up a runner — both following `claude-review.yml`'s
   `concurrency:` / `timeout-minutes:` precedent.

2. **Two credentials, all-or-nothing dormancy.** Each job needs two independent credentials and must
   be a green no-op unless *both* are present — combining the single-credential dormancy gate
   `roadmap-id.yml` already uses for its App token with the one `claude-review.yml` already uses for
   its AI-provider credential (neither precedent gates on both; this proposal is the first to require
   the pair), so a half-provisioned repo never goes red:
   - **An AI provider**, selected exactly as `claude-review.yml` does (BE-0203, [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md)):
     the Claude Code subscription (`CLAUDE_CODE_OAUTH_TOKEN`) when present, else Amazon Bedrock via
     OIDC (`AWS_BEDROCK_ROLE_ARN` + `BEDROCK_MODEL_ID`), else dormant. This authors the updates.
   - **The automation App token** (`AUTOMATION_BOT_APP_ID` / `AUTOMATION_BOT_PRIVATE_KEY`, via
     `actions/create-github-app-token`), as `roadmap-id.yml` and `roadmap-drift-check.yml` use it. A
     PR opened with the default `GITHUB_TOKEN` does **not** trigger other workflows, so the refresh
     PR's own `check` CI would never run — the App's installation token carries no such restriction.
     Opening the PR under the App identity is what makes the deterministic gate actually run on the
     AI's output.

3. **A tracked contract file** (`.github/*-refresh-prompt.md`, mirroring `.github/claude-review-prompt.md`)
   is the authoritative instruction the action runs. Each workflow has its own, and each states a
   hard path allowlist enforced on the diff before the PR is opened, a **conservatism rule** (propose
   an update only where there is concrete evidence of drift — a merged PR, a shipped capability — and
   when uncertain leave the file unchanged and note the uncertainty in the PR body rather than
   guessing), and the bilingual rule where it applies (any Japanese follows the `japanese-tech-writing`
   skill and the ですます調 register; English/Japanese are updated together).

4. **In-job `make check` before opening the PR.** After the agent edits files, the workflow runs the
   deterministic gate in-job, holding the AI's output to the same bar as any human change. A red gate
   does not block the PR (it opens as **Draft** regardless), but the result is surfaced in the PR
   body. The PR is *also* subject to the normal `check` CI (that is why the App token is required).

5. **One rolling, idempotent, always-Draft, human-merged PR per workflow.** If the reconciliation
   produces no diff, the job exits without opening or touching a PR (no daily noise on a quiet week).
   Otherwise it pushes to that workflow's fixed branch and reuses the existing open PR if there is one,
   rather than opening a new PR every day. The PR opens with `--draft`; the human reviews, and *only*
   the human marks it ready and merges. There is no auto-merge on either branch.

   **Never clobber a human's work on the rolling branch.** The daily run rewrites the branch only
   when its current tip is a commit the automation itself last authored (the App/bot identity). If a
   human has pushed onto the branch since the last run — a reviewer's fixup commit, an edit made
   mid-review — the run does **not** force-update over it: it leaves the branch untouched and surfaces
   that it skipped (a `::warning::` in the job, noted on the PR) so the human's work is never
   overwritten and the skip is loud rather than silent. This mirrors the guard in
   `scripts/check_stale_roadmap_prs.py` (`open_or_update_fix_pr`), which deliberately bases its branch
   on the exact SHA a fix was computed from rather than the live tip, to avoid clobbering a newer
   commit with content computed from a stale snapshot (its comment at lines 177–180). A fresh branch
   is created only when no rolling branch exists.

### What differs

| | roadmap-refresh | docs-refresh |
|---|---|---|
| Branch / PR | `chore/roadmap-refresh` | `chore/docs-refresh` |
| Contract | `.github/roadmap-refresh-prompt.md` | `.github/docs-refresh-prompt.md` |
| Reconciles | BE items' `Status` / `Progress` / `Implementing PR` against merged PRs; runs `make roadmap-index` when it changes an index-affecting field | `docs/architecture.md#implementation-status`; `DESIGN.md` / `docs/architecture.md` prose vs behavior (BE-0113) |
| Path allowlist | `roadmaps/**` only | `docs/**` and `DESIGN.md` only |

Neither workflow ever edits product code (`bajutsu/`, `BajutsuKit/`, tests, config, demos) — the
allowlist forbids it, mirroring the ideation skill's "authoring only, never implement" boundary.

The docs-refresh allowlist deliberately **excludes** the top-level `README*` and `CLAUDE.md`. Those
are the project's contract surface, not behavior-tracking prose — `CLAUDE.md` in particular states the
prime directives the AI author is itself bound by, so letting the agent auto-draft edits to its own
operating contract is a boundary we don't want to cross even behind a Draft PR. Edits there stay
human-authored; the workflow's remit is `docs/` and `DESIGN.md`, exactly the prose that drifts against
behavior. (The deterministic `docs/` → roadmap link repair for `README*` / `CLAUDE.md` is a separate,
LLM-free concern already owned by [BE-0096](../BE-0096-docs-roadmap-link-integrity/BE-0096-docs-roadmap-link-integrity.md).)

### Prime-directive compliance

The LLM is used purely on the *authoring* path — it drafts updates, exactly like `record` drafts
scenarios — and its output is a Draft PR that a human must review and merge. No LLM call is added to
`run` or to any `required_status_checks`; the deterministic `check` remains the sole merge arbiter,
and the human is the sole judge. Each workflow's path allowlist keeps it out of drivers, the runner,
and tests, so it cannot affect determinism or the app-agnostic core.

## Alternatives considered

- **One combined workflow that refreshes roadmap and docs in a single PR.** The original shape of
  this proposal. Rejected in favor of splitting: it forces the fast, near-mechanical roadmap fixes to
  wait behind the slow, careful docs review, mixes two diffs a reviewer would rather judge apart, and
  couples the two failure modes and cadences (see *Why two workflows*). The shared design lives in one
  place here (this item), so the duplication cost of two workflows is small.
- **Deterministic-only refresh (no LLM).** Regenerate indexes, re-check links, refresh tracking-issue
  URLs on a schedule. Rejected as the *whole* answer: those parts are already covered by `make check`
  / `make roadmap-index` / BE-0096, and they are not what rots. The drift this item targets —
  `Status`/`Progress` lag, prose vs behavior — is inherently semantic and cannot be reconciled without
  judgment.
- **Commit the refresh straight to `main`.** Rejected: it removes the human from the loop (prime
  directive 1 — the human is the judge here) and bypasses review of AI-authored prose. Draft PRs are
  the whole point.
- **Extend the advisory reviewer (BE-0203) instead of new workflows.** Different shape: BE-0203
  *reviews* an existing PR and posts comments; these *author* PRs from scratch on a schedule. Reusing
  its provider-selection logic is the right amount of sharing; merging the jobs would overload one
  workflow with unrelated triggers and scopes.
- **On-demand only (`workflow_dispatch`, no cron).** Kept as an additional trigger, but the value is
  the unattended daily cadence — "always current" — which an on-demand-only button does not deliver.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `roadmap-refresh.yml` — daily workflow reconciling BE `Status` / `Progress` / `Implementing PR`, its own contract, branch, and rolling Draft PR
- [x] `docs-refresh.yml` — daily workflow reconciling `docs/` / `DESIGN.md` prose vs behavior, its own contract, branch, and rolling Draft PR
- [x] Shared shape wired into both: two-credential dormancy gate, concurrency + timeout, in-job `make check` surfaced in the PR body, idempotent no-diff exit
- [x] Shared shape factored into a reusable `workflow_call` workflow (or composite action) both thin workflow files call, so they can't drift apart
- [x] Rolling-branch clobber guard: force-update only over the automation's own tip; skip loudly if a human pushed onto the branch
- [x] Path allowlists enforced per workflow (roadmap-only; docs-only, excluding `README*` / `CLAUDE.md`) so neither touches product code or the contract surface

### Log

- [#882](https://github.com/bajutsu-e2e/bajutsu/pull/882): shipped in one PR — the reusable `refresh.yml` (shared shape — two-credential dormancy gate,
  concurrency/timeout, App-token checkout, AI-authoring step, in-job `make check`), the two thin
  callers `roadmap-refresh.yml` / `docs-refresh.yml`, their contracts
  (`.github/{roadmap,docs}-refresh-prompt.md`), and the deterministic `scripts/refresh_pr.py`
  (path-allowlist enforcement, idempotent no-diff exit, rolling-branch clobber guard, always-Draft
  human-merged PR) with `tests/test_refresh_pr.py`.

## References

`.github/workflows/claude-review.yml` (BE-0203 — AI provider selection + Claude Code action usage,
the pattern the shared shape reuses); `.github/workflows/roadmap-id.yml` (BE-0089) and
`.github/workflows/roadmap-drift-check.yml` ([BE-0149](../BE-0149-roadmap-placeholder-format-guardrail/BE-0149-roadmap-placeholder-format-guardrail.md))
(the automation App token that lets a bot-opened PR trigger its own `check` CI);
[BE-0096](../BE-0096-docs-roadmap-link-integrity/BE-0096-docs-roadmap-link-integrity.md) (the
deterministic link-integrity guard this complements); [BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md)
(keep DESIGN.md / architecture.md in step with behavior — the review-time norm this automates a nudge
for); [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend.md)
(vendor-neutral AI provider); the `japanese-tech-writing` skill (the register the Japanese output
follows).
